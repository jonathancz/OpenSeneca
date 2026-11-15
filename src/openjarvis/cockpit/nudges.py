"""Discord nudges for the captain, decided from the cockpit folder.

Rules (the captain's defaults, decision D6 of the cockpit design):

* **At once** when a crew task opens a decision or reports a block.
* **Digest** at 09:00 and 17:00: PRs ready, reports landed since the last
  digest, agents quiet for a day, and how many decisions still wait.
* **Quiet hours** 23:00 to 08:00 hold everything except blocks. A decision
  opened at night is sent when quiet hours end.

Each message is one line that names the agent and what it needs. It never
carries code, tokens, file contents or transcript text: only the task title,
its space, the decision key and where to answer. Client projects arrive here
already reduced to "<project> task" by the feeder. Messages go through the
existing channel bridge to the ``captain`` alias (the owner's Discord DM).

:func:`plan_nudges` is pure; :class:`NudgeRunner` adds the file reads, the
persisted dedupe state and the thread.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

CAPTAIN_CHANNEL = "captain"
TICK_SECONDS = 30.0
STALE_FLEET_SECONDS = 15 * 60
QUIET_FOR_A_DAY = 24 * 60 * 60
QUIET_FOR_A_WEEK = 7 * QUIET_FOR_A_DAY
MESSAGE_CAP = 400


@dataclass(frozen=True)
class NudgeRules:
    digest_times: tuple[str, ...] = ("09:00", "17:00")
    quiet_start: str = "23:00"
    quiet_end: str = "08:00"
    # A digest missed by more than this (server down at the time) is skipped.
    digest_grace_hours: float = 3.0
    max_at_once: int = 5

    def to_dict(self) -> dict[str, Any]:
        return {
            "at_once": ["decision", "blocked"],
            "digest_times": list(self.digest_times),
            "quiet_hours": [self.quiet_start, self.quiet_end],
            "quiet_hours_hold": "everything except blocks",
        }


DEFAULT_RULES = NudgeRules()


@dataclass
class NudgeState:
    seeded: bool = False
    sent: dict[str, int] = field(default_factory=dict)
    last_digest_slot: str | None = None
    last_digest_at: int | None = None
    sent_total: int = 0
    last_sent_at: int | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NudgeState":
        st = cls()
        st.seeded = bool(data.get("seeded"))
        sent = data.get("sent")
        if isinstance(sent, dict):
            st.sent = {str(k): int(v) for k, v in sent.items() if isinstance(v, int)}
        st.last_digest_slot = data.get("last_digest_slot") or None
        st.last_digest_at = data.get("last_digest_at") or None
        st.sent_total = int(data.get("sent_total") or 0)
        st.last_sent_at = data.get("last_sent_at") or None
        return st


def _hm(value: str) -> tuple[int, int]:
    h, _, m = value.partition(":")
    return int(h), int(m or 0)


def in_quiet_hours(now: _dt.datetime, rules: NudgeRules = DEFAULT_RULES) -> bool:
    start, end = _hm(rules.quiet_start), _hm(rules.quiet_end)
    cur = (now.hour, now.minute)
    if start <= end:
        return start <= cur < end
    return cur >= start or cur < end


def latest_digest_slot(
    now: _dt.datetime, rules: NudgeRules = DEFAULT_RULES
) -> _dt.datetime | None:
    """The most recent digest time at or before ``now`` (today or yesterday)."""
    best = None
    for day_offset in (0, -1):
        day = (now + _dt.timedelta(days=day_offset)).date()
        for t in rules.digest_times:
            h, m = _hm(t)
            slot = _dt.datetime.combine(day, _dt.time(h, m), tzinfo=now.tzinfo)
            if slot <= now and (best is None or slot > best):
                best = slot
    return best


def _clip(text: str, cap: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= cap else text[: cap - 1].rstrip() + "…"


def item_key(item: dict[str, Any]) -> str:
    return f"{item.get('agent_id')}:{item.get('kind')}:{item.get('key') or 'default'}"


def _space_names(fleet: dict[str, Any]) -> dict[str, str]:
    return {
        str(s.get("id")): str(s.get("name") or s.get("id"))
        for s in fleet.get("spaces") or []
        if isinstance(s, dict)
    }


def at_once_message(item: dict[str, Any], spaces: dict[str, str]) -> str:
    title = _clip(item.get("title") or "A crew task", 80)
    space = spaces.get(str(item.get("space")), str(item.get("space") or ""))
    where = f" ({space})" if space and space.lower() not in title.lower() else ""
    key = item.get("key")
    detail = f": {key}" if key and key != "default" and not item.get("client") else ""
    if item.get("kind") == "blocked":
        return f"{title}{where} is blocked{detail}. Help it in Claude Code."
    return f"{title}{where} needs your decision{detail}. Answer in Claude Code."


def digest_message(
    fleet: dict[str, Any], since: int | None, now_epoch: float
) -> str | None:
    """One line: PRs ready, reports landed, quiet agents, waiting decisions."""
    spaces = _space_names(fleet)
    waiting = [w for w in fleet.get("waiting") or [] if isinstance(w, dict)]
    agents = [a for a in fleet.get("agents") or [] if isinstance(a, dict)]
    parts: list[str] = []

    prs = [
        a
        for a in agents
        if a.get("state") == "done" and a.get("pr") and not a.get("closed")
    ]
    if prs:
        names = []
        for a in prs:
            pr = a.get("pr") or {}
            num = f"PR #{pr['number']}" if pr.get("number") else "a PR"
            names.append(f"{num} ({spaces.get(str(a.get('space')), a.get('space'))})")
        parts.append(f"{len(prs)} ready for review: " + ", ".join(names))

    reports = [
        a
        for a in agents
        if a.get("report")
        and a.get("kind") == "scout"
        and a.get("state") == "done"
        and not a.get("closed")
        and (since is None or (a.get("last_activity") or 0) > since)
    ]
    if reports:
        parts.append(
            f"{len(reports)} report{'s' if len(reports) != 1 else ''} landed: "
            + ", ".join(_clip(a.get("title") or "", 50) for a in reports)
        )

    idle = [
        (now_epoch - a["last_activity"], a)
        for a in agents
        if a.get("state") == "quiet"
        and not a.get("closed")
        and a.get("last_activity")
        and now_epoch - a["last_activity"] >= QUIET_FOR_A_DAY
    ]
    quiet = [a for age, a in idle if age <= QUIET_FOR_A_WEEK]
    stale = len(idle) - len(quiet)
    if quiet or stale:
        text = ""
        if quiet:
            text = "quiet for a day or more: " + ", ".join(
                _clip(a.get("title") or "", 50) for a in quiet
            )
        if stale:
            tail = f"{stale} tareas paradas desde hace más de una semana"
            text = f"{text} y {tail}" if text else f"y {tail}"
        parts.append(text)

    asks = [w for w in waiting if w.get("kind") in ("decision", "blocked")]
    if asks:
        parts.append(
            f"{len(asks)} decision{'s' if len(asks) != 1 else ''} or block"
            f"{'s' if len(asks) != 1 else ''} still wait"
        )

    if not parts:
        return None
    return _clip("Crew digest: " + "; ".join(parts) + ".", MESSAGE_CAP)


def plan_nudges(
    fleet: dict[str, Any] | None,
    state: NudgeState,
    now: _dt.datetime,
    rules: NudgeRules = DEFAULT_RULES,
) -> tuple[list[tuple[str, list[str]]], NudgeState]:
    """Decide what to send now.

    Returns ``(messages, state)`` where each message is ``(text, keys)``: the
    keys to record as sent once the text went out. The state is a new object;
    the caller persists it after marking sent keys with :func:`mark_sent`.
    """
    st = NudgeState.from_dict(asdict(state))
    out: list[tuple[str, list[str]]] = []
    now_epoch = int(now.timestamp())
    if not fleet:
        return out, st

    waiting = [w for w in fleet.get("waiting") or [] if isinstance(w, dict)]
    asks = [w for w in waiting if w.get("kind") in ("decision", "blocked")]
    open_keys = {item_key(w): w for w in asks}
    slot = latest_digest_slot(now, rules)
    slot_id = slot.isoformat(timespec="minutes") if slot else None

    if not st.seeded:
        # First run: what already waits is covered by the next digest rather
        # than a burst of messages.
        st.seeded = True
        st.sent = {k: now_epoch for k in open_keys}
        st.last_digest_slot = slot_id
        st.last_digest_at = now_epoch
        return out, st

    # Forget closed asks, so a reopened key is news again.
    st.sent = {k: v for k, v in st.sent.items() if k in open_keys}

    quiet = in_quiet_hours(now, rules)
    spaces = _space_names(fleet)
    fresh = [
        (k, w)
        for k, w in open_keys.items()
        if k not in st.sent and (not quiet or w.get("kind") == "blocked")
    ]
    fresh.sort(key=lambda kw: (kw[1].get("kind") != "blocked", kw[1].get("since") or 0))
    if len(fresh) > rules.max_at_once:
        head, rest = fresh[: rules.max_at_once - 1], fresh[rules.max_at_once - 1 :]
        for k, w in head:
            out.append((at_once_message(w, spaces), [k]))
        out.append(
            (
                f"And {len(rest)} more crew tasks need you. See Crew in Seneca.",
                [k for k, _ in rest],
            )
        )
    else:
        for k, w in fresh:
            out.append((at_once_message(w, spaces), [k]))

    if slot is not None and slot_id != st.last_digest_slot and not quiet:
        grace = rules.digest_grace_hours * 3600
        if (now - slot).total_seconds() <= grace:
            text = digest_message(fleet, st.last_digest_at, now_epoch)
            if text:
                out.append((text, [f"digest:{slot_id}"]))
            else:
                st.last_digest_slot = slot_id
                st.last_digest_at = now_epoch
        else:
            st.last_digest_slot = slot_id
            st.last_digest_at = now_epoch
    return out, st


def mark_sent(state: NudgeState, keys: list[str], now_epoch: int) -> None:
    for k in keys:
        if k.startswith("digest:"):
            state.last_digest_slot = k[len("digest:") :]
            state.last_digest_at = now_epoch
        else:
            state.sent[k] = now_epoch
    state.sent_total += 1
    state.last_sent_at = now_epoch


def _zone(name: str | None) -> _dt.tzinfo | None:
    if not name:
        return None
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - unknown zone name
        return None


class NudgeRunner:
    """Reads ``fleet.json`` every tick and sends what :func:`plan_nudges` says."""

    def __init__(
        self,
        cockpit_dir: Path,
        state_path: Path,
        send: Callable[[str], bool],
        *,
        rules: NudgeRules = DEFAULT_RULES,
        tz_name: str | None = None,
        ready: Callable[[], bool] | None = None,
    ) -> None:
        self.cockpit_dir = Path(cockpit_dir)
        self.state_path = Path(state_path)
        self.rules = rules
        self._send = send
        self._ready = ready or (lambda: True)
        self._tz_name = tz_name
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.state = self._load_state()
        self.last_error: str | None = None

    # -- persistence -----------------------------------------------------
    def _load_state(self) -> NudgeState:
        try:
            return NudgeState.from_dict(json.loads(self.state_path.read_text()))
        except (OSError, ValueError, TypeError):
            return NudgeState()

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            prefix=".cockpit-nudges.", dir=str(self.state_path.parent)
        )
        with os.fdopen(fd, "w") as fh:
            json.dump(asdict(self.state), fh)
        os.replace(tmp, self.state_path)

    def read_fleet(self) -> dict[str, Any] | None:
        try:
            data = json.loads((self.cockpit_dir / "fleet.json").read_text())
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def tz(self, fleet: dict[str, Any] | None) -> _dt.tzinfo | None:
        name = self._tz_name or os.environ.get("OPENJARVIS_COCKPIT_TZ")
        if not name and fleet:
            name = fleet.get("tz")
        return _zone(name)

    # -- one tick ----------------------------------------------------------
    def tick(self, now_epoch: float | None = None) -> list[str]:
        """Send what is due. Returns the texts that went out."""
        now_epoch = time.time() if now_epoch is None else now_epoch
        with self._lock:
            if not self._ready():
                return []
            fleet = self.read_fleet()
            if fleet is None:
                return []
            generated = fleet.get("generated_at") or 0
            if now_epoch - generated > STALE_FLEET_SECONDS:
                # The feeder stopped; old asks were sent when they were new.
                return []
            tz = self.tz(fleet)
            now = (
                _dt.datetime.fromtimestamp(now_epoch, tz)
                if tz
                else _dt.datetime.fromtimestamp(now_epoch).astimezone()
            )
            messages, new_state = plan_nudges(fleet, self.state, now, self.rules)
            sent: list[str] = []
            for text, keys in messages:
                ok = False
                try:
                    ok = bool(self._send(text))
                except Exception as exc:  # noqa: BLE001 - keep the runner alive
                    self.last_error = type(exc).__name__
                if not ok:
                    self.last_error = self.last_error or "send failed"
                    break
                mark_sent(new_state, keys, int(now_epoch))
                sent.append(text)
            self.state = new_state
            try:
                self._save_state()
            except OSError as exc:
                self.last_error = f"state not saved: {exc.strerror}"
            return sent

    def status(self) -> dict[str, Any]:
        return {
            "rules": self.rules.to_dict(),
            "running": self._thread is not None and self._thread.is_alive(),
            "channel_ready": bool(self._safe_ready()),
            "sent_total": self.state.sent_total,
            "last_sent_at": self.state.last_sent_at,
            "last_digest_at": self.state.last_digest_at,
            "last_error": self.last_error,
        }

    def _safe_ready(self) -> bool:
        try:
            return self._ready()
        except Exception:  # noqa: BLE001
            return False

    # -- thread --------------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="cockpit-nudges", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # noqa: BLE001
                logger.exception("cockpit nudge tick failed")
            self._stop.wait(TICK_SECONDS)


__all__ = [
    "CAPTAIN_CHANNEL",
    "DEFAULT_RULES",
    "NudgeRules",
    "NudgeRunner",
    "NudgeState",
    "at_once_message",
    "digest_message",
    "in_quiet_hours",
    "latest_digest_slot",
    "mark_sent",
    "plan_nudges",
]
