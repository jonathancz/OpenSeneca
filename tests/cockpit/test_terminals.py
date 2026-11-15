"""Live terminal mirror: view only tmux reads, on demand, own projects only."""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from openjarvis.cockpit import feed
from openjarvis.cockpit.feed import (
    TerminalMirror,
    TmuxRefused,
    capture_pane,
    mirror_targets,
    prepare_demand_dir,
    read_demand,
)

FAKE_TMUX = """#!/bin/sh
echo "$@" >> "$TMUX_LOG"
printf '\\033[38;5;211mhello\\033[39m from the pane\\n'
printf 'OPENAI_API_KEY=sk-livekeyabcdefghijklmnop\\n'
printf '\\n\\n'
"""


@pytest.fixture
def fake_tmux(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    tmux = bin_dir / "tmux"
    tmux.write_text(FAKE_TMUX)
    tmux.chmod(0o755)
    log = tmp_path / "tmux.log"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("TMUX_LOG", str(log))
    return log


def test_only_a_printed_capture_is_allowed():
    for args in (
        ("send-keys", "-t", "x", "ls", "Enter"),
        ("attach", "-t", "x"),
        ("capture-pane", "-t", "x"),  # without -p it fills a tmux buffer
        ("capture-pane", "-p", "-b", "buf", "-t", "x"),
        ("kill-session", "-t", "x"),
        (),
    ):
        with pytest.raises(TmuxRefused):
            feed._tmux(*args)


def test_capture_keeps_colours_and_redacts_secrets(fake_tmux):
    lines = capture_pane("firstmate:fm-alpha")
    assert lines == [
        "\x1b[38;5;211mhello\x1b[39m from the pane",
        "OPENAI_API_KEY=[redacted]",
    ]
    argv = fake_tmux.read_text().split()
    assert argv[:2] == ["capture-pane", "-p"] and "-e" in argv
    assert argv[argv.index("-t") + 1] == "firstmate:fm-alpha"
    assert capture_pane("bad target; rm -rf /") is None


def test_mirror_targets_exclude_client_and_finished_work():
    fleet = {
        "agents": [
            {"id": "a", "window": "firstmate:fm-a", "state": "working"},
            {"id": "c", "window": None, "client": True, "state": "working"},
            {"id": "d", "window": "firstmate:fm-d", "state": "done"},
            {"id": "x", "window": "firstmate:fm-x", "closed": True, "state": "quiet"},
            {"id": "n", "state": "quiet"},
        ]
    }
    assert mirror_targets(fleet) == {"a": "firstmate:fm-a"}


def test_demand_folder_mode_and_ttl(tmp_path):
    demand = tmp_path / "demand"
    prepare_demand_dir(demand)
    assert demand.stat().st_mode & 0o7777 == 0o1733
    (demand / "a").touch()
    (demand / "old").touch()
    os.utime(demand / "old", (1, 1))
    (demand / ".hidden").touch()
    now = (demand / "a").stat().st_mtime
    assert read_demand(demand, now + 5) == {"a"}
    assert read_demand(demand, now + 60) == set()


def test_mirror_captures_only_what_a_tab_asked_for(tmp_path, fake_tmux, monkeypatch):
    seen: list[list[str]] = []
    real_run = subprocess.run

    def spy(cmd, *a, **kw):
        seen.append(list(cmd))
        return real_run(cmd, *a, **kw)

    monkeypatch.setattr(feed.subprocess, "run", spy)
    out, demand = tmp_path / "out", tmp_path / "demand"
    prepare_demand_dir(demand)
    mirror = TerminalMirror(out, demand, every=2.0)
    mirror.set_targets({"a": "firstmate:fm-a", "b": "firstmate:fm-b"})
    (demand / "a").touch()
    (demand / "zzz").touch()  # not a mirrorable agent
    now = (demand / "a").stat().st_mtime
    assert mirror.tick(now) == ["a"]
    data = json.loads((out / "terminals" / "a.json").read_text())
    assert data["schema"] == feed.TERMINAL_SCHEMA and data["window"] == "firstmate:fm-a"
    assert data["lines"][0].endswith("from the pane")
    assert mirror.tick(now + 1) == []  # not due yet
    assert not (out / "terminals" / "b.json").exists()
    # Every process the mirror started was a printed pane capture.
    assert seen and all(c[:3] == ["tmux", "capture-pane", "-p"] for c in seen)
    # The agent leaves the mirrorable set: its file goes.
    mirror.set_targets({"b": "firstmate:fm-b"})
    mirror.tick(now + 3)
    assert not (out / "terminals" / "a.json").exists()


def test_demand_dir_inside_firstmate_home_is_refused(tmp_path):
    home = tmp_path / "fm"
    home.mkdir()
    args = feed.parse_args(
        [
            "--firstmate",
            str(home),
            "--out",
            str(tmp_path / "out"),
            "--demand-dir",
            str(home / "state" / "demand"),
            "--salt-file",
            str(tmp_path / "salt"),
            "--no-snapshot",
            "--claude-projects",
            "none",
        ]
    )
    with pytest.raises(SystemExit):
        feed.run(feed.config_from_args(args), args)
    assert not (home / "state").exists()
