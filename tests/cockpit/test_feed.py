"""Tests for the cockpit feeder: parsing, privacy, the board's data, trimming,
changes, and read only use."""

from __future__ import annotations

import hashlib
import json
import os
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
    project_slug,
    read_changes,
    scrub,
    tool_summary,
    trim_transcript,
    write_fleet,
    write_outputs,
)

NOW = 1_794_700_000  # 2026-11-15, a Sunday morning in Paris
DAY = 86_400


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.invalid",
        },
    ).stdout


def make_repo(root: Path, branch: str) -> Path:
    repo = root / "worktree"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "a.txt").write_text("one\n")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "checkout", "-q", "-b", branch)
    (repo / "a.txt").write_text("one\ntwo\nthree\n")
    _git(repo, "commit", "-q", "-am", "work")
    (repo / "new.txt").write_text("untracked\n")
    return repo


def jsonl(*records: dict) -> str:
    return "\n".join(json.dumps(r) for r in records) + "\n"


def iso(epoch: float) -> str:
    import datetime as dt

    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).isoformat()


@pytest.fixture
def fm(tmp_path: Path) -> dict:
    home = tmp_path / "firstmate"
    state = home / "state"
    data = home / "data"
    state.mkdir(parents=True)
    data.mkdir()
    (home / "config").mkdir()
    (home / "config" / "backend").write_text("tmux\n")
    repo = make_repo(tmp_path, "fm/alpha")
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

    projects = tmp_path / "claude-projects"
    folder = projects / project_slug(str(repo))
    folder.mkdir(parents=True)
    (folder / "sess.jsonl").write_text(
        jsonl(
            {"type": "mode", "mode": "normal"},
            {
                "type": "user",
                "timestamp": iso(spawn + 10),
                "message": {
                    "role": "user",
                    "content": "Firstmate input: read the brief"
                    "<system-reminder>hidden reminder</system-reminder>",
                },
            },
            {
                "type": "assistant",
                "timestamp": iso(spawn + 20),
                "message": {
                    "content": [
                        {"type": "thinking", "thinking": "private thoughts"},
                        {"type": "text", "text": "Reading the brief now."},
                        {
                            "type": "tool_use",
                            "name": "Bash",
                            "input": {
                                "command": f"cat {state}/alpha.status && cat ~/.env",
                                "description": "Read the status log",
                            },
                        },
                        {
                            "type": "tool_use",
                            "name": "Read",
                            "input": {"file_path": "/repo/src/secret_module.py"},
                        },
                    ]
                },
            },
            {
                "type": "user",
                "timestamp": iso(spawn + 21),
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "content": "FILE CONTENT key=sk-livekeyabcdefghijklmnop",
                        }
                    ]
                },
            },
            {
                "type": "assistant",
                "isSidechain": True,
                "timestamp": iso(spawn + 22),
                "message": {"content": [{"type": "text", "text": "sidechain"}]},
            },
            {
                "type": "assistant",
                "timestamp": iso(spawn + 30),
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": "Use OPENAI_API_KEY=abc123secretvalue please.",
                        }
                    ]
                },
            },
        )
    )
    # A later session in the same folder that belongs to another task.
    (folder / "other.jsonl").write_text(
        jsonl(
            {
                "type": "user",
                "timestamp": iso(spawn + 2000),
                "message": {
                    "content": f"echo done >> {state}/someone-else.status "
                    f"{state}/someone-else.status"
                },
            },
            {
                "type": "assistant",
                "timestamp": iso(spawn + 2001),
                "message": {"content": [{"type": "text", "text": "not alpha"}]},
            },
        )
    )
    return {
        "home": home,
        "state": state,
        "repo": repo,
        "projects": projects,
        "out": tmp_path / "out",
        "spawn": spawn,
    }


def cfg_for(fm: dict) -> FeedConfig:
    return FeedConfig(
        fm_home=fm["home"],
        out_dir=fm["out"],
        tz_name="Europe/Paris",
        claude_projects=fm["projects"],
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


def test_tool_summary_never_carries_commands_or_contents():
    assert (
        tool_summary("Bash", {"command": "cat secret", "description": "List"}) == "List"
    )
    assert tool_summary("Bash", {"command": "cat secret"}) == "shell command"
    assert tool_summary("Read", {"file_path": "/a/b/c.py"}) == "c.py"
    assert (
        tool_summary("WebFetch", {"url": "https://example.com/x?q=1"}) == "example.com"
    )
    assert tool_summary("mcp__x__y", {"payload": "secret"}) == ""


# ---------------------------------------------------------------------------
# Transcripts
# ---------------------------------------------------------------------------


def test_trim_transcript_keeps_messages_and_tool_names_only(fm):
    path = fm["projects"] / project_slug(str(fm["repo"])) / "sess.jsonl"
    entries = trim_transcript(path)
    blob = json.dumps(entries)
    assert [e["t"] for e in entries] == ["user", "agent", "tool", "tool", "agent"]
    assert entries[0]["text"] == "Firstmate input: read the brief"
    assert entries[2] == {
        "t": "tool",
        "at": entries[2]["at"],
        "name": "Bash",
        "summary": "Read the status log",
    }
    assert entries[3]["summary"] == "secret_module.py"
    for leaked in (
        "hidden reminder",
        "private thoughts",
        "FILE CONTENT",
        "sk-live",
        "sidechain",
        "cat ~/.env",
        "abc123secretvalue",
    ):
        assert leaked not in blob


def test_transcript_binding_needs_the_tasks_own_state_files(fm):
    path = feed.find_transcript(
        fm["projects"],
        str(fm["repo"]),
        fm["spawn"],
        task_id="alpha",
        state_dir=str(fm["state"]),
    )
    assert path is not None and path.name == "sess.jsonl"
    other = feed.find_transcript(
        fm["projects"],
        str(fm["repo"]),
        fm["spawn"],
        task_id="someone-else",
        state_dir=str(fm["state"]),
    )
    assert other is not None and other.name == "other.jsonl"
    # Started before the task was spawned: not bound.
    assert (
        feed.find_transcript(
            fm["projects"],
            str(fm["repo"]),
            fm["spawn"] + 10_000,
            task_id="alpha",
            state_dir=str(fm["state"]),
        )
        is None
    )


# ---------------------------------------------------------------------------
# Changes
# ---------------------------------------------------------------------------


def test_read_changes_against_main_without_touching_the_index(fm):
    repo = fm["repo"]
    index = repo / ".git" / "index"
    before = (index.stat().st_mtime_ns, index.read_bytes())
    ch = read_changes(str(repo), "fm/alpha")
    assert ch is not None
    files = {f["path"]: f for f in ch["files"]}
    assert files["a.txt"] == {"path": "a.txt", "status": "M", "added": 2, "removed": 0}
    assert files["new.txt"]["status"] == "A"
    assert (ch["total_files"], ch["added"], ch["removed"]) == (2, 2, 0)
    assert (index.stat().st_mtime_ns, index.read_bytes()) == before
    assert not (repo / ".git" / "index.lock").exists()
    # The folder now holds another task's branch.
    assert read_changes(str(repo), "fm/other") is None


def test_feeder_runs_only_allowed_read_commands(fm, monkeypatch):
    seen: list[list[str]] = []
    real_run = subprocess.run

    def spy(cmd, *a, **kw):
        seen.append(list(cmd))
        return real_run(cmd, *a, **kw)

    monkeypatch.setattr(feed.subprocess, "run", spy)
    build_fleet(cfg_for(fm), now=NOW)
    assert seen, "expected git reads"
    for cmd in seen:
        assert cmd[0] == "git" and cmd[2] == str(fm["repo"])
        assert cmd[3] == "--no-optional-locks"
        assert ("git", cmd[4]) in feed.ALLOWED_COMMANDS


def test_build_fleet_states_waiting_backlog_and_finished(fm):
    res = build_fleet(cfg_for(fm), now=NOW, salt=b"s" * 32)
    fleet = res.fleet
    assert set(res.transcripts) == {"alpha", "gamma"}
    t = res.transcripts["alpha"]["entries"]
    assert t[0]["t"] == "brief" and t[0]["text"] == "Build the crew home."
    assert any(e["t"] == "event" and e.get("key") == "pick-a" for e in t)
    assert set(res.changes) == {"alpha"}
    assert fleet["schema"] == feed.SCHEMA and fleet["source"] == "records"
    assert fleet["backend"] == "tmux" and fleet["tz"] == "Europe/Paris"
    assert fleet["today"] == "2026-11-15"
    agents = {a["id"]: a for a in fleet["agents"]}

    alpha = agents["alpha"]
    assert alpha["state"] == "needs" and alpha["space"] == "openseneca"
    assert alpha["title"] == "Crew home for Seneca"
    assert alpha["branch"] == "fm/alpha" and alpha["closed"] is False
    assert alpha["window"] == "firstmate:fm-alpha"
    assert alpha["diff"] == {"files": 2, "added": 2, "removed": 0}
    assert alpha["has_transcript"] is True
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


def test_unchanged_files_are_not_read_again(fm, monkeypatch):
    reads: list[str] = []
    for name in ("transcript_start", "transcript_owner", "trim_transcript"):
        real = getattr(feed, name)

        def spy(*a, _real=real, _name=name, **kw):
            reads.append(_name)
            return _real(*a, **kw)

        monkeypatch.setattr(feed, name, spy)
    real_run = subprocess.run
    git_calls: list[list[str]] = []

    def run_spy(cmd, *a, **kw):
        git_calls.append(list(cmd))
        return real_run(cmd, *a, **kw)

    monkeypatch.setattr(feed.subprocess, "run", run_spy)
    cache = feed.FeedCache()
    first = build_fleet(cfg_for(fm), now=NOW, cache=cache)
    assert "trim_transcript" in reads and git_calls
    reads.clear()
    git_calls.clear()
    second = build_fleet(cfg_for(fm), now=NOW + 5, cache=cache)
    assert reads == [] and git_calls == []
    assert (
        second.transcripts["alpha"]["entries"] == first.transcripts["alpha"]["entries"]
    )
    assert second.changes["alpha"]["files"] == first.changes["alpha"]["files"]

    session = fm["projects"] / project_slug(str(fm["repo"])) / "sess.jsonl"
    with open(session, "a") as fh:
        fh.write(
            jsonl(
                {
                    "type": "assistant",
                    "timestamp": iso(fm["spawn"] + 40),
                    "message": {"content": [{"type": "text", "text": "Later."}]},
                }
            )
        )
    _git(fm["repo"], "add", "new.txt")
    third = build_fleet(cfg_for(fm), now=NOW + 10, cache=cache)
    assert "trim_transcript" in reads and git_calls
    texts = [e.get("text") for e in third.transcripts["alpha"]["entries"]]
    assert "Later." in texts


def _close_alpha(fm: dict) -> None:
    backlog = fm["home"] / "data" / "backlog.md"
    backlog.write_text(
        backlog.read_text().replace(
            "- [ ] alpha - Crew home", "- [x] alpha - Crew home"
        )
    )


def _output_state(out: Path) -> dict[str, tuple[bytes, int]]:
    return {
        rel: ((out / rel).read_bytes(), (out / rel).stat().st_mtime_ns)
        for rel in ("transcripts/alpha.json", "changes/alpha.json")
    }


def test_closing_a_task_keeps_its_files_untouched(fm, monkeypatch):
    cache = feed.FeedCache()
    previous: dict[str, bytes] = {}
    open_res = build_fleet(cfg_for(fm), now=NOW, cache=cache)
    write_outputs(fm["out"], open_res, previous)
    before = _output_state(fm["out"])
    open_alpha = {a["id"]: a for a in open_res.fleet["agents"]}["alpha"]

    _close_alpha(fm)
    (fm["state"] / "alpha.status").write_text(
        (fm["state"] / "alpha.status").read_text()
        + f"done [at={fm['spawn'] + 1200}]: merged\n"
    )
    monkeypatch.setattr(
        feed, "trim_transcript", lambda *a, **k: pytest.fail("transcript read")
    )
    monkeypatch.setattr(feed, "read_changes", lambda *a, **k: pytest.fail("git read"))
    for step, use_cache in ((1, cache), (2, cache), (3, feed.FeedCache())):
        res = build_fleet(cfg_for(fm), now=NOW + step * 60, cache=use_cache)
        alpha = {a["id"]: a for a in res.fleet["agents"]}["alpha"]
        assert alpha["closed"] is True
        assert alpha["has_transcript"] is True
        assert alpha["diff"] == open_alpha["diff"]
        assert alpha["last_activity"] >= open_alpha["last_activity"]
        write_outputs(fm["out"], res, previous if use_cache is cache else {})
        assert _output_state(fm["out"]) == before


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
    res = build_fleet(cfg_for(fm), now=NOW, salt=b"s" * 32)
    beta_id = [a["id"] for a in res.fleet["agents"] if a["client"]][0]
    assert beta_id not in res.transcripts and beta_id not in res.changes


def test_write_outputs_skips_unchanged_and_removes_departed(fm):
    res = build_fleet(cfg_for(fm), now=NOW)
    previous: dict[str, bytes] = {}
    first = write_outputs(fm["out"], res, previous)
    assert first == len(res.transcripts) + len(res.changes) + 1
    later = build_fleet(cfg_for(fm), now=NOW + 1)
    assert write_outputs(fm["out"], later, previous) == 0
    later.transcripts.pop("gamma")
    write_outputs(fm["out"], later, previous)
    assert not (fm["out"] / "transcripts" / "gamma.json").exists()
    assert not list(fm["out"].rglob(".*.json.*"))


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
            "--claude-projects",
            str(fm["projects"]),
            "--salt-file",
            str(fm["out"].parent / "state" / "cockpit.salt"),
            "--once",
            "--no-snapshot",
            "--no-github",
        ]
    )
    assert feed.run(feed.config_from_args(args), args) == 0
    assert _tree_digest(fm["home"]) == before
    fleet = json.loads((fm["out"] / "fleet.json").read_text())
    assert fleet["schema"] == feed.SCHEMA
    assert sorted(p.name for p in fm["out"].iterdir()) == [
        "changes",
        "fleet.json",
        "transcripts",
    ]
    assert (fm["out"] / "transcripts" / "alpha.json").is_file()
    assert (fm["out"] / "changes" / "alpha.json").is_file()


def test_salt_lives_outside_the_mounted_folder(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / ".salt").write_bytes(b"x" * 32)
    salt_file = tmp_path / "state" / "cockpit.salt"
    # An older feeder's salt is moved out once, so client ids stay stable.
    assert feed.load_salt(salt_file, legacy_out_dir=out) == b"x" * 32
    assert not (out / ".salt").exists()
    assert salt_file.read_bytes() == b"x" * 32
    assert salt_file.stat().st_mode & 0o777 == 0o600
    assert feed.load_salt(salt_file, legacy_out_dir=out) == b"x" * 32
    fresh = feed.load_salt(tmp_path / "other.salt")
    assert len(fresh) == 32 and fresh != b"x" * 32


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


# ---------------------------------------------------------------------------
# Merged PRs finish a task even when its backlog row stayed open
# ---------------------------------------------------------------------------


def _mark_merged(state: Path, task_id: str, when: float) -> None:
    marker = state / f"{task_id}.pr-poll-merge-notified"
    marker.write_text("fm-pr-poll-merge-notified-v1\ngithub\ngithub.com\no/r\n7\n")
    os.utime(marker, (when, when))


def _settle(fm: dict, task_id: str = "alpha") -> None:
    """Make a task done and idle, with its decision answered."""
    (fm["state"] / f"{task_id}.status").write_text(
        f"done [at={fm['spawn'] + 900}]: PR https://github.com/o/r/pull/7 opened\n"
    )


def test_firstmate_merge_record_finishes_the_task(fm):
    _settle(fm)
    _mark_merged(fm["state"], "alpha", NOW - 600)  # 00:16 on 15 Nov in Paris
    fleet = build_fleet(cfg_for(fm), now=NOW).fleet
    alpha = {a["id"]: a for a in fleet["agents"]}["alpha"]
    assert alpha["closed"] is True and alpha["state"] == "done"
    done = next(f for f in fleet["finished"] if f["id"] == "alpha")
    assert done["done_on"] == "2026-11-15" and done["pr"]["number"] == 7
    # A week later it leaves the board.
    later = build_fleet(cfg_for(fm), now=NOW + 8 * DAY).fleet
    assert "alpha" not in {a["id"] for a in later["agents"]}
    assert "alpha" not in {f["id"] for f in later["finished"]}


def test_a_working_agent_is_not_finished_by_an_old_merge(fm):
    (fm["state"] / "alpha.status").write_text("working: next slice\n")
    (fm["state"] / "alpha.busy-state").write_text(f"v1 state=busy ts={NOW - 60}\n")
    _mark_merged(fm["state"], "alpha", NOW - 3600)
    alpha = {a["id"]: a for a in build_fleet(cfg_for(fm), now=NOW).fleet["agents"]}[
        "alpha"
    ]
    assert alpha["state"] == "working" and alpha["closed"] is False


def test_github_is_asked_once_and_cached(fm):
    _settle(fm)
    calls: list[str] = []

    def fetch(url):
        calls.append(url)
        return {"state": "MERGED", "merged_at": NOW - 7200}

    cfg = cfg_for(fm)
    cfg.github = feed.PrStates(fetch=fetch)
    fleet = build_fleet(cfg, now=NOW).fleet
    assert {a["id"]: a for a in fleet["agents"]}["alpha"]["closed"] is True
    build_fleet(cfg, now=NOW + 5)
    build_fleet(cfg, now=NOW + 5 * 3600)
    assert calls == ["https://github.com/o/r/pull/7"]


def test_open_pr_states_expire_after_half_an_hour():
    calls: list[str] = []

    def fetch(url):
        calls.append(url)
        return {"state": "OPEN", "merged_at": None}

    states = feed.PrStates(fetch=fetch)
    url = "https://github.com/o/r/pull/1"
    states.state(url, 0)
    states.state(url, 60)
    assert len(calls) == 1
    states.state(url, 31 * 60)
    assert len(calls) == 2


def test_gh_is_called_read_only(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(
        '#!/bin/sh\necho "$@" > "$GH_LOG"\n'
        'echo \'{"state":"MERGED","mergedAt":"2026-11-14T10:00:00Z"}\'\n'
    )
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("GH_LOG", str(tmp_path / "gh.log"))
    st = feed.gh_pr_state("https://github.com/o/r/pull/7")
    assert st is not None and st["state"] == "MERGED" and st["merged_at"] > 0
    assert (tmp_path / "gh.log").read_text().split() == [
        "pr",
        "view",
        "https://github.com/o/r/pull/7",
        "--json",
        "state,mergedAt",
    ]
    assert feed.gh_pr_state("https://bitbucket.org/o/r/pull-requests/1") is None


def test_backlog_merged_date_counts_as_done():
    rows = feed.parse_backlog(
        "## Done\n- [x] t - Move the key https://github.com/o/r/pull/126 "
        "(repo: r) (kind: ship) (merged 2026-11-15)\n"
    )
    assert rows[0].done == "2026-11-15" and rows[0].title.endswith("pull/126")
