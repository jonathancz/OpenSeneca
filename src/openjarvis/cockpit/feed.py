#!/usr/bin/env python3
"""Cockpit feeder: firstmate's records in, a read only JSON file out.

Runs on the host as the captain's user, never inside Docker: the container
cannot see tmux or the firstmate home, and must not. Only the Python standard
library is used, so ``python3 feed.py`` works without the OpenJarvis
environment (see ``scripts/cockpit-feed/``).

What it reads, all read only:

* ``<firstmate>/state/<id>.meta``, ``.status`` and ``.busy-state``: one live
  or recent crew task each.
* ``<firstmate>/data/backlog.md``: task titles, the queue, captain holds and
  finished work.
* ``<firstmate>/data/<id>/report.md``: whether a scout's report exists.
* ``bin/fm-fleet-snapshot.sh --json`` when it succeeds. It is preferred for
  each task's current state and open decisions; today it fails on large
  backlogs, so the direct records are always the fallback.

What it writes, atomically, into the output folder only (never inside the
firstmate home): ``fleet.json``.

Privacy: client projects (belit, VIEW, alpine by default) are reduced to the
project name and the task's state: no title, no status text, no branch, no PR,
and an opaque id. Anything that looks like a token or key is redacted from
every piece of text.
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
        for marker in (" (repo:", " (kind:", " (since ", " (done "):
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
                done=_paren_field(rest, "done"),
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


def load_salt(out_dir: Path) -> bytes:
    path = out_dir / ".salt"
    try:
        data = path.read_bytes()
        if len(data) >= 16:
            return data
    except OSError:
        pass
    data = os.urandom(32)
    out_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_bytes(path, data)
    return data


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


@dataclass
class FeedConfig:
    fm_home: Path
    out_dir: Path
    space_names: dict[str, str] = field(
        default_factory=lambda: dict(DEFAULT_SPACE_NAMES)
    )
    client_patterns: tuple[str, ...] = DEFAULT_CLIENT_PATTERNS
    tz_name: str | None = None


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


@dataclass
class FeedResult:
    fleet: dict[str, Any]


def _public(privacy: "Privacy", task_id: str, client: bool) -> str:
    return privacy.opaque_id(task_id) if client else task_id


def build_fleet(
    cfg: FeedConfig,
    *,
    now: float | None = None,
    snapshot: dict[str, Any] | None = None,
    salt: bytes = b"cockpit",
) -> FeedResult:
    now = time.time() if now is None else now
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

        times = [t for t in (status_mtime, busy.get("ts")) if t]
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
        closed = row is not None and (row.checked or row.section == "done")
        if closed and state in ("working", "quiet", "paused"):
            state = "done"

        if (
            state == "done"
            and last_activity
            and now - last_activity > (FINISHED_WINDOW_DAYS * 86400)
        ):
            continue

        public_id = _public(privacy, task_id, client)
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

        title = (row.title if row else "") or task_id
        agent = {
            "id": public_id,
            "title": client_title(space) if client else (plain(title, 160) or task_id),
            "space": space,
            "client": client,
            "kind": kind,
            "branch": None if client else meta.get("branch"),
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
        }
        agents.append(agent)

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
    return FeedResult(fleet=fleet)


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


def run(cfg: FeedConfig, args: argparse.Namespace) -> int:
    assert_safe_out_dir(cfg.out_dir, cfg.fm_home)
    cfg.out_dir.mkdir(parents=True, exist_ok=True)
    salt = load_salt(cfg.out_dir)
    previous: dict[str, bytes] = {}
    poller = None
    if not args.no_snapshot and not args.once:
        poller = SnapshotPoller(cfg.fm_home, args.snapshot_every, args.snapshot_retry)
        poller.start()

    def feed_once() -> None:
        snapshot = None
        if args.once and not args.no_snapshot:
            snapshot = run_snapshot(cfg.fm_home)
        elif poller is not None:
            snapshot = poller.latest(max_age=args.snapshot_every * 2)
        write_fleet(
            cfg.out_dir, build_fleet(cfg, snapshot=snapshot, salt=salt), previous
        )

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
