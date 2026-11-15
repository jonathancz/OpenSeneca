"""Tests for the Discord nudge rules (decision D6 defaults)."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from openjarvis.cockpit.nudges import (
    NudgeRunner,
    NudgeState,
    at_once_message,
    digest_message,
    in_quiet_hours,
    latest_digest_slot,
    mark_sent,
    plan_nudges,
)

PARIS = ZoneInfo("Europe/Paris")


def at(hh: int, mm: int = 0, day: int = 16) -> dt.datetime:
    return dt.datetime(2026, 11, day, hh, mm, tzinfo=PARIS)


def ask(agent: str, kind: str = "decision", key: str = "k1", **extra) -> dict:
    return {
        "kind": kind,
        "agent_id": agent,
        "title": f"Task {agent}",
        "space": "arbo",
        "client": False,
        "key": key,
        "summary": "SUMMARY TEXT that must not be sent",
        "since": 1_794_000_000,
        **extra,
    }


def fleet(*waiting: dict, agents: list | None = None, generated: int = 0) -> dict:
    return {
        "generated_at": generated,
        "tz": "Europe/Paris",
        "spaces": [
            {"id": "arbo", "name": "NextBranch"},
            {"id": "belit", "name": "belit"},
        ],
        "agents": agents or [],
        "waiting": list(waiting),
    }


def run(f: dict, state: NudgeState, now: dt.datetime) -> tuple[list[str], NudgeState]:
    msgs, st = plan_nudges(f, state, now)
    for _text, keys in msgs:
        mark_sent(st, keys, int(now.timestamp()))
    return [m for m, _ in msgs], st


def seeded() -> NudgeState:
    _, st = plan_nudges(fleet(), NudgeState(), at(10))
    return st


def test_quiet_hours_and_slots():
    assert (
        in_quiet_hours(at(23, 0))
        and in_quiet_hours(at(2))
        and in_quiet_hours(at(7, 59))
    )
    assert not in_quiet_hours(at(8)) and not in_quiet_hours(at(22, 59))
    assert latest_digest_slot(at(9, 5)) == at(9)
    assert latest_digest_slot(at(16, 59)) == at(9)
    assert latest_digest_slot(at(8, 0)) == at(17, day=15)


def test_first_run_seeds_without_a_burst():
    msgs, st = run(fleet(ask("a"), ask("b", "blocked")), NudgeState(), at(10))
    assert msgs == [] and st.seeded and len(st.sent) == 2


def test_new_decision_is_sent_once_and_names_only_what_it_needs():
    st = seeded()
    msgs, st = run(fleet(ask("a", key="token-expiry")), st, at(10, 1))
    assert msgs == [
        "Task a (NextBranch) needs your decision: token-expiry. Answer in Claude Code."
    ]
    assert "SUMMARY" not in msgs[0]
    msgs, st = run(fleet(ask("a", key="token-expiry")), st, at(10, 2))
    assert msgs == []


def test_reopened_key_is_news_again():
    st = seeded()
    _, st = run(fleet(ask("a")), st, at(10, 1))
    _, st = run(fleet(), st, at(10, 2))
    msgs, st = run(fleet(ask("a")), st, at(10, 3))
    assert len(msgs) == 1


def test_quiet_hours_hold_decisions_but_not_blocks():
    st = seeded()
    f = fleet(ask("a"), ask("b", "blocked", key="vpn"))
    msgs, st = run(f, st, at(23, 30))
    assert msgs == ["Task b (NextBranch) is blocked: vpn. Help it in Claude Code."]
    msgs, st = run(f, st, at(3))
    assert msgs == []
    msgs, st = run(f, st, at(8, 0, day=17))
    assert msgs == [
        "Task a (NextBranch) needs your decision: k1. Answer in Claude Code."
    ]


def test_client_messages_carry_no_key():
    item = ask(
        "client-1", "blocked", key=None, client=True, title="belit task", space="belit"
    )
    assert at_once_message(item, {"belit": "belit"}) == (
        "belit task is blocked. Help it in Claude Code."
    )


def test_burst_is_capped():
    st = seeded()
    f = fleet(*[ask(f"t{i}", key=f"k{i}") for i in range(8)])
    msgs, st = run(f, st, at(11))
    assert len(msgs) == 5
    assert msgs[-1] == "And 4 more crew tasks need you. See Crew in Seneca."
    assert len(st.sent) == 8


def test_digest_at_nine_and_five_once_each():
    now_epoch = int(at(9, 5).timestamp())
    agents = [
        {
            "id": "s",
            "title": "Payments plan",
            "kind": "scout",
            "state": "quiet",
            "last_activity": now_epoch - 3 * 86400,
        },
        {
            "id": "r",
            "title": "UX audit",
            "kind": "scout",
            "state": "done",
            "report": True,
            "last_activity": now_epoch - 3600,
        },
        {
            "id": "w",
            "title": "Busy one",
            "kind": "ship",
            "state": "working",
            "last_activity": now_epoch - 60,
        },
    ]
    agents.append(
        {
            "id": "p",
            "title": "MCP",
            "kind": "ship",
            "state": "done",
            "space": "arbo",
            "pr": {"url": "u", "number": 97},
            "last_activity": now_epoch - 600,
        }
    )
    agents.append(
        {
            "id": "m",
            "title": "Merged",
            "kind": "ship",
            "state": "done",
            "closed": True,
            "space": "arbo",
            "pr": {"url": "u", "number": 5},
            "last_activity": now_epoch,
        }
    )
    f = fleet(ask("a"), agents=agents)
    st = seeded()
    st.sent = {"a:decision:k1": 1}
    st.last_digest_at = now_epoch - 12 * 3600
    st.last_digest_slot = at(17, day=15).isoformat(timespec="minutes")
    msgs, st = run(f, st, at(9, 5))
    assert msgs == [
        "Crew digest: 1 ready for review: PR #97 (NextBranch); 1 report landed: "
        "UX audit; quiet for a day or more: Payments plan; 1 decision or block "
        "still wait."
    ]
    msgs, st = run(f, st, at(9, 30))
    assert msgs == []
    msgs, st = run(f, st, at(17, 1))
    assert len(msgs) == 1 and "UX audit" not in msgs[0]  # report already told


def test_missed_digest_is_skipped_not_sent_late():
    st = seeded()
    st.last_digest_slot = at(17, day=15).isoformat(timespec="minutes")
    f = fleet(ask("a"))
    st.sent = {"a:decision:k1": 1}
    msgs, st = run(f, st, at(13))
    assert msgs == []
    assert st.last_digest_slot == at(9).isoformat(timespec="minutes")


def test_empty_digest_sends_nothing():
    assert digest_message(fleet(), None, at(9).timestamp()) is None


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def write_fleet(folder: Path, f: dict) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "fleet.json").write_text(json.dumps(f))


def test_runner_sends_through_the_bridge_and_persists(tmp_path):
    sent: list[str] = []
    cockpit = tmp_path / "cockpit"
    state_path = tmp_path / "data" / "cockpit-nudges.json"
    t0 = at(10).timestamp()
    write_fleet(cockpit, fleet(generated=int(t0)))
    runner = NudgeRunner(cockpit, state_path, send=lambda s: sent.append(s) or True)
    assert runner.tick(t0) == []  # seeds
    write_fleet(cockpit, fleet(ask("a"), generated=int(t0) + 30))
    assert runner.tick(t0 + 30) == sent
    assert len(sent) == 1
    again = NudgeRunner(cockpit, state_path, send=lambda s: sent.append(s) or True)
    assert again.tick(t0 + 60) == []  # dedupe survives a restart
    assert again.status()["sent_total"] == 1


def test_runner_skips_a_stale_fleet_and_unready_channel(tmp_path):
    sent: list[str] = []
    cockpit = tmp_path / "cockpit"
    t0 = at(10).timestamp()
    write_fleet(cockpit, fleet(ask("a"), generated=int(t0) - 3600))
    runner = NudgeRunner(cockpit, tmp_path / "s.json", send=sent.append)
    runner.state = seeded()
    assert runner.tick(t0) == []
    write_fleet(cockpit, fleet(ask("a"), generated=int(t0)))
    closed = NudgeRunner(
        cockpit, tmp_path / "s2.json", send=sent.append, ready=lambda: False
    )
    closed.state = seeded()
    assert closed.tick(t0) == [] and sent == []


def test_failed_send_is_retried(tmp_path):
    cockpit = tmp_path / "cockpit"
    t0 = at(10).timestamp()
    write_fleet(cockpit, fleet(ask("a"), generated=int(t0)))
    outcomes = [False, True]
    runner = NudgeRunner(cockpit, tmp_path / "s.json", send=lambda s: outcomes.pop(0))
    runner.state = seeded()
    assert runner.tick(t0) == []
    assert runner.status()["last_error"] == "send failed"
    assert len(runner.tick(t0 + 30)) == 1
