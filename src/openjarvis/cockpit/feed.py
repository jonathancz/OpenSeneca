#!/usr/bin/env python3
"""Cockpit feeder: firstmate's records in, a read only JSON folder out.

Runs on the host as the captain's user, never inside Docker: the container
cannot see tmux, ``~/.claude`` or the firstmate home, and must not. Only the
Python standard library is used, so ``python3 feed.py`` works without the
OpenJarvis environment (see ``scripts/cockpit-feed/``).

What it reads, all read only:

* ``<firstmate>/state/<id>.meta``, ``.status`` and ``.busy-state``: one live
  or recent crew task each.
* ``<firstmate>/data/backlog.md``: task titles, the queue, captain holds and
  finished work.
* ``<firstmate>/data/<id>/brief.md`` (the captain's intent only) and whether
  ``report.md`` exists.
* ``bin/fm-fleet-snapshot.sh --json`` when it succeeds. It is preferred for
  each task's current state and open decisions; today it fails on large
  backlogs, so the direct records are always the fallback.
* Claude Code transcripts under ``~/.claude/projects/<worktree slug>/``.
* ``git status`` and ``git diff --numstat`` in each ship task's worktree, with
  optional locks off so not even the index is refreshed.

What it writes, atomically, into the output folder only (never inside the
firstmate home): ``fleet.json``, ``transcripts/<id>.json`` and
``changes/<id>.json``. The salt behind opaque client ids lives outside that
folder, so the container that mounts it cannot read it.

Privacy: client projects (belit, VIEW, alpine by default) are reduced to the
project name and the task's state: no title, no status text, no branch, no
PR, no transcript, no changes, and an opaque id. Transcripts are trimmed to
messages, tool names and a one line summary per tool call; tool output and
file contents are never read into the output. Anything that looks like a
token or key is redacted from every piece of text.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

SCHEMA = "seneca-cockpit-fleet.v1"
TRANSCRIPT_SCHEMA = "seneca-cockpit-transcript.v1"
TERMINAL_SCHEMA = "seneca-cockpit-terminal.v1"
CHANGES_SCHEMA = "seneca-cockpit-changes.v1"

# Display names for firstmate project folders. Unknown projects keep their
# folder name.
DEFAULT_SPACE_NAMES = {
    "openseneca": "Seneca",
    "arbo": "NextBranch",
    "oposiciones-study": "NextPlaza",
    "firstmate": "firstmate",
}

# Client work: only the project name and the task's state may leave the host.
DEFAULT_CLIENT_PATTERNS = (r"belit", r"view", r"alpine", r"alp-", r"alp\d", r"atlas")

STATUS_VERBS = {
    "working",
    "needs-decision",
    "blocked",
    "paused",
    "done",
    "failed",
    "resolved",
    "captain-held",
}

WORKING_FRESH_SECONDS = 3 * 60 * 60
FINISHED_WINDOW_DAYS = 7
HOLD_AGED_DAYS = 14
SUMMARY_CAP = 240
QUEUED_MAX = 60
TRANSCRIPT_MAX_ENTRIES = 200
TEXT_CAP = 2000
TOOL_SUMMARY_CAP = 100
BRIEF_CAP = 1600
TRANSCRIPT_FULL_READ_BYTES = 8 * 1024 * 1024
TRANSCRIPT_HEAD_BYTES = 1 * 1024 * 1024
TRANSCRIPT_TAIL_BYTES = 6 * 1024 * 1024
CHANGES_MAX_FILES = 200
GIT_TIMEOUT = 10
CHANGES_MAX_AGE = 5 * 60

# Commands this feeder may run, and nothing else. A test asserts it.
ALLOWED_COMMANDS = (
    ("git", "rev-parse"),
    ("git", "merge-base"),
    ("git", "diff"),
    ("git", "status"),
    ("bin/fm-fleet-snapshot.sh", "--json"),
    ("tmux", "capture-pane"),
    ("gh", "pr view"),
)

# ---------------------------------------------------------------------------
# Text hygiene
# ---------------------------------------------------------------------------

_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"\b(?:sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|gho_[A-Za-z0-9]{20,}"
            r"|github_pat_[A-Za-z0-9_]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}"
            r"|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}|re_[A-Za-z0-9]{20,}"
            r"|glpat-[A-Za-z0-9_-]{16,})"
        ),
        "[redacted]",
    ),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}"), "Bearer [redacted]"),
    (
        re.compile(
            r"(?i)\b([A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|API_?KEY|ACCESS_KEY)"
            r"[A-Z0-9_]*)(\s*[=:]\s*)(?!\[redacted\])[^\s'\"]{6,}"
        ),
        r"\1\2[redacted]",
    ),
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*-----"),
        "[redacted]",
    ),
)
_LONG_TOKEN = re.compile(r"[A-Za-z0-9+/_=-]{40,}")


def _looks_secret(token: str) -> bool:
    has_upper = any(c.isupper() for c in token)
    has_lower = any(c.islower() for c in token)
    has_digit = any(c.isdigit() for c in token)
    return has_upper and has_lower and has_digit


def scrub(text: str) -> str:
    """Redact anything that looks like a credential."""
    if not text:
        return text
    for pattern, repl in _SECRET_PATTERNS:
        text = pattern.sub(repl, text)
    return _LONG_TOKEN.sub(
        lambda m: "[redacted]" if _looks_secret(m.group(0)) else m.group(0), text
    )


def clip(text: str, cap: int) -> str:
    text = text.strip()
    if len(text) <= cap:
        return text
    return text[: cap - 1].rstrip() + "…"


def one_line(text: str, cap: int) -> str:
    return clip(" ".join(text.split()), cap)


def clean(text: str, cap: int) -> str:
    return clip(scrub(text), cap)


_ANY_URL = re.compile(r"\(?https?://\S+")


def plain(text: str | None, cap: int) -> str | None:
    """One line of plain words: no links (PRs travel as links on their own),
    no secrets. None when nothing meaningful is left."""
    if not text:
        return None
    out = one_line(_ANY_URL.sub("", scrub(text)), cap)
    out = re.sub(r"\s+([,.;:)])", r"\1", out).strip(" -,;:")
    return out if len(out) > 3 else None


# ---------------------------------------------------------------------------
# firstmate records
# ---------------------------------------------------------------------------


def read_text(path: Path, limit: int | None = None) -> str:
    try:
        with open(path, "rb") as fh:
            data = fh.read() if limit is None else fh.read(limit)
    except OSError:
        return ""
    return data.decode("utf-8", errors="replace")


def mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def parse_meta(text: str) -> dict[str, str]:
    meta: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and key and not key.startswith("#"):
            meta[key.strip()] = value.strip()
    return meta


def spawn_epoch(meta: dict[str, str]) -> int | None:
    for key in ("spawn_gen", "busy_gen"):
        m = re.match(r"^[sg](\d{9,})", meta.get(key, ""))
        if m:
            return int(m.group(1))
    return None


@dataclass
class StatusEvent:
    verb: str
    note: str
    key: str | None = None
    at: int | None = None
    line_no: int = 0


_TAG = re.compile(r"^\s*\[([a-z-]+)=([^\]]*)\]")


def parse_status_line(line: str, line_no: int = 0) -> StatusEvent | None:
    """Parse one status log line, or return None for continuation prose.

    Accepts ``verb [at=..] [key=..]: note`` and ``verb: [key=..] note``. A
    line with neither a colon after its tags nor a key tag is not a
    transition, matching firstmate's own fold.
    """
    m = re.match(r"^([a-z][a-z-]*)(.*)$", line.rstrip("\n"))
    if not m or m.group(1) not in STATUS_VERBS:
        return None
    verb, rest = m.group(1), m.group(2)
    tags: dict[str, str] = {}
    while True:
        t = _TAG.match(rest)
        if not t:
            break
        tags[t.group(1)] = t.group(2)
        rest = rest[t.end() :]
    rest_stripped = rest.lstrip()
    if rest_stripped.startswith(":"):
        note = rest_stripped[1:].strip()
    elif "key" in tags:
        note = rest_stripped.strip()
    else:
        return None
    while True:
        t = _TAG.match(note)
        if not t:
            break
        tags.setdefault(t.group(1), t.group(2))
        note = note[t.end() :].strip()
    at: int | None = None
    if tags.get("at", "").isdigit():
        at = int(tags["at"])
    key = tags.get("key") or None
    return StatusEvent(verb=verb, note=note, key=key, at=at, line_no=line_no)


def parse_status_log(text: str) -> list[StatusEvent]:
    events = []
    for i, line in enumerate(text.splitlines()):
        ev = parse_status_line(line, i)
        if ev is not None:
            events.append(ev)
    return events


def fold_open_decisions(events: Iterable[StatusEvent], kind: str) -> list[StatusEvent]:
    """firstmate's open decision fold, mirrored.

    ``needs-decision`` and ``blocked`` open their key (``default`` without
    one); ``resolved`` and ``captain-held`` close it; for ship and scout tasks
    ``done`` and ``failed`` close everything.
    """
    open_: dict[str, StatusEvent] = {}
    for ev in events:
        if ev.verb in ("done", "failed") and kind in ("ship", "scout"):
            open_.clear()
        elif ev.verb in ("needs-decision", "blocked"):
            k = ev.key or "default"
            open_.pop(k, None)
            open_[k] = ev
        elif ev.verb in ("resolved", "captain-held"):
            open_.pop(ev.key or "default", None)
    return list(open_.values())


def parse_busy_state(text: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for part in text.split():
        k, sep, v = part.partition("=")
        if sep:
            out[k] = v
    ts = out.get("ts", "")
    out["ts"] = int(ts) if str(ts).isdigit() else None
    return out


# ---------------------------------------------------------------------------
# Backlog
# ---------------------------------------------------------------------------


@dataclass
class BacklogRow:
    id: str
    section: str
    checked: bool
    title: str
    repo: str | None = None
    kind: str | None = None
    since: str | None = None
    done: str | None = None
    hold_kind: str | None = None
    hold_reason: str | None = None
    hold_until: str | None = None
    blocked_by: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)


_ROW = re.compile(r"^- \[( |x)\] ([A-Za-z0-9._-]+) - (.*)$")
_PR_URL = re.compile(
    r"https://(?:github\.com/[\w.-]+/[\w.-]+/pull/\d+"
    r"|bitbucket\.org/[\w.-]+/[\w.-]+/pull-requests/\d+)"
)


def _paren_field(rest: str, name: str) -> str | None:
    m = re.search(r"\(" + re.escape(name) + r":? ([^()]*)\)", rest)
    return m.group(1).strip() if m else None


def _hold_reason(rest: str) -> str | None:
    """The ``(hold: ...)`` text, which may itself hold parentheses."""
    start = rest.find("(hold: ")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(rest)):
        if rest[i] == "(":
            depth += 1
        elif rest[i] == ")":
            depth -= 1
            if depth == 0:
                return rest[start + len("(hold: ") : i].strip()
    return rest[start + len("(hold: ") :].strip()


def parse_backlog(text: str) -> list[BacklogRow]:
    rows: list[BacklogRow] = []
    section = ""
    for line in text.splitlines():
        if line.startswith("## "):
            section = line[3:].strip().lower()
            continue
        m = _ROW.match(line)
        if not m:
            continue
        rest = m.group(3)
        cut = len(rest)
        for marker in (" (repo:", " (kind:", " (since ", " (done ", " (merged "):
            idx = rest.find(marker)
            if 0 <= idx < cut:
                cut = idx
        title = rest[:cut].strip()
        blocked_by: list[str] = []
        bm = re.search(r"\s+blocked-by:\s*([A-Za-z0-9._,\s-]+)$", title)
        if bm:
            blocked_by = [b for b in re.split(r"[\s,]+", bm.group(1)) if b]
            title = title[: bm.start()].strip()
        rows.append(
            BacklogRow(
                id=m.group(2),
                section=section,
                checked=m.group(1) == "x",
                title=title,
                repo=_paren_field(rest, "repo"),
                kind=_paren_field(rest, "kind"),
                since=_paren_field(rest, "since"),
                done=_paren_field(rest, "done") or _paren_field(rest, "merged"),
                hold_kind=_paren_field(rest, "hold-kind"),
                hold_reason=_hold_reason(rest),
                hold_until=_paren_field(rest, "hold-until"),
                blocked_by=blocked_by,
                links=_PR_URL.findall(rest),
            )
        )
    return rows


def _date_epoch(date: str | None, tz: _dt.tzinfo | None) -> int | None:
    if not date:
        return None
    try:
        d = _dt.datetime.strptime(date[:10], "%Y-%m-%d")
    except ValueError:
        return None
    return int(d.replace(tzinfo=tz).timestamp())


def hold_bucket(
    row: BacklogRow, done_ids: set[str], now: float, tz: _dt.tzinfo | None
) -> str | None:
    """firstmate's captain hold bucket: blocked, dated, aged or live."""
    if row.hold_kind != "captain" or row.checked or row.section == "done":
        return None
    if any(b not in done_ids for b in row.blocked_by):
        return "blocked"
    until = _date_epoch(row.hold_until, tz)
    if until is not None and until > now:
        return "dated"
    since = _date_epoch(row.since, tz)
    if since is not None and now - since >= HOLD_AGED_DAYS * 86400:
        return "aged"
    return "live"


# ---------------------------------------------------------------------------
# Privacy
# ---------------------------------------------------------------------------


class Privacy:
    def __init__(self, patterns: Iterable[str], salt: bytes) -> None:
        self._re = (
            re.compile(r"^(?:" + "|".join(patterns) + r")", re.IGNORECASE)
            if patterns
            else None
        )
        self._salt = salt

    def is_client(self, *names: str | None) -> bool:
        if self._re is None:
            return False
        return any(n and self._re.match(n) for n in names)

    def opaque_id(self, task_id: str) -> str:
        digest = hmac.new(self._salt, task_id.encode(), hashlib.sha256).hexdigest()
        return "client-" + digest[:10]


def default_salt_file() -> Path:
    return Path.home() / ".local" / "state" / "openseneca" / "cockpit.salt"


def load_salt(path: Path, legacy_out_dir: Path | None = None) -> bytes:
    """The key behind opaque client ids, kept outside the mounted folder.

    A salt left in the output folder by an earlier version is moved here once,
    so client ids stay stable and the container can no longer read it.
    """
    legacy = legacy_out_dir / ".salt" if legacy_out_dir is not None else None
    try:
        data = path.read_bytes()
        if len(data) >= 16:
            if legacy is not None and legacy.exists():
                legacy.unlink()
            return data
    except OSError:
        pass
    data = b""
    if legacy is not None:
        try:
            data = legacy.read_bytes()
        except OSError:
            data = b""
    if len(data) < 16:
        data = os.urandom(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_bytes(path, data)
    os.chmod(path, 0o600)
    if legacy is not None and legacy.exists():
        legacy.unlink()
    return data


# ---------------------------------------------------------------------------
# Transcripts
# ---------------------------------------------------------------------------


def project_slug(path: str) -> str:
    """Claude Code's folder name for a working directory."""
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def _iso_epoch(value: Any) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return _dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


_REMINDER = re.compile(r"<system-reminder>[\s\S]*?</system-reminder>")
_SKIP_USER = re.compile(
    r"^\s*<(?:command-|local-command|bash-|task-notification|user-prompt-submit)"
)


def _user_text(content: Any) -> str:
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        text = "\n".join(parts)
    else:
        return ""
    text = _REMINDER.sub("", text).strip()
    if not text or _SKIP_USER.match(text):
        return ""
    return text


def tool_summary(name: str, tool_input: Any) -> str:
    """One line describing a tool call, never its output or file contents."""
    inp = tool_input if isinstance(tool_input, dict) else {}
    summary = ""
    if name == "Bash":
        summary = str(inp.get("description") or "shell command")
    elif name in ("Read", "Edit", "Write", "MultiEdit", "NotebookEdit"):
        path = str(inp.get("file_path") or inp.get("notebook_path") or "")
        summary = os.path.basename(path.rstrip("/"))
    elif name in ("Grep", "Glob"):
        summary = str(inp.get("pattern") or "")
    elif name in ("Agent", "Task"):
        summary = str(inp.get("description") or "")
    elif name == "WebFetch":
        m = re.match(r"https?://([^/]+)", str(inp.get("url") or ""))
        summary = m.group(1) if m else ""
    elif name == "WebSearch":
        summary = str(inp.get("query") or "")
    elif name == "Skill":
        summary = str(inp.get("skill") or "")
    return one_line(scrub(summary), TOOL_SUMMARY_CAP)


def _iter_transcript_lines(path: Path) -> Iterable[str]:
    try:
        size = path.stat().st_size
        with open(path, "rb") as fh:
            if size <= TRANSCRIPT_FULL_READ_BYTES:
                chunks = [fh.read()]
            else:
                head = fh.read(TRANSCRIPT_HEAD_BYTES)
                fh.seek(size - TRANSCRIPT_TAIL_BYTES)
                tail = fh.read()
                # Drop the partial first line of the tail.
                tail = tail[tail.find(b"\n") + 1 :]
                head = head[: head.rfind(b"\n") + 1]
                chunks = [head, tail]
    except OSError:
        return
    for chunk in chunks:
        for raw in chunk.splitlines():
            yield raw.decode("utf-8", errors="replace")


# Cheap pre-filter so huge request blobs are never JSON-decoded.
_KIND_HINT = re.compile(r'"type":\s?"(?:user|assistant)"')


def trim_transcript(path: Path) -> list[dict[str, Any]]:
    """Messages, tool names and one line summaries; nothing else."""
    entries: list[dict[str, Any]] = []
    for raw in _iter_transcript_lines(path):
        if not _KIND_HINT.search(raw, 0, 4096):
            continue
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        kind = rec.get("type")
        if kind not in ("user", "assistant") or rec.get("isSidechain"):
            continue
        if rec.get("isMeta") or rec.get("isCompactSummary"):
            continue
        at = _iso_epoch(rec.get("timestamp"))
        msg = rec.get("message") or {}
        content = msg.get("content")
        if kind == "user":
            text = _user_text(content)
            if text:
                entries.append({"t": "user", "at": at, "text": clean(text, TEXT_CAP)})
            continue
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                text = str(block.get("text", "")).strip()
                if text:
                    entries.append(
                        {"t": "agent", "at": at, "text": clean(text, TEXT_CAP)}
                    )
            elif btype == "tool_use":
                name = str(block.get("name", "tool"))
                entries.append(
                    {
                        "t": "tool",
                        "at": at,
                        "name": one_line(name, 60),
                        "summary": tool_summary(name, block.get("input")),
                    }
                )
    return entries


def transcript_start(path: Path) -> float | None:
    """Time of the first timestamped record, read from the file's head."""
    try:
        with open(path, "rb") as fh:
            for _ in range(400):
                raw = fh.readline()
                if not raw:
                    break
                m = re.search(rb'"timestamp":\s?"([^"]+)"', raw)
                if m:
                    return _iso_epoch(m.group(1).decode())
    except OSError:
        return None
    return None


_OWNER_SCAN_BYTES = 3 * 1024 * 1024


def transcript_owner(path: Path, state_dir: str) -> str | None:
    """The task whose firstmate state files this session touches most.

    A crew session reads its brief and appends to ``state/<id>.status``, so
    the id named most often in its head is the task that owns it.
    """
    pattern = re.compile(
        re.escape(state_dir.rstrip("/")).encode()
        + rb"/([A-Za-z0-9._-]+?)\.(?:status|inbox|meta)\b"
    )
    try:
        with open(path, "rb") as fh:
            head = fh.read(_OWNER_SCAN_BYTES)
    except OSError:
        return None
    counts: dict[bytes, int] = {}
    for m in pattern.finditer(head):
        counts[m.group(1)] = counts.get(m.group(1), 0) + 1
    if not counts:
        return None
    best = max(counts.items(), key=lambda kv: kv[1])[0]
    return best.decode(errors="replace")


def _stat_key(path: Path) -> tuple[int, int] | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_size, st.st_mtime_ns)


def _git_dir(worktree: str) -> Path:
    dot = Path(worktree) / ".git"
    if dot.is_file():
        text = read_text(dot, 4096).strip()
        if text.startswith("gitdir:"):
            target = Path(text[len("gitdir:") :].strip())
            return target if target.is_absolute() else Path(worktree) / target
    return dot


def _git_signature(worktree: str) -> tuple:
    gitdir = _git_dir(worktree)
    return tuple(_stat_key(gitdir / name) for name in ("HEAD", "index", "logs/HEAD"))


class FeedCache:
    """Remembers transcript and git reads between rebuilds of one feeder.

    A session file is read again only when its size or mtime changes; a
    worktree's changes are read again when its HEAD, index or reflog change,
    or after :data:`CHANGES_MAX_AGE` for edits that touch none of them.
    Entries not used by the latest rebuild are dropped by :meth:`sweep`.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, tuple[tuple[int, int], dict[str, Any]]] = {}
        self._changes: dict[
            tuple[str, str | None], tuple[tuple, float, dict[str, Any] | None]
        ] = {}
        self._used_sessions: set[str] = set()
        self._used_changes: set[tuple[str, str | None]] = set()
        self._built: dict[str, dict[str, dict[str, Any] | None]] = {}
        self._used_built: set[str] = set()

    def remember(
        self,
        task_id: str,
        transcript: dict[str, Any] | None,
        changes: dict[str, Any] | None,
    ) -> None:
        """Keep what was last built for a task, to reuse once it closes."""
        self._used_built.add(task_id)
        self._built[task_id] = {"transcript": transcript, "changes": changes}

    def kept(self, task_id: str, out_dir: Path | None) -> dict[str, Any]:
        """What was last built for a closed task: from memory, else its files."""
        self._used_built.add(task_id)
        hit = self._built.get(task_id)
        if hit is None:
            hit = {
                "transcript": _read_output(
                    out_dir, "transcripts", task_id, TRANSCRIPT_SCHEMA
                ),
                "changes": _read_output(out_dir, "changes", task_id, CHANGES_SCHEMA),
            }
            self._built[task_id] = hit
        return hit

    def _session(self, path: Path) -> dict[str, Any] | None:
        name = str(path)
        key = _stat_key(path)
        if key is None:
            self._sessions.pop(name, None)
            return None
        self._used_sessions.add(name)
        hit = self._sessions.get(name)
        if hit is not None and hit[0] == key:
            return hit[1]
        memo: dict[str, Any] = {}
        self._sessions[name] = (key, memo)
        return memo

    def start(self, path: Path) -> float | None:
        memo = self._session(path)
        if memo is None:
            return None
        if "start" not in memo:
            memo["start"] = transcript_start(path)
        return memo["start"]

    def owner(self, path: Path, state_dir: str) -> str | None:
        memo = self._session(path)
        if memo is None:
            return None
        slot = f"owner:{state_dir}"
        if slot not in memo:
            memo[slot] = transcript_owner(path, state_dir)
        return memo[slot]

    def entries(self, path: Path) -> list[dict[str, Any]]:
        memo = self._session(path)
        if memo is None:
            return []
        if "entries" not in memo:
            memo["entries"] = trim_transcript(path)
        return memo["entries"]

    def changes(
        self, worktree: str, branch: str | None, now: float
    ) -> dict[str, Any] | None:
        slot = (worktree, branch)
        self._used_changes.add(slot)
        sig = _git_signature(worktree)
        hit = self._changes.get(slot)
        if hit is not None and hit[0] == sig and 0 <= now - hit[1] < CHANGES_MAX_AGE:
            return hit[2]
        result = read_changes(worktree, branch)
        self._changes[slot] = (sig, now, result)
        return result

    def sweep(self) -> None:
        for name in set(self._sessions) - self._used_sessions:
            del self._sessions[name]
        for slot in set(self._changes) - self._used_changes:
            del self._changes[slot]
        for task_id in set(self._built) - self._used_built:
            del self._built[task_id]
        self._used_sessions.clear()
        self._used_changes.clear()
        self._used_built.clear()


def _read_output(
    out_dir: Path | None, sub: str, task_id: str, schema: str
) -> dict[str, Any] | None:
    if out_dir is None or "/" in task_id or task_id.startswith("."):
        return None
    try:
        data = json.loads((out_dir / sub / f"{task_id}.json").read_bytes())
    except (OSError, ValueError):
        return None
    if (
        not isinstance(data, dict)
        or data.get("schema") != schema
        or data.get("task_id") != task_id
    ):
        return None
    return data


def find_transcript(
    projects_dir: Path,
    worktree: str,
    spawned: int | None,
    *,
    task_id: str,
    state_dir: str,
    cache: FeedCache | None = None,
) -> Path | None:
    """Newest Claude Code session for this worktree that belongs to the task.

    Treehouse reuses worktree folders, so a session counts only when it
    started after the task's spawn and names the task's own state files more
    than any other task's.
    """
    if not worktree or spawned is None:
        return None
    cache = cache if cache is not None else FeedCache()
    folder = projects_dir / project_slug(worktree)
    try:
        files = [p for p in folder.glob("*.jsonl") if p.is_file()]
    except OSError:
        return None
    candidates: list[tuple[float, Path]] = []
    for p in files:
        m = mtime(p)
        if m is None or m < spawned - 120:
            continue
        start = cache.start(p)
        if start is None or start < spawned - 120:
            continue
        candidates.append((start, p))
    for _start, p in sorted(candidates, key=lambda c: c[0], reverse=True):
        if cache.owner(p, state_dir) == task_id:
            return p
    return None


# ---------------------------------------------------------------------------
# Git changes (read only, optional locks off)
# ---------------------------------------------------------------------------

_GIT_ENV = {"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}


def _git(worktree: str, *args: str) -> str | None:
    cmd = ["git", "-C", worktree, "--no-optional-locks", *args]
    assert ("git", args[0]) in ALLOWED_COMMANDS, args[0]
    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
            env={**os.environ, **_GIT_ENV},
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return res.stdout if res.returncode == 0 else None


def read_changes(worktree: str, branch: str | None) -> dict[str, Any] | None:
    """Files changed on the task's branch, committed or not, against main."""
    if not worktree or not os.path.isdir(worktree):
        return None
    head = _git(worktree, "rev-parse", "--abbrev-ref", "HEAD")
    if head is None:
        return None
    if branch and head.strip() != branch:
        # The folder now belongs to another task.
        return None
    base = None
    for ref in ("origin/HEAD", "origin/main", "origin/master", "main", "master"):
        out = _git(worktree, "merge-base", "HEAD", ref)
        if out and out.strip():
            base = out.strip()
            break
    files: dict[str, dict[str, Any]] = {}
    if base:
        numstat = _git(worktree, "diff", "--numstat", "-M", base) or ""
        for line in numstat.splitlines():
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            add = int(parts[0]) if parts[0].isdigit() else 0
            rem = int(parts[1]) if parts[1].isdigit() else 0
            path = parts[-1]
            if " => " in path:
                path = re.sub(r"\{[^}]* => ([^}]*)\}", r"\1", path)
                path = path.split(" => ")[-1]
            files[path] = {"path": path, "status": "M", "added": add, "removed": rem}
        names = _git(worktree, "diff", "--name-status", "-M", base) or ""
        for line in names.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2 and parts[-1] in files:
                files[parts[-1]]["status"] = parts[0][:1]
    status = _git(worktree, "status", "--porcelain=v1", "-uall") or ""
    for line in status.splitlines():
        if line.startswith("?? "):
            path = line[3:]
            files.setdefault(
                path, {"path": path, "status": "A", "added": 0, "removed": 0}
            )
    rows = sorted(files.values(), key=lambda r: r["path"])
    return {
        "branch": head.strip(),
        "files": rows[:CHANGES_MAX_FILES],
        "total_files": len(rows),
        "added": sum(r["added"] for r in rows),
        "removed": sum(r["removed"] for r in rows),
    }


# ---------------------------------------------------------------------------
# firstmate's structured snapshot, when it works
# ---------------------------------------------------------------------------


def run_snapshot(fm_home: Path, timeout: float = 120.0) -> dict[str, Any] | None:
    """``fm-fleet-snapshot.sh --json`` with every optional write pointed away.

    Secondmate summaries (its only cache write) are switched off and the cache
    directory is a throwaway temp folder; the forge fallback of the crew state
    reader is off. Returns None on any failure.
    """
    script = fm_home / "bin" / "fm-fleet-snapshot.sh"
    if not script.is_file():
        return None
    with tempfile.TemporaryDirectory(prefix="cockpit-snapshot-") as tmp:
        env = {
            **os.environ,
            "FM_SNAPSHOT_SECONDMATES": "0",
            "FM_SNAPSHOT_CACHE_DIR": tmp,
            "FM_CREW_STATE_NO_FORGE": "1",
        }
        try:
            res = subprocess.run(
                [str(script), "--json"],
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
                cwd=tmp,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
    if res.returncode != 0 or not res.stdout.strip():
        return None
    try:
        data = json.loads(res.stdout)
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("schema") != "fm-fleet-snapshot.v1":
        return None
    return data


def snapshot_task_states(snapshot: dict[str, Any] | None) -> dict[str, dict]:
    """Per task: ``state`` and ``open`` decisions from the snapshot."""
    out: dict[str, dict] = {}
    if not snapshot:
        return out
    for row in snapshot.get("tasks") or []:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        cur = row.get("current_state") or {}
        hints = row.get("hints") or {}
        decisions = []
        for d in hints.get("open_decisions") or []:
            if isinstance(d, dict) and d.get("verb") in ("needs-decision", "blocked"):
                decisions.append(
                    StatusEvent(
                        verb=d["verb"],
                        note=str(d.get("summary") or ""),
                        key=str(d.get("key") or "default"),
                    )
                )
        out[str(row["id"])] = {"state": cur.get("state"), "open": decisions}
    return out


# ---------------------------------------------------------------------------
# Building the fleet
# ---------------------------------------------------------------------------

_SNAPSHOT_STATE = {
    "working": "working",
    "parked": "paused",
    "paused": "paused",
    "done": "done",
    "failed": "failed",
    "blocked": "blocked",
}


# ---------------------------------------------------------------------------
# Merged PRs
# ---------------------------------------------------------------------------

PR_STATE_TTL = 30 * 60
_GITHUB_PR = re.compile(r"^https://github\.com/[\w.-]+/[\w.-]+/pull/\d+$")


def merge_record(state_dir: Path, task_id: str, meta: dict[str, str]) -> float | None:
    """When firstmate recorded this task's PR as merged, or None.

    firstmate's PR poller writes ``state/<id>.pr-poll-merge-notified``
    (first line ``fm-pr-poll-merge-notified-v1``) when it sees the merge; its
    mtime is the time it saw it. A ``merged=`` line in the meta also counts.
    """
    marker = state_dir / f"{task_id}.pr-poll-merge-notified"
    head = read_text(marker, 256).splitlines()
    if head and head[0].strip() == "fm-pr-poll-merge-notified-v1":
        return mtime(marker)
    merged = meta.get("merged", "").strip()
    if merged:
        if merged.isdigit():
            return float(merged)
        return _iso_epoch(merged) or mtime(state_dir / f"{task_id}.meta")
    return None


def gh_pr_state(url: str) -> dict[str, Any] | None:
    """``gh pr view <url> --json state,mergedAt``: GitHub read only."""
    if not _GITHUB_PR.match(url):
        return None
    try:
        res = subprocess.run(
            ["gh", "pr", "view", url, "--json", "state,mergedAt"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            stdin=subprocess.DEVNULL,
            env={**os.environ, "GH_PROMPT_DISABLED": "1", "NO_COLOR": "1"},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if res.returncode != 0:
        return None
    try:
        data = json.loads(res.stdout)
    except ValueError:
        return None
    state = str(data.get("state") or "").upper()
    if state not in ("OPEN", "MERGED", "CLOSED"):
        return None
    return {"state": state, "merged_at": _iso_epoch(data.get("mergedAt"))}


class PrStates:
    """Cached PR states: merged or closed answers forever, the rest 30 minutes."""

    def __init__(self, ttl: float = PR_STATE_TTL, fetch=gh_pr_state) -> None:
        self._ttl = ttl
        self._fetch = fetch
        self._cache: dict[str, tuple[float, bool, dict[str, Any] | None]] = {}

    def state(self, url: str, now: float) -> dict[str, Any] | None:
        hit = self._cache.get(url)
        if hit is not None and (hit[1] or now - hit[0] < self._ttl):
            return hit[2]
        value = self._fetch(url)
        final = value is not None and value["state"] in ("MERGED", "CLOSED")
        self._cache[url] = (now, final, value)
        return value


@dataclass
class FeedConfig:
    fm_home: Path
    out_dir: Path
    space_names: dict[str, str] = field(
        default_factory=lambda: dict(DEFAULT_SPACE_NAMES)
    )
    client_patterns: tuple[str, ...] = DEFAULT_CLIENT_PATTERNS
    tz_name: str | None = None
    # Claude Code transcripts; None leaves conversations out.
    claude_projects: Path | None = None
    # Read git status in ship worktrees for the Changes panel.
    read_git: bool = True
    # Ask GitHub (read only, cached) whether a task's PR merged.
    github: "PrStates | None" = None


def local_tz_name() -> str | None:
    tz = os.environ.get("TZ")
    if tz and "/" in tz:
        return tz.lstrip(":")
    try:
        text = Path("/etc/timezone").read_text().strip()
        if text:
            return text
    except OSError:
        pass
    try:
        target = os.readlink("/etc/localtime")
        m = re.search(r"zoneinfo/(.+)$", target)
        if m:
            return m.group(1)
    except OSError:
        pass
    return None


def _space_of(meta: dict[str, str], row: BacklogRow | None) -> str:
    project = meta.get("project", "")
    if project:
        return os.path.basename(project.rstrip("/")) or "laptop"
    if row and row.repo and row.repo != "-":
        return row.repo
    return "laptop"


def _space_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "laptop"


def _latest_pr(events: list[StatusEvent], row: BacklogRow | None) -> str | None:
    for ev in reversed(events):
        found = _PR_URL.findall(ev.note)
        if found:
            return found[-1]
    if row and row.links:
        return row.links[-1]
    return None


def _pr_number(url: str | None) -> int | None:
    if not url:
        return None
    m = re.search(r"(\d+)$", url)
    return int(m.group(1)) if m else None


def _brief_excerpt(text: str) -> str:
    """The captain's intent, or the Task section, of a crew brief."""
    m = re.search(r"^## Captain's intent\s*\n([\s\S]*?)(?=^#{1,2} |\Z)", text, re.M)
    if not m:
        m = re.search(r"^# Task\s*\n([\s\S]*?)(?=^# |\Z)", text, re.M)
    body = m.group(1) if m else text
    return clean(body, BRIEF_CAP)


@dataclass
class FeedResult:
    fleet: dict[str, Any]
    transcripts: dict[str, dict[str, Any]] = field(default_factory=dict)
    changes: dict[str, dict[str, Any]] = field(default_factory=dict)


def _public(privacy: "Privacy", task_id: str, client: bool) -> str:
    return privacy.opaque_id(task_id) if client else task_id


def build_fleet(
    cfg: FeedConfig,
    *,
    now: float | None = None,
    snapshot: dict[str, Any] | None = None,
    salt: bytes = b"cockpit",
    cache: FeedCache | None = None,
) -> FeedResult:
    now = time.time() if now is None else now
    cache = cache if cache is not None else FeedCache()
    tz_name = cfg.tz_name or local_tz_name()
    tz = _load_tz(tz_name)
    privacy = Privacy(cfg.client_patterns, salt)
    state_dir = cfg.fm_home / "state"
    data_dir = cfg.fm_home / "data"
    backlog = parse_backlog(read_text(data_dir / "backlog.md"))
    rows = {r.id: r for r in backlog}
    done_ids = {r.id for r in backlog if r.checked or r.section == "done"}
    snap = snapshot_task_states(snapshot)

    metas: dict[str, dict[str, str]] = {}
    try:
        meta_paths = sorted(state_dir.glob("*.meta"))
    except OSError:
        meta_paths = []
    for p in meta_paths:
        metas[p.name[: -len(".meta")]] = parse_meta(read_text(p))

    agents: list[dict[str, Any]] = []
    waiting: list[dict[str, Any]] = []
    spaces: dict[str, dict[str, Any]] = {}
    transcripts: dict[str, dict[str, Any]] = {}
    changes: dict[str, dict[str, Any]] = {}
    merged_finished: list[dict[str, Any]] = []

    def space_entry(raw: str) -> str:
        key = _space_key(raw)
        if key not in spaces:
            name = cfg.space_names.get(raw, cfg.space_names.get(key, raw))
            if key == "laptop":
                name = "This laptop"
            spaces[key] = {
                "id": key,
                "name": name,
                "letter": (name[:1] or "?").upper(),
                "client": privacy.is_client(raw),
            }
        return key

    def client_title(space: str) -> str:
        return f"{spaces[space]['name']} task"

    for task_id, meta in metas.items():
        row = rows.get(task_id)
        raw_space = _space_of(meta, row)
        client = privacy.is_client(raw_space, task_id, row.repo if row else None)
        space = space_entry(raw_space)
        kind = meta.get("kind") or (row.kind if row else None) or "ship"
        status_path = state_dir / f"{task_id}.status"
        events = parse_status_log(read_text(status_path))
        status_mtime = mtime(status_path)
        busy = parse_busy_state(read_text(state_dir / f"{task_id}.busy-state", 4096))
        spawned = spawn_epoch(meta)

        open_decisions = fold_open_decisions(events, kind)
        snap_row = snap.get(task_id)
        state_source = "records"
        if snap_row is not None:
            open_decisions = snap_row["open"]
            state_source = "snapshot"

        closed = row is not None and (row.checked or row.section == "done")
        public_id = _public(privacy, task_id, client)
        kept = cache.kept(public_id, cfg.out_dir) if closed and not client else None

        # The agent's own conversation, trimmed (own projects only).
        transcript_path = None
        if not client and not closed and cfg.claude_projects is not None:
            transcript_path = find_transcript(
                cfg.claude_projects,
                meta.get("worktree", ""),
                spawned,
                task_id=task_id,
                state_dir=str(state_dir),
                cache=cache,
            )
        entries: list[dict[str, Any]] = []
        if transcript_path is not None:
            entries = cache.entries(transcript_path)
        elif kept is not None and kept["transcript"] is not None:
            entries = [
                e
                for e in kept["transcript"].get("entries") or []
                if isinstance(e, dict) and e.get("t") in ("user", "agent", "tool")
            ]

        times = [t for t in (status_mtime, busy.get("ts")) if t]
        times += [e["at"] for e in entries[-5:] if e.get("at")]
        last_activity = int(max(times)) if times else spawned

        last_event = events[-1] if events else None
        busy_fresh = (
            busy.get("state") == "busy"
            and last_activity is not None
            and now - last_activity < WORKING_FRESH_SECONDS
        )
        if any(d.verb == "blocked" for d in open_decisions):
            state = "blocked"
        elif open_decisions:
            state = "needs"
        elif snap_row is not None and snap_row["state"] in _SNAPSHOT_STATE:
            state = _SNAPSHOT_STATE[snap_row["state"]]
        elif busy_fresh:
            state = "working"
        elif last_event and last_event.verb in ("done", "paused", "failed"):
            state = last_event.verb
        else:
            state = "quiet"
        # A merged PR finishes the task even when its backlog row was never
        # closed, unless the agent is still at work or asking for something.
        finished_at: float | None = None
        if not closed and state not in ("working", "needs", "blocked"):
            finished_at = merge_record(state_dir, task_id, meta)
            if finished_at is None and not client and cfg.github is not None:
                any_pr = _latest_pr(events, row)
                st = cfg.github.state(any_pr, now) if any_pr else None
                if st is not None and st["state"] == "MERGED":
                    finished_at = st["merged_at"] or now
            if finished_at is not None:
                closed = True
                if now - finished_at > FINISHED_WINDOW_DAYS * 86400:
                    continue
        if closed and state in ("working", "quiet", "paused"):
            state = "done"

        if (
            state == "done"
            and last_activity
            and now - last_activity > (FINISHED_WINDOW_DAYS * 86400)
        ):
            continue

        pr_url = None if client else _latest_pr(events, row)
        report = (data_dir / task_id / "report.md").is_file()
        status_line = None
        status_at = None
        status_verb = None
        if last_event is not None:
            status_verb = last_event.verb
            status_at = last_event.at or (int(status_mtime) if status_mtime else None)
            if not client:
                status_line = plain(last_event.note, SUMMARY_CAP)
        quiet_note = None
        if state == "quiet":
            if busy.get("state") == "busy":
                quiet_note = "busy signal is stale"
            elif not events:
                quiet_note = "no status line yet"
            if not report and kind == "scout":
                quiet_note = (quiet_note + ", " if quiet_note else "") + "no report"

        diff = None
        if (
            not client
            and not closed
            and cfg.read_git
            and meta.get("branch")
            and not meta.get("stale_worktree_reused_by_other_task")
        ):
            ch = cache.changes(meta.get("worktree", ""), meta.get("branch"), now)
            if ch is not None:
                changes[public_id] = {
                    "schema": CHANGES_SCHEMA,
                    "task_id": public_id,
                    "generated_at": int(now),
                    **ch,
                }
                diff = {
                    "files": ch["total_files"],
                    "added": ch["added"],
                    "removed": ch["removed"],
                }
        if kept is not None and kept["changes"] is not None:
            ch = kept["changes"]
            changes[public_id] = ch
            diff = {
                "files": ch.get("total_files", 0),
                "added": ch.get("added", 0),
                "removed": ch.get("removed", 0),
            }

        title = (row.title if row else "") or task_id
        agent = {
            "id": public_id,
            "title": client_title(space) if client else (plain(title, 160) or task_id),
            "space": space,
            "client": client,
            "kind": kind,
            "branch": None if client else meta.get("branch"),
            "window": None if client else meta.get("window"),
            "harness": None if client else meta.get("harness"),
            "model": None if client else meta.get("model"),
            "state": state,
            "state_source": state_source,
            "closed": closed,
            "started_at": spawned,
            "last_activity": last_activity,
            "status_verb": status_verb,
            "status_line": status_line,
            "status_at": status_at,
            "decisions": [
                {
                    "key": None if client else (d.key or "default"),
                    "verb": d.verb,
                    "summary": None if client else plain(d.note, SUMMARY_CAP),
                    "at": d.at,
                }
                for d in open_decisions
            ],
            "pr": {"url": pr_url, "number": _pr_number(pr_url)} if pr_url else None,
            "report": report,
            "quiet_note": quiet_note,
            "diff": diff,
            "has_transcript": bool(entries),
        }
        agents.append(agent)
        if finished_at is not None:
            merged_finished.append(
                {
                    "id": public_id,
                    "title": agent["title"],
                    "space": space,
                    "client": client,
                    "done_on": (
                        _dt.datetime.fromtimestamp(finished_at, tz)
                        if tz
                        else _dt.datetime.fromtimestamp(finished_at)
                    ).strftime("%Y-%m-%d"),
                    "pr": agent["pr"],
                }
            )

        if kept is not None and kept["transcript"] is not None:
            transcripts[public_id] = kept["transcript"]
        elif not client:
            items: list[dict[str, Any]] = []
            brief = read_text(data_dir / task_id / "brief.md", 200_000)
            if brief:
                items.append(
                    {"t": "brief", "at": spawned, "text": _brief_excerpt(brief)}
                )
            items.extend(entries)
            for ev in events:
                if ev.at is None:
                    continue
                items.append(
                    {
                        "t": "event",
                        "at": ev.at,
                        "verb": ev.verb,
                        "key": ev.key,
                        "text": one_line(scrub(ev.note), SUMMARY_CAP),
                    }
                )
            items.sort(key=lambda e: e.get("at") or 0)
            if items:
                transcripts[public_id] = {
                    "schema": TRANSCRIPT_SCHEMA,
                    "task_id": public_id,
                    "generated_at": int(now),
                    "bound": transcript_path is not None,
                    "entries": items[-TRANSCRIPT_MAX_ENTRIES:],
                }
        if not client and not closed:
            cache.remember(
                public_id, transcripts.get(public_id), changes.get(public_id)
            )

        since_fallback = status_at or last_activity
        for d in open_decisions:
            waiting.append(
                {
                    "kind": "blocked" if d.verb == "blocked" else "decision",
                    "agent_id": public_id,
                    "title": agent["title"],
                    "space": space,
                    "client": client,
                    "key": None if client else (d.key or "default"),
                    "summary": None if client else plain(d.note, SUMMARY_CAP),
                    "since": d.at or since_fallback,
                }
            )

    # Captain holds that are live now (firstmate's "Captain's call").
    covered = {w["agent_id"] for w in waiting}
    agent_ids = {a["id"] for a in agents}
    for row in backlog:
        if hold_bucket(row, done_ids, now, tz) != "live":
            continue
        client = privacy.is_client(row.repo, row.id)
        space = space_entry(row.repo if row.repo and row.repo != "-" else "laptop")
        public_id = _public(privacy, row.id, client)
        if public_id in covered:
            continue
        waiting.append(
            {
                "kind": "hold",
                "agent_id": public_id if public_id in agent_ids else None,
                "title": client_title(space)
                if client
                else (plain(row.title, 160) or row.id),
                "space": space,
                "client": client,
                "key": None if client else f"hold-{row.id}",
                "summary": None if client else plain(row.hold_reason, SUMMARY_CAP),
                "since": _date_epoch(row.since, tz),
            }
        )

    # The queue, and in flight rows that have no live agent record.
    backlog_items: list[dict[str, Any]] = []
    for row in backlog:
        if row.checked or row.hold_kind == "captain" or row.kind == "captain":
            continue
        if row.section == "in flight" and row.id in metas:
            continue
        if row.section not in ("queued", "in flight"):
            continue
        client = privacy.is_client(row.repo, row.id)
        space = space_entry(row.repo if row.repo and row.repo != "-" else "laptop")
        blocked = [b for b in row.blocked_by if b not in done_ids]
        backlog_items.append(
            {
                "id": _public(privacy, row.id, client),
                "section": "queued" if row.section == "queued" else "in_flight",
                "title": client_title(space)
                if client
                else (plain(row.title, 160) or row.id),
                "space": space,
                "client": client,
                "kind": row.kind,
                "since": _date_epoch(row.since, tz),
                "waits_on_other_task": bool(blocked),
                "pr": None
                if client or not row.links
                else {"url": row.links[-1], "number": _pr_number(row.links[-1])},
            }
        )
    queued = [b for b in backlog_items if b["section"] == "queued"]
    queued.sort(key=lambda b: b["since"] or 0, reverse=True)
    backlog_items = [b for b in backlog_items if b["section"] != "queued"]
    backlog_items += queued[:QUEUED_MAX]

    finished: list[dict[str, Any]] = []
    for row in backlog:
        if not (row.checked or row.section == "done") or row.kind == "captain":
            continue
        done_at = _date_epoch(row.done, tz)
        if done_at is None or now - done_at > FINISHED_WINDOW_DAYS * 86400:
            continue
        client = privacy.is_client(row.repo, row.id)
        space = space_entry(row.repo if row.repo and row.repo != "-" else "laptop")
        pr = None if client or not row.links else row.links[-1]
        finished.append(
            {
                "id": _public(privacy, row.id, client),
                "title": client_title(space)
                if client
                else (plain(row.title, 160) or row.id),
                "space": space,
                "client": client,
                "done_on": row.done,
                "pr": {"url": pr, "number": _pr_number(pr)} if pr else None,
            }
        )
    seen = {f["id"] for f in finished}
    finished += [f for f in merged_finished if f["id"] not in seen]
    finished.sort(key=lambda f: f["done_on"] or "", reverse=True)

    backend = read_text(cfg.fm_home / "config" / "backend", 64).strip() or None
    today = (
        _dt.datetime.fromtimestamp(now, tz) if tz else _dt.datetime.fromtimestamp(now)
    ).strftime("%Y-%m-%d")
    fleet = {
        "schema": SCHEMA,
        "version": int(now * 1000),
        "generated_at": int(now),
        "today": today,
        "source": "snapshot" if snapshot else "records",
        "backend": backend,
        "tz": tz_name,
        "spaces": sorted(
            spaces.values(), key=lambda s: (s["id"] == "laptop", s["name"].lower())
        ),
        "agents": agents,
        "waiting": waiting,
        "backlog": backlog_items,
        "queued_total": len(queued),
        "finished": finished,
    }
    cache.sweep()
    return FeedResult(fleet=fleet, transcripts=transcripts, changes=changes)


def _load_tz(name: str | None) -> _dt.tzinfo | None:
    if not name:
        return None
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - unknown zone: fall back to local time
        return None


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _dump(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode()


def assert_safe_out_dir(out_dir: Path, fm_home: Path) -> None:
    """Refuse to write anywhere inside the firstmate home."""
    out = out_dir.expanduser().resolve()
    home = fm_home.expanduser().resolve()
    if out == home or home in out.parents:
        raise SystemExit(f"refusing to write inside the firstmate home: {out}")


def write_fleet(out_dir: Path, result: FeedResult, previous: dict[str, bytes]) -> bool:
    """Write ``fleet.json`` atomically when its content changed.

    ``generated_at`` and ``version`` change on every run, so they are left
    out of the comparison; an unchanged fleet is not rewritten and the live
    channel stays quiet. Returns True when the file was written.
    """
    stable = {
        k: v for k, v in result.fleet.items() if k not in ("generated_at", "version")
    }
    key = _dump(stable)
    path = out_dir / "fleet.json"
    if previous.get("fleet") == key and path.exists():
        # Refresh the age once a minute at most, without a content change.
        age = time.time() - (mtime(path) or 0)
        if age < 60:
            return False
    _atomic_write_bytes(path, _dump(result.fleet))
    previous["fleet"] = key
    return True


def _same_bytes(path: Path, data: bytes) -> bool:
    try:
        return path.read_bytes() == data
    except OSError:
        return False


def _stable_dump(obj: dict[str, Any], *volatile: str) -> bytes:
    return _dump({k: v for k, v in obj.items() if k not in volatile})


def write_outputs(out_dir: Path, result: FeedResult, previous: dict[str, bytes]) -> int:
    """Write per agent files that changed, drop departed ones, then the fleet.

    ``previous`` remembers what was last written, so unchanged files are not
    rewritten. ``fleet.json`` goes last: a reader that sees its new version
    finds the files it points at. Returns the number of files written.
    """
    written = 0
    wanted: dict[str, tuple[bytes, bytes]] = {}
    for sub, items in (
        ("transcripts", result.transcripts),
        ("changes", result.changes),
    ):
        for tid, data in items.items():
            wanted[f"{sub}/{tid}.json"] = (
                _stable_dump(data, "generated_at"),
                _dump(data),
            )
    for rel, (key, data) in wanted.items():
        if previous.get(rel) != key or not (out_dir / rel).exists():
            if rel not in previous and _same_bytes(out_dir / rel, data):
                previous[rel] = key
                continue
            _atomic_write_bytes(out_dir / rel, data)
            previous[rel] = key
            written += 1
    for sub in ("transcripts", "changes"):
        folder = out_dir / sub
        if not folder.is_dir():
            continue
        for p in folder.glob("*.json"):
            rel = f"{sub}/{p.name}"
            if rel not in wanted:
                previous.pop(rel, None)
                try:
                    p.unlink()
                except OSError:
                    pass
    if write_fleet(out_dir, result, previous):
        written += 1
    return written


# ---------------------------------------------------------------------------
# Watch loop
# ---------------------------------------------------------------------------


def records_signature(fm_home: Path) -> tuple:
    """Cheap change detector: stat of state files and the backlog."""
    sig = []
    try:
        for entry in os.scandir(fm_home / "state"):
            if entry.name.endswith((".meta", ".status", ".busy-state", ".turn-ended")):
                try:
                    st = entry.stat()
                except OSError:
                    continue
                sig.append((entry.name, st.st_mtime_ns, st.st_size))
    except OSError:
        pass
    st = mtime(fm_home / "data" / "backlog.md")
    sig.append(("backlog", st))
    return tuple(sorted(sig, key=lambda t: t[0]))


class SnapshotPoller:
    """Runs the slow firstmate snapshot in the background, rarely."""

    def __init__(self, fm_home: Path, every: float, retry: float) -> None:
        self._fm_home = fm_home
        self._every = every
        self._retry = retry
        self._lock = threading.Lock()
        self._latest: tuple[float, dict[str, Any]] | None = None
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self.last_error: float | None = None

    def start(self) -> None:
        self._thread.start()

    def latest(self, max_age: float) -> dict[str, Any] | None:
        with self._lock:
            if self._latest and time.time() - self._latest[0] <= max_age:
                return self._latest[1]
        return None

    def _loop(self) -> None:
        while True:
            snap = run_snapshot(self._fm_home)
            if snap is not None:
                with self._lock:
                    self._latest = (time.time(), snap)
                time.sleep(self._every)
            else:
                self.last_error = time.time()
                time.sleep(self._retry)


# ---------------------------------------------------------------------------
# Live terminal mirror (view only)
# ---------------------------------------------------------------------------

TERMINAL_LINES = 400
TERMINAL_EVERY = 2.0
TERMINAL_TIMEOUT = 3
# A tab that stopped asking for this long no longer gets captures.
DEMAND_TTL = 15.0
# A mirror file nobody asked for in this long is removed.
TERMINAL_KEEP = 120.0


class TmuxRefused(ValueError):
    """A tmux call other than a plain read of a pane was attempted."""


def _tmux(*args: str) -> str | None:
    """Run tmux for reading a pane, and refuse anything else.

    The mirror is view only: no send-keys, no attach, no resize, nothing that
    could reach a worker's input. Only ``capture-pane -p`` (print to stdout,
    never into a tmux buffer) is allowed.
    """
    if not args or args[0] != "capture-pane" or "-p" not in args:
        raise TmuxRefused(" ".join(args[:1]) or "(empty)")
    if "-b" in args:  # -b writes into a tmux paste buffer
        raise TmuxRefused("capture-pane -b")
    try:
        res = subprocess.run(
            ["tmux", *args],
            capture_output=True,
            text=True,
            timeout=TERMINAL_TIMEOUT,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return res.stdout if res.returncode == 0 else None


def capture_pane(target: str, lines: int = TERMINAL_LINES) -> list[str] | None:
    """The last ``lines`` lines of a worker's pane, with colours, secrets redacted."""
    if not re.match(r"^[A-Za-z0-9._:@%-]+$", target or ""):
        return None
    out = _tmux("capture-pane", "-p", "-e", "-J", "-t", target, "-S", f"-{lines}")
    if out is None:
        return None
    rows = out.split("\n")
    while rows and not rows[-1].strip():
        rows.pop()
    return [scrub(r) for r in rows[-lines:]]


def mirror_targets(fleet: dict[str, Any]) -> dict[str, str]:
    """Agents whose pane may be mirrored: own projects, still open, with a window.

    Client projects never carry a window in the fleet, so they never qualify.
    """
    out: dict[str, str] = {}
    for a in fleet.get("agents") or []:
        if a.get("client") or a.get("closed") or a.get("state") == "done":
            continue
        win = a.get("window")
        if win and a.get("id"):
            out[a["id"]] = win
    return out


def read_demand(demand_dir: Path, now: float, ttl: float = DEMAND_TTL) -> set[str]:
    """Agent ids a browser tab asked to mirror within the last ``ttl`` seconds."""
    wanted: set[str] = set()
    try:
        entries = list(os.scandir(demand_dir))
    except OSError:
        return wanted
    for e in entries:
        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$", e.name):
            continue
        try:
            if now - e.stat().st_mtime <= ttl:
                wanted.add(e.name)
        except OSError:
            continue
    return wanted


class TerminalMirror:
    """Captures the panes browser tabs ask for, about every two seconds.

    ``targets`` is refreshed by the main feed loop (agent id to tmux window,
    own projects only). Each tick reads the demand folder Seneca touches and
    writes ``terminals/<id>.json`` for the demanded, mirrorable agents.
    """

    def __init__(self, out_dir: Path, demand_dir: Path, every: float = TERMINAL_EVERY):
        self.out_dir = out_dir
        self.demand_dir = demand_dir
        self.every = every
        self._lock = threading.Lock()
        self._targets: dict[str, str] = {}
        self._last: dict[str, tuple[float, list[str]]] = {}
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def set_targets(self, targets: dict[str, str]) -> None:
        with self._lock:
            self._targets = dict(targets)

    def start(self) -> None:
        self._thread.start()

    def tick(self, now: float | None = None) -> list[str]:
        """Capture what is due; returns the ids written."""
        now = time.time() if now is None else now
        with self._lock:
            targets = dict(self._targets)
        written = []
        for tid in sorted(read_demand(self.demand_dir, now) & set(targets)):
            last = self._last.get(tid)
            if last is not None and now - last[0] < self.every:
                continue
            lines = capture_pane(targets[tid])
            if lines is None:
                continue
            changed = last is None or last[1] != lines
            self._last[tid] = (now, lines)
            if (
                changed
                or now - (mtime(self.out_dir / "terminals" / f"{tid}.json") or 0) > 10
            ):
                _atomic_write_bytes(
                    self.out_dir / "terminals" / f"{tid}.json",
                    _dump(
                        {
                            "schema": TERMINAL_SCHEMA,
                            "task_id": tid,
                            "captured_at": now,
                            "window": targets[tid],
                            "lines": lines,
                        }
                    ),
                )
                written.append(tid)
        self._prune(now, set(targets))
        return written

    def _prune(self, now: float, targets: set[str]) -> None:
        folder = self.out_dir / "terminals"
        try:
            files = list(folder.glob("*.json"))
        except OSError:
            return
        for p in files:
            tid = p.name[: -len(".json")]
            age = now - (mtime(p) or 0)
            if tid not in targets or age > TERMINAL_KEEP:
                self._last.pop(tid, None)
                try:
                    p.unlink()
                except OSError:
                    pass

    def _loop(self) -> None:
        while True:
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001 - keep the mirror alive
                print(f"cockpit-feed terminals: {exc}", file=sys.stderr)
            time.sleep(0.5)


def prepare_demand_dir(path: Path) -> None:
    """Create the folder Seneca's container touches to ask for a mirror.

    Mode 1733: other users (the container's) may create entries but not list
    or delete others'. Only file names are read, never contents.
    """
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o1733)


def run(cfg: FeedConfig, args: argparse.Namespace) -> int:
    assert_safe_out_dir(cfg.out_dir, cfg.fm_home)
    cfg.out_dir.mkdir(parents=True, exist_ok=True)
    salt = load_salt(Path(args.salt_file).expanduser(), legacy_out_dir=cfg.out_dir)
    previous: dict[str, bytes] = {}
    cache = FeedCache()
    poller = None
    if not args.no_snapshot and not args.once:
        poller = SnapshotPoller(cfg.fm_home, args.snapshot_every, args.snapshot_retry)
        poller.start()
    mirror = None
    if not args.no_terminals and not args.once:
        demand_dir = Path(args.demand_dir).expanduser()
        assert_safe_out_dir(demand_dir, cfg.fm_home)
        prepare_demand_dir(demand_dir)
        mirror = TerminalMirror(cfg.out_dir, demand_dir)
        mirror.start()

    def feed_once() -> None:
        snapshot = None
        if args.once and not args.no_snapshot:
            snapshot = run_snapshot(cfg.fm_home)
        elif poller is not None:
            snapshot = poller.latest(max_age=args.snapshot_every * 2)
        result = build_fleet(cfg, snapshot=snapshot, salt=salt, cache=cache)
        write_outputs(cfg.out_dir, result, previous)
        if mirror is not None:
            mirror.set_targets(mirror_targets(result.fleet))

    if args.once:
        feed_once()
        return 0

    last_sig = None
    changed_at: float | None = None
    last_run = 0.0
    while True:
        sig = records_signature(cfg.fm_home)
        now = time.monotonic()
        if sig != last_sig:
            last_sig = sig
            changed_at = now
        due_debounced = changed_at is not None and now - changed_at >= args.debounce
        due_floor = now - last_run >= args.floor
        if due_debounced or due_floor:
            try:
                feed_once()
            except Exception as exc:  # noqa: BLE001 - keep the watcher alive
                print(f"cockpit-feed: {exc}", file=sys.stderr)
            last_run = time.monotonic()
            changed_at = None
        time.sleep(args.scan_every)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    home = Path.home()
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--firstmate",
        default=os.environ.get("FIRSTMATE_HOME", str(home / "firstmate")),
    )
    p.add_argument(
        "--out",
        default=os.environ.get(
            "OPENSENECA_COCKPIT_DIR",
            str(home / ".local" / "share" / "openseneca" / "cockpit"),
        ),
    )
    p.add_argument(
        "--client",
        action="append",
        default=None,
        help="regex prefix of a client project (repeatable)",
    )
    p.add_argument(
        "--space-name",
        action="append",
        default=[],
        help="folder=Display name (repeatable)",
    )
    p.add_argument("--tz", default=None, help="IANA zone for dates")
    p.add_argument(
        "--claude-projects",
        default=str(home / ".claude" / "projects"),
        help="Claude Code transcripts folder; 'none' leaves conversations out",
    )
    p.add_argument("--no-git", action="store_true", help="skip worktree changes")
    p.add_argument(
        "--no-github", action="store_true", help="never ask GitHub if a PR merged"
    )
    p.add_argument("--salt-file", default=str(default_salt_file()))
    p.add_argument(
        "--demand-dir",
        default=os.environ.get(
            "OPENSENECA_COCKPIT_DEMAND_DIR",
            str(home / ".local" / "share" / "openseneca" / "cockpit-demand"),
        ),
        help="folder Seneca touches to ask for a live terminal mirror",
    )
    p.add_argument(
        "--no-terminals", action="store_true", help="never mirror tmux panes"
    )
    p.add_argument("--once", action="store_true", help="write once and exit")
    p.add_argument("--no-snapshot", action="store_true")
    p.add_argument("--scan-every", type=float, default=5.0)
    p.add_argument("--debounce", type=float, default=2.0)
    p.add_argument("--floor", type=float, default=60.0)
    p.add_argument("--snapshot-every", type=float, default=600.0)
    p.add_argument("--snapshot-retry", type=float, default=1800.0)
    return p.parse_args(argv)


def config_from_args(args: argparse.Namespace) -> FeedConfig:
    names = dict(DEFAULT_SPACE_NAMES)
    for item in args.space_name:
        k, sep, v = item.partition("=")
        if sep:
            names[k.strip()] = v.strip()
    return FeedConfig(
        fm_home=Path(args.firstmate).expanduser(),
        out_dir=Path(args.out).expanduser(),
        space_names=names,
        client_patterns=tuple(args.client) if args.client else DEFAULT_CLIENT_PATTERNS,
        tz_name=args.tz,
        claude_projects=None
        if args.claude_projects == "none"
        else Path(args.claude_projects).expanduser(),
        read_git=not args.no_git,
        github=None if args.no_github else PrStates(),
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        os.nice(19)
    except OSError:
        pass
    return run(config_from_args(args), args)


if __name__ == "__main__":
    sys.exit(main())
