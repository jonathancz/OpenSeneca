"""Tests for the crew cockpit routes: read only, folder backed."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytest.importorskip("fastapi")

from fastapi import FastAPI  # noqa: E402
from fastapi.routing import APIRoute, APIWebSocketRoute  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from openjarvis.server import cockpit_routes  # noqa: E402
from openjarvis.server.cockpit_routes import install_cockpit  # noqa: E402

READ_ONLY_METHODS = {"GET", "HEAD"}


def make_app(tmp_path: Path, **kw) -> FastAPI:
    app = FastAPI()
    install_cockpit(
        app,
        cockpit_dir=tmp_path / "cockpit",
        state_path=tmp_path / "data" / "nudges.json",
        start_nudges=False,
        demand_dir=tmp_path / "demand",
        **kw,
    )
    return app


def write(folder: Path, rel: str, data: dict) -> None:
    path = folder / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def cockpit_routes_of(app: FastAPI):
    return [
        r
        for r in app.routes
        if isinstance(r, (APIRoute, APIWebSocketRoute))
        and r.path.startswith("/v1/cockpit")
    ]


def test_every_cockpit_route_is_read_only(tmp_path):
    routes = cockpit_routes_of(make_app(tmp_path))
    listed = sorted(
        (r.path, ",".join(sorted(getattr(r, "methods", None) or {"WS"})))
        for r in routes
    )
    assert listed == [
        ("/v1/cockpit/agents/{agent_id}/changes", "GET"),
        ("/v1/cockpit/agents/{agent_id}/terminal", "GET"),
        ("/v1/cockpit/agents/{agent_id}/transcript", "GET"),
        ("/v1/cockpit/fleet", "GET"),
        ("/v1/cockpit/live", "WS"),
        ("/v1/cockpit/nudges", "GET"),
    ]
    for r in routes:
        if isinstance(r, APIRoute):
            assert set(r.methods) <= READ_ONLY_METHODS, r.path


def test_the_real_app_mounts_only_read_only_cockpit_routes():
    from openjarvis.core.config import JarvisConfig
    from openjarvis.server.app import create_app

    cfg = JarvisConfig()
    cfg.analytics.enabled = False
    cfg.traces.enabled = False
    cfg.security.enabled = False
    engine = MagicMock()
    engine.engine_id = "mock"
    app = create_app(engine, "test-model", config=cfg)
    routes = cockpit_routes_of(app)
    assert len(routes) == 6
    for r in routes:
        if isinstance(r, APIRoute):
            assert set(r.methods) <= READ_ONLY_METHODS, r.path


def test_fleet_missing_then_served(tmp_path):
    client = TestClient(make_app(tmp_path))
    body = client.get("/v1/cockpit/fleet").json()
    assert body["available"] is False
    write(
        tmp_path / "cockpit",
        "fleet.json",
        {"generated_at": int(time.time()), "agents": []},
    )
    body = client.get("/v1/cockpit/fleet").json()
    assert body["available"] is True and body["stale"] is False
    assert body["fleet"]["agents"] == []
    write(tmp_path / "cockpit", "fleet.json", {"generated_at": 1})
    assert client.get("/v1/cockpit/fleet").json()["stale"] is True


def test_agent_files_and_bad_ids(tmp_path):
    client = TestClient(make_app(tmp_path))
    write(tmp_path / "cockpit", "transcripts/alpha.json", {"entries": [{"t": "agent"}]})
    write(tmp_path / "cockpit", "changes/alpha.json", {"files": []})
    (tmp_path / "secret.json").write_text('{"x": 1}')
    assert client.get("/v1/cockpit/agents/alpha/transcript").json()["entries"]
    assert client.get("/v1/cockpit/agents/alpha/changes").json() == {"files": []}
    assert client.get("/v1/cockpit/agents/beta/transcript").status_code == 404
    for bad in ("..%2F..%2Fsecret", ".hidden", "a..b"):
        assert client.get(f"/v1/cockpit/agents/{bad}/changes").status_code == 404


def test_mutating_methods_are_refused(tmp_path):
    client = TestClient(make_app(tmp_path))
    for method in ("post", "put", "patch", "delete"):
        res = getattr(client, method)("/v1/cockpit/fleet")
        assert res.status_code == 405


def test_nudges_status(tmp_path, monkeypatch):
    client = TestClient(make_app(tmp_path))
    body = client.get("/v1/cockpit/nudges").json()
    assert body["enabled"] is True and body["channel_ready"] is False
    assert body["rules"]["digest_times"] == ["09:00", "17:00"]
    assert body["rules"]["quiet_hours"] == ["23:00", "08:00"]
    monkeypatch.setenv("OPENJARVIS_COCKPIT_NUDGES", "0")
    off = TestClient(make_app(tmp_path / "off"))
    assert off.get("/v1/cockpit/nudges").json() == {
        "enabled": False,
        "channel_ready": False,
    }


def test_nudges_use_the_captain_alias_of_the_bridge(tmp_path):
    app = make_app(tmp_path)
    bridge = MagicMock()
    bridge.list_channels.return_value = ["discord", "captain"]
    bridge.send.return_value = True
    app.state.channel_bridge = bridge
    runner = app.state.cockpit_nudges
    # A fixed midday time in the fleet's own zone: no digest is due and it is
    # outside quiet hours, whatever the host clock or time zone says.
    now = 1_763_208_000.0  # 2025-11-15 12:00 UTC
    write(
        tmp_path / "cockpit",
        "fleet.json",
        {"generated_at": int(now), "tz": "UTC", "waiting": []},
    )
    runner.tick(now)  # seed
    write(
        tmp_path / "cockpit",
        "fleet.json",
        {
            "generated_at": int(now),
            "tz": "UTC",
            "spaces": [],
            "waiting": [
                {"kind": "blocked", "agent_id": "a", "title": "Task a", "key": "k"}
            ],
        },
    )
    assert runner.tick(now + 1) == ["Task a is blocked: k. Help it in Claude Code."]
    bridge.send.assert_called_once_with(
        "captain", "Task a is blocked: k. Help it in Claude Code."
    )


def test_live_channel_pushes_new_versions(tmp_path, monkeypatch):
    monkeypatch.setattr(cockpit_routes, "LIVE_CHECK_SECONDS", 0.01)
    client = TestClient(make_app(tmp_path))
    write(tmp_path / "cockpit", "fleet.json", {"generated_at": 1})
    with client.websocket_connect("/v1/cockpit/live") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello" and hello["version"]
        time.sleep(0.02)
        write(tmp_path / "cockpit", "fleet.json", {"generated_at": 2})
        msg = ws.receive_json()
        assert msg["type"] == "fleet" and msg["version"] != hello["version"]


def test_live_channel_requires_the_api_key(tmp_path):
    app = make_app(tmp_path)
    app.state.api_key = "k-test"
    client = TestClient(app)
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/v1/cockpit/live") as ws:
            ws.receive_json()
    with client.websocket_connect(
        "/v1/cockpit/live", headers={"Authorization": "Bearer k-test"}
    ) as ws:
        assert ws.receive_json()["type"] == "hello"


def test_terminal_mirror_is_asked_for_and_served(tmp_path):
    client = TestClient(make_app(tmp_path))
    (tmp_path / "demand").mkdir()
    write(
        tmp_path / "cockpit",
        "fleet.json",
        {
            "generated_at": int(time.time()),
            "agents": [
                {"id": "alpha", "window": "firstmate:fm-alpha", "client": False},
                {"id": "client-x", "window": None, "client": True},
                {"id": "nowin", "client": False},
            ],
        },
    )
    body = client.get("/v1/cockpit/agents/alpha/terminal").json()
    assert body == {
        "available": False,
        "reason": "Waiting for the first capture from the feeder.",
    }
    assert (tmp_path / "demand" / "alpha").is_file()
    write(
        tmp_path / "cockpit",
        "terminals/alpha.json",
        {"captured_at": time.time(), "lines": ["\u001b[32mok\u001b[0m"]},
    )
    body = client.get("/v1/cockpit/agents/alpha/terminal").json()
    assert body["available"] is True and body["lines"] == ["\u001b[32mok\u001b[0m"]
    write(
        tmp_path / "cockpit",
        "terminals/alpha.json",
        {"captured_at": time.time() - 120, "lines": ["old"]},
    )
    assert client.get("/v1/cockpit/agents/alpha/terminal").json()["available"] is False
    for other in ("client-x", "nowin", "ghost", "..%2Fx"):
        assert client.get(f"/v1/cockpit/agents/{other}/terminal").status_code == 404
    assert sorted(p.name for p in (tmp_path / "demand").iterdir()) == ["alpha"]


def test_terminal_without_a_writable_demand_folder_says_so(tmp_path):
    client = TestClient(make_app(tmp_path))
    write(
        tmp_path / "cockpit",
        "fleet.json",
        {"agents": [{"id": "alpha", "window": "firstmate:fm-alpha"}]},
    )
    body = client.get("/v1/cockpit/agents/alpha/terminal").json()
    assert body["available"] is False and "demand folder" in body["reason"]
