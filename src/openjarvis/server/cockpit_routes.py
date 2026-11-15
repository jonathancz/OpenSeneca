"""Crew cockpit API: a read only view of the file the host feeder writes.

The feeder (``openjarvis.cockpit.feed``) runs on the host and writes
``fleet.json`` into a folder that this server only reads. In Docker the
folder is mounted read only (``deploy/docker/docker-compose.cockpit.yml``)
and its path given in ``OPENJARVIS_COCKPIT_DIR``; otherwise it is
``<data dir>/cockpit``.

Every HTTP route here is a GET. Nothing in this module can reach firstmate,
tmux, git or GitHub: the cockpit is read only toward the crew, and decisions
and merges stay in Claude Code. ``tests/server/test_cockpit_routes.py`` lists
the routes and fails if any of them accepts a method that changes state.

The live channel (``/v1/cockpit/live``) is a push only WebSocket: it tells the
browser when ``fleet.json`` has a new version and never reads a command.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, Request, WebSocket
from starlette.websockets import WebSocketDisconnect

COCKPIT_DIR_ENV = "OPENJARVIS_COCKPIT_DIR"
STALE_SECONDS = 5 * 60
LIVE_CHECK_SECONDS = 2.0
LIVE_HEARTBEAT_SECONDS = 30.0
MAX_FILE_BYTES = 8 * 1024 * 1024

router = APIRouter(prefix="/v1/cockpit", tags=["cockpit"])


def default_cockpit_dir() -> Path:
    env = os.environ.get(COCKPIT_DIR_ENV, "").strip()
    if env:
        return Path(env).expanduser()
    from openjarvis.core.paths import get_data_dir

    return get_data_dir() / "cockpit"


def _dir(request: Request | WebSocket) -> Path:
    d = getattr(request.app.state, "cockpit_dir", None)
    return Path(d) if d is not None else default_cockpit_dir()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def fleet_version(cockpit_dir: Path) -> int | None:
    """Cheap version read for the live channel: the file's mtime in ns."""
    try:
        return (cockpit_dir / "fleet.json").stat().st_mtime_ns
    except OSError:
        return None


@router.get("/fleet")
def get_fleet(request: Request) -> dict[str, Any]:
    fleet = _read_json(_dir(request) / "fleet.json")
    if fleet is None:
        return {
            "available": False,
            "reason": "The cockpit feeder has not written anything yet.",
        }
    age = max(0, int(time.time()) - int(fleet.get("generated_at") or 0))
    return {
        "available": True,
        "age_seconds": age,
        "stale": age > STALE_SECONDS,
        "fleet": fleet,
    }


@router.websocket("/live")
async def live(websocket: WebSocket) -> None:
    from openjarvis.server.auth_middleware import authenticate_websocket

    expected_key = getattr(websocket.app.state, "api_key", "") or ""
    authorized, subprotocol = authenticate_websocket(websocket, expected_key)
    if not authorized:
        await websocket.close(code=1008)
        return
    await websocket.accept(subprotocol=subprotocol)
    cockpit_dir = _dir(websocket)
    last = fleet_version(cockpit_dir)
    await websocket.send_json({"type": "hello", "version": last})
    last_beat = time.monotonic()
    try:
        while True:
            await asyncio.sleep(LIVE_CHECK_SECONDS)
            cur = fleet_version(cockpit_dir)
            if cur != last:
                last = cur
                await websocket.send_json({"type": "fleet", "version": cur})
                last_beat = time.monotonic()
            elif time.monotonic() - last_beat >= LIVE_HEARTBEAT_SECONDS:
                await websocket.send_json({"type": "beat"})
                last_beat = time.monotonic()
    except (WebSocketDisconnect, RuntimeError, OSError):
        return


def install_cockpit(app: FastAPI, *, cockpit_dir: Path | None = None) -> None:
    """Attach the read only cockpit routes."""
    app.state.cockpit_dir = cockpit_dir or default_cockpit_dir()
    app.include_router(router)


__all__ = [
    "COCKPIT_DIR_ENV",
    "default_cockpit_dir",
    "fleet_version",
    "install_cockpit",
    "router",
]
