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


def make_app(tmp_path: Path) -> FastAPI:
    app = FastAPI()
    install_cockpit(app, cockpit_dir=tmp_path / "cockpit")
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
        ("/v1/cockpit/fleet", "GET"),
        ("/v1/cockpit/live", "WS"),
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
    assert len(routes) == 2
    for r in routes:
        if isinstance(r, APIRoute):
            assert set(r.methods) <= READ_ONLY_METHODS, r.path


def test_cockpit_server_code_has_no_path_to_the_crew():
    for module in (cockpit_routes,):
        src = Path(module.__file__).read_text()
        for forbidden in (
            "subprocess",
            "send-keys",
            "fm-send",
            "tmux send",
            "gh pr",
            "pull/merge",
            "os.system",
            "terminal.send",
        ):
            assert forbidden not in src, (module.__name__, forbidden)


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


def test_mutating_methods_are_refused(tmp_path):
    client = TestClient(make_app(tmp_path))
    for method in ("post", "put", "patch", "delete"):
        res = getattr(client, method)("/v1/cockpit/fleet")
        assert res.status_code == 405


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
