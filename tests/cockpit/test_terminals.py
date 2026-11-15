"""Live terminal mirror: view only tmux reads of live own-project agents."""

from __future__ import annotations

import json
import os
import subprocess
import time

import pytest

from openjarvis.cockpit import feed
from openjarvis.cockpit.feed import (
    TerminalMirror,
    TmuxRefused,
    capture_pane,
    mirror_targets,
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


NOW = 1_800_000_000


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
    assert mirror_targets(fleet, NOW) == {"a": "firstmate:fm-a"}


def test_mirror_targets_are_live_agents_most_recent_first_capped():
    agents = [
        {"id": "w", "window": "s:w", "state": "working", "last_activity": NOW - 3600},
        {"id": "q", "window": "s:q", "state": "quiet", "last_activity": NOW - 60},
        {"id": "n", "window": "s:n", "state": "needs", "last_activity": NOW - 300},
        {"id": "old", "window": "s:old", "state": "quiet", "last_activity": NOW - 700},
    ]
    assert list(mirror_targets({"agents": agents}, NOW)) == ["q", "n", "w"]
    many = [
        {"id": f"a{i}", "window": f"s:{i}", "state": "working", "last_activity": t}
        for i, t in enumerate(NOW - j for j in range(8))
    ]
    assert list(mirror_targets({"agents": many}, NOW)) == ["a0", "a1", "a2", "a3", "a4"]


def test_mirror_captures_its_targets_only(tmp_path, fake_tmux, monkeypatch):
    seen: list[list[str]] = []
    real_run = subprocess.run

    def spy(cmd, *a, **kw):
        seen.append(list(cmd))
        return real_run(cmd, *a, **kw)

    monkeypatch.setattr(feed.subprocess, "run", spy)
    out = tmp_path / "out"
    mirror = TerminalMirror(out, every=2.0)
    mirror.set_targets({"a": "firstmate:fm-a"})
    now = time.time()
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
    assert (out / "terminals" / "b.json").exists()


def test_demand_dir_option_is_gone():
    with pytest.raises(SystemExit):
        feed.parse_args(["--demand-dir", "/tmp/x"])
