"""Tests for the cockpit feeder: parsing, privacy, the board's data, read only use."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from openjarvis.cockpit import feed
from openjarvis.cockpit.feed import (
    FeedConfig,
    assert_safe_out_dir,
    build_fleet,
    fold_open_decisions,
    hold_bucket,
    parse_backlog,
    parse_status_line,
    parse_status_log,
    scrub,
    write_fleet,
)

NOW = 1_794_700_000  # 2026-11-15, a Sunday morning in Paris
DAY = 86_400


@pytest.fixture
def fm(tmp_path: Path) -> dict:
    home = tmp_path / "firstmate"
    state = home / "state"
    data = home / "data"
    state.mkdir(parents=True)
    data.mkdir()
    (home / "config").mkdir()
    (home / "config" / "backend").write_text("tmux\n")
    repo = tmp_path / "worktree"
    repo.mkdir()
    spawn = NOW - 3600

    (state / "alpha.meta").write_text(
        "window=firstmate:fm-alpha\n"
        f"worktree={repo}\n"
        "project=/home/x/firstmate/projects/openseneca\n"
        "harness=claude\nkind=ship\nbranch=fm/alpha\nmodel=claude-opus-5-5\n"
        f"spawn_gen=s{spawn}.1.1\n"
    )
    (state / "alpha.status").write_text(
        f"working [at={spawn + 60}]: setup done\n"
        f"needs-decision [at={spawn + 600}] [key=pick-a]: choose A or B, "
        "see https://github.com/o/r/pull/7\n"
    )
    (state / "alpha.busy-state").write_text(
        f"v1 gen=g1 seq=3 state=idle source=claude-hook event=stop ts={spawn + 610}\n"
    )

    (state / "beta.meta").write_text(
        "project=/mnt/c/Repos/belit\nkind=ship\nbranch=b\n"
        f"worktree={tmp_path}/nowhere\nspawn_gen=s{spawn}.2.2\n"
    )
    (state / "beta.status").write_text(
        "blocked: [key=vpn-access] need VPN for Calvin, token=abcdef123456\n"
    )

    (state / "gamma.meta").write_text(
        f"project=/home/x/firstmate/projects/arbo\nkind=scout\nspawn_gen=s{spawn}.3.3\n"
    )
    (state / "gamma.status").write_text(
        f"needs-decision [at={spawn + 5}]: which source\n"
        f"done [at={spawn + 900}]: report written\n"
    )
    (data / "gamma").mkdir()
    (data / "gamma" / "report.md").write_text("# report\n")
    (data / "alpha").mkdir()
    (data / "alpha" / "brief.md").write_text(
        "You are a crewmate.\n\n# Task\n## Captain's intent\nBuild the crew home.\n\n"
        "## Firstmate spec\n- secret plan\n\n# Rules\n1. never push\n"
    )

    (data / "backlog.md").write_text(
        "# Backlog\n\n## In flight\n"
        "- [ ] alpha - Crew home for Seneca (repo: openseneca) (kind: ship)"
        " (since 2026-11-15)\n"
        "  body line with https://github.com/o/r/pull/99\n"
        "- [ ] beta - belit BLTS-1: secret client title (repo: belit) (kind: ship)"
        " (since 2026-11-14)\n"
        "- [ ] gamma - Plan payments (repo: arbo) (kind: scout) (since 2026-11-14)\n"
        "- [ ] hold-live - Pick a logo (repo: arbo) (kind: captain)"
        " (since 2026-11-12) (hold: options (twelve of them) in options.html)"
        " (hold-kind: captain)\n"
        "- [ ] hold-old - Old question (repo: arbo) (kind: captain)"
        " (since 2026-09-01) (hold: stale) (hold-kind: captain)\n"
        "- [ ] hold-client - VIEW thing (repo: VIEW) (kind: captain)"
        " (since 2026-11-13) (hold: client detail) (hold-kind: captain)\n"
        "- [ ] hold-blocked - Needs gamma first blocked-by: zeta (repo: arbo)"
        " (kind: captain) (since 2026-11-13) (hold: wait) (hold-kind: captain)\n"
        "\n## Queued\n"
        "- [ ] zeta - later (repo: arbo) (kind: ship) (since 2026-11-10)\n"
        "\n## Done\n"
        "- [x] shipped - Faster status poll https://github.com/o/r/pull/5"
        " (repo: openseneca) (kind: ship) (done 2026-11-13)\n"
        "- [x] ancient - Old work (repo: openseneca) (kind: ship) (done 2026-10-01)\n"
        "- [x] asked - A captain call (repo: openseneca) (kind: captain)"
        " (done 2026-11-14)\n"
        "- [x] client-done - belit work (repo: belit) (kind: ship) (done 2026-11-14)\n"
    )

    return {
        "home": home,
        "state": state,
        "repo": repo,
        "out": tmp_path / "out",
        "spawn": spawn,
    }


def cfg_for(fm: dict) -> FeedConfig:
    return FeedConfig(
        fm_home=fm["home"],
        out_dir=fm["out"],
        tz_name="Europe/Paris",
    )


# ---------------------------------------------------------------------------
# Status log
# ---------------------------------------------------------------------------


def test_status_line_forms():
    ev = parse_status_line("needs-decision [at=123] [key=k1]: pick one")
    assert (ev.verb, ev.at, ev.key, ev.note) == (
        "needs-decision",
        123,
        "k1",
        "pick one",
    )
    ev = parse_status_line("needs-decision: [key=k2] pick two")
    assert (ev.key, ev.note) == ("k2", "pick two")
    ev = parse_status_line("resolved [key=k2]")
    assert ev.verb == "resolved" and ev.key == "k2"
    ev = parse_status_line("done [at=10:30]: odd stamp")
    assert ev.verb == "done" and ev.at is None
    assert parse_status_line("done without colon is prose") is None
    assert parse_status_line("  continuation line: x") is None
    assert parse_status_line("unknown-verb: x") is None


def test_open_decision_fold_matches_firstmate():
    events = parse_status_log(
        "needs-decision [key=a]: one\n"
        "blocked: stuck\n"
        "needs-decision [key=b]: two\n"
        "resolved [key=a]: answered\n"
        "resolved: unblocked\n"
    )
    assert [e.key for e in fold_open_decisions(events, "ship")] == ["b"]
    events += parse_status_log("done: finished\n")
    assert fold_open_decisions(events, "ship") == []
    # A captain task's done does not clear the set.
    assert [e.key for e in fold_open_decisions(events, "captain")] == ["b"]


# ---------------------------------------------------------------------------
# Backlog
# ---------------------------------------------------------------------------


def test_backlog_rows_and_holds(fm):
    rows = {
        r.id: r for r in parse_backlog((fm["home"] / "data/backlog.md").read_text())
    }
    assert rows["alpha"].title == "Crew home for Seneca"
    assert rows["alpha"].repo == "openseneca" and rows["alpha"].since == "2026-11-15"
    assert rows["hold-live"].hold_reason == "options (twelve of them) in options.html"
    assert rows["hold-blocked"].blocked_by == ["zeta"]
    assert rows["hold-blocked"].title == "Needs gamma first"
    assert rows["shipped"].done == "2026-11-13" and rows["shipped"].checked
    assert rows["shipped"].links == ["https://github.com/o/r/pull/5"]
    done = {r.id for r in rows.values() if r.checked}
    assert hold_bucket(rows["hold-live"], done, NOW, None) == "live"
    assert hold_bucket(rows["hold-old"], done, NOW, None) == "aged"
    assert hold_bucket(rows["hold-blocked"], done, NOW, None) == "blocked"
    assert hold_bucket(rows["alpha"], done, NOW, None) is None


# ---------------------------------------------------------------------------
# Hygiene
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "key sk-abcdefghijklmnopqrstuvwx",
        "Authorization: Bearer abcdefghijklmnop123",
        "DISCORD_BOT_TOKEN=MTIzNDU2Nzg5.abcdef.ghijkl",
        "ghp_abcdefghijklmnopqrstuvwxyz0123",
        "blob AbCdEfGh1234567890AbCdEfGh1234567890XyZ9",
    ],
)
def test_scrub_redacts_secrets(text):
    out = scrub(text)
    assert "[redacted]" in out
    for secret in ("abcdefghijklmnop", "MTIzNDU2", "ghp_abc", "AbCdEfGh12345"):
        assert secret not in out


def test_plain_drops_links_and_secrets():
    assert feed.plain("Move the key out https://github.com/o/r/pull/126", 80) == (
        "Move the key out"
    )
    assert feed.plain("PR https://github.com/o/r/pull/97 (renders)", 80) == (
        "PR (renders)"
    )
    assert feed.plain("PR https://github.com/o/r/pull/97", 80) is None
    assert feed.plain("token=abcdef1234567 here", 80) == "token=[redacted] here"
    assert feed.plain(None, 80) is None


def test_scrub_keeps_ordinary_text():
    text = (
        "Waiting on review of PR #97, commit 2f83739ce4d69599f89afa783e8ff455aa00bb11"
    )
    assert scrub(text) == text


def test_feeder_runs_no_command_but_the_snapshot(fm, monkeypatch):
    seen: list[list[str]] = []
    real_run = subprocess.run

    def spy(cmd, *a, **kw):
        seen.append(list(cmd))
        return real_run(cmd, *a, **kw)

    monkeypatch.setattr(feed.subprocess, "run", spy)
    build_fleet(cfg_for(fm), now=NOW)
    assert seen == []


def test_feeder_source_has_no_write_paths_toward_the_crew():
    src = Path(feed.__file__).read_text()
    for forbidden in (
        "send-keys",
        "fm-send",
        "fm-spawn",
        "gh pr",
        "terminal.send",
        '"git"',
        "git push",
        "os.system",
    ):
        assert forbidden not in src
    # One subprocess call site: firstmate's read only snapshot.
    assert src.count("subprocess.run(") == 1


# ---------------------------------------------------------------------------
# The fleet
# ---------------------------------------------------------------------------


def test_build_fleet_states_waiting_backlog_and_finished(fm):
    fleet = build_fleet(cfg_for(fm), now=NOW, salt=b"s" * 32).fleet
    assert fleet["schema"] == feed.SCHEMA and fleet["source"] == "records"
    assert fleet["backend"] == "tmux" and fleet["tz"] == "Europe/Paris"
    assert fleet["today"] == "2026-11-15"
    agents = {a["id"]: a for a in fleet["agents"]}

    alpha = agents["alpha"]
    assert alpha["state"] == "needs" and alpha["space"] == "openseneca"
    assert alpha["title"] == "Crew home for Seneca"
    assert alpha["branch"] == "fm/alpha" and alpha["closed"] is False
    assert alpha["pr"] == {"url": "https://github.com/o/r/pull/7", "number": 7}
    assert alpha["decisions"][0]["key"] == "pick-a"
    assert alpha["last_activity"] >= fm["spawn"] + 610

    gamma = agents["gamma"]
    assert gamma["state"] == "done" and gamma["report"] is True
    assert gamma["decisions"] == []  # done clears the earlier question

    spaces = {s["id"]: s for s in fleet["spaces"]}
    assert spaces["openseneca"]["name"] == "Seneca"
    assert spaces["arbo"]["name"] == "NextBranch"
    assert spaces["belit"]["client"] is True

    kinds = sorted((w["kind"], w["title"]) for w in fleet["waiting"])
    assert ("decision", "Crew home for Seneca") in kinds
    assert ("hold", "Pick a logo") in kinds
    assert ("blocked", "belit task") in kinds
    assert ("hold", "VIEW task") in kinds
    assert not any(t in ("Old question", "Needs gamma first") for _, t in kinds)

    backlog = [(b["section"], b["title"]) for b in fleet["backlog"]]
    assert backlog == [("queued", "later")]
    assert fleet["queued_total"] == 1

    finished = [f["title"] for f in fleet["finished"]]
    assert finished == [
        "belit task",
        "Faster status poll",
    ]
    assert fleet["finished"][1]["pr"]["number"] == 5


def test_in_flight_rows_without_an_agent_are_kept(fm):
    (fm["state"] / "gamma.meta").unlink()
    fleet = build_fleet(cfg_for(fm), now=NOW).fleet
    rows = {b["id"]: b for b in fleet["backlog"]}
    assert rows["gamma"]["section"] == "in_flight"
    assert rows["gamma"]["title"] == "Plan payments"


def test_client_projects_leave_only_name_and_state(fm):
    fleet = build_fleet(cfg_for(fm), now=NOW, salt=b"s" * 32).fleet
    client = [a for a in fleet["agents"] if a["client"]]
    assert len(client) == 1
    beta = client[0]
    assert beta["id"].startswith("client-") and "beta" not in beta["id"]
    assert beta["title"] == "belit task" and beta["state"] == "blocked"
    assert beta["status_line"] is None and beta["branch"] is None
    assert beta["pr"] is None
    assert beta["decisions"] == [
        {"key": None, "verb": "blocked", "summary": None, "at": None}
    ]
    blob = json.dumps(fleet)
    for leaked in (
        "BLTS",
        "secret client title",
        "VPN",
        "vpn-access",
        "Calvin",
        "client detail",
        "abcdef123456",
        "beta",
        "client-done",
        "hold-client",
    ):
        assert leaked not in blob


def test_snapshot_is_preferred_when_it_succeeds(fm):
    snapshot = {
        "schema": "fm-fleet-snapshot.v1",
        "tasks": [
            {
                "id": "alpha",
                "current_state": {"state": "working"},
                "hints": {"open_decisions": []},
            },
            {
                "id": "gamma",
                "current_state": {"state": "parked"},
                "hints": {
                    "open_decisions": [
                        {"key": "k9", "verb": "needs-decision", "summary": "why"}
                    ]
                },
            },
        ],
    }
    res = build_fleet(cfg_for(fm), now=NOW, snapshot=snapshot)
    agents = {a["id"]: a for a in res.fleet["agents"]}
    assert res.fleet["source"] == "snapshot"
    assert agents["alpha"]["state"] == "working"
    assert agents["alpha"]["state_source"] == "snapshot"
    assert agents["gamma"]["state"] == "needs"
    assert agents["gamma"]["decisions"][0]["key"] == "k9"


def test_run_snapshot_falls_back_on_failure(tmp_path):
    home = tmp_path / "fm"
    (home / "bin").mkdir(parents=True)
    script = home / "bin" / "fm-fleet-snapshot.sh"
    script.write_text("#!/bin/sh\necho 'Argument list too long' >&2\nexit 1\n")
    script.chmod(0o755)
    assert feed.run_snapshot(home) is None
    script.write_text(
        "#!/bin/sh\n"
        'test "$FM_SNAPSHOT_SECONDMATES" = 0 || exit 3\n'
        'test "$FM_CREW_STATE_NO_FORGE" = 1 || exit 4\n'
        'case "$FM_SNAPSHOT_CACHE_DIR" in *cockpit-snapshot-*) ;; *) exit 5;; esac\n'
        'echo \'{"schema":"fm-fleet-snapshot.v1","tasks":[]}\'\n'
    )
    assert feed.run_snapshot(home) == {"schema": "fm-fleet-snapshot.v1", "tasks": []}


# ---------------------------------------------------------------------------
# Output and the read only promise
# ---------------------------------------------------------------------------


def _tree_digest(root: Path) -> dict[str, str]:
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def test_feed_run_leaves_firstmate_home_untouched(fm):
    before = _tree_digest(fm["home"])
    args = feed.parse_args(
        [
            "--firstmate",
            str(fm["home"]),
            "--out",
            str(fm["out"]),
            "--once",
            "--no-snapshot",
        ]
    )
    assert feed.run(feed.config_from_args(args), args) == 0
    assert _tree_digest(fm["home"]) == before
    fleet = json.loads((fm["out"] / "fleet.json").read_text())
    assert fleet["schema"] == feed.SCHEMA
    assert sorted(p.name for p in fm["out"].iterdir()) == [".salt", "fleet.json"]


def test_out_dir_inside_firstmate_home_is_refused(fm):
    with pytest.raises(SystemExit):
        assert_safe_out_dir(fm["home"] / "data" / "cockpit", fm["home"])
    with pytest.raises(SystemExit):
        assert_safe_out_dir(fm["home"], fm["home"])
    assert_safe_out_dir(fm["out"], fm["home"])


def test_write_fleet_skips_unchanged_content(fm):
    previous: dict[str, bytes] = {}
    assert write_fleet(fm["out"], build_fleet(cfg_for(fm), now=NOW), previous)
    # Same content one second later: not rewritten, the live channel stays quiet.
    assert not write_fleet(fm["out"], build_fleet(cfg_for(fm), now=NOW + 1), previous)
    (fm["state"] / "alpha.status").write_text("done: finished\n")
    assert write_fleet(fm["out"], build_fleet(cfg_for(fm), now=NOW + 2), previous)
    assert not list(fm["out"].glob(".fleet.json.*"))
