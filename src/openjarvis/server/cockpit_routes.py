"""Crew cockpit API: read only views of the folder the host feeder writes.

The feeder (``openjarvis.cockpit.feed``) runs on the host and writes
``fleet.json``, ``transcripts/<id>.json``, ``changes/<id>.json`` and
``terminals/<id>.json`` into a folder that this server only reads. In Docker
the folder is mounted read only (``deploy/docker/docker-compose.cockpit.yml``)
and its path given in ``OPENJARVIS_COCKPIT_DIR``; otherwise it is
``<data dir>/cockpit``.

The only thing this server writes for the cockpit is an empty marker file per
agent in its own demand folder (``OPENJARVIS_COCKPIT_DEMAND_DIR``), which tells
the feeder that a browser tab is watching that agent's live terminal mirror.

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
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request, WebSocket
from starlette.websockets import WebSocketDisconnect

from openjarvis.cockpit.nudges import CAPTAIN_CHANNEL, NudgeRunner

logger = logging.getLogger(__name__)

COCKPIT_DIR_ENV = "OPENJARVIS_COCKPIT_DIR"
DEMAND_DIR_ENV = "OPENJARVIS_COCKPIT_DEMAND_DIR"
# A capture older than this is not shown as live.
TERMINAL_FRESH_SECONDS = 30
NUDGES_ENV = "OPENJARVIS_COCKPIT_NUDGES"
STALE_SECONDS = 5 * 60
LIVE_CHECK_SECONDS = 2.0
LIVE_HEARTBEAT_SECONDS = 30.0
MAX_FILE_BYTES = 8 * 1024 * 1024

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")

router = APIRouter(prefix="/v1/cockpit", tags=["cockpit"])


def default_cockpit_dir() -> Path:
    env = os.environ.get(COCKPIT_DIR_ENV, "").strip()
    if env:
        return Path(env).expanduser()
    from openjarvis.core.paths import get_data_dir

    return get_data_dir() / "cockpit"


def default_demand_dir() -> Path:
    env = os.environ.get(DEMAND_DIR_ENV, "").strip()
    if env:
        return Path(env).expanduser()
    from openjarvis.core.paths import get_data_dir

    return get_data_dir() / "cockpit-demand"


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


def _agent_file(request: Request, sub: str, agent_id: str) -> dict[str, Any]:
    if not _ID.match(agent_id) or ".." in agent_id:
        raise HTTPException(status_code=404, detail="unknown agent")
    data = _read_json(_dir(request) / sub / f"{agent_id}.json")
    if data is None:
        raise HTTPException(status_code=404, detail=f"no {sub} for this agent")
    return data


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


@router.get("/agents/{agent_id}/transcript")
def get_transcript(agent_id: str, request: Request) -> dict[str, Any]:
    return _agent_file(request, "transcripts", agent_id)


@router.get("/agents/{agent_id}/changes")
def get_changes(agent_id: str, request: Request) -> dict[str, Any]:
    return _agent_file(request, "changes", agent_id)


@router.get("/agents/{agent_id}/terminal")
def get_terminal(agent_id: str, request: Request) -> dict[str, Any]:
    """The latest view only capture of an agent's terminal.

    Asking for it is what keeps it coming: the route touches
    ``<demand dir>/<agent id>`` (Seneca's own folder, never the crew's), and
    the host feeder captures only panes asked for in the last few seconds.
    Client projects have no mirror.
    """
    if not _ID.match(agent_id) or ".." in agent_id:
        raise HTTPException(status_code=404, detail="unknown agent")
    fleet = _read_json(_dir(request) / "fleet.json") or {}
    agent = next(
        (a for a in fleet.get("agents") or [] if a.get("id") == agent_id), None
    )
    if agent is None or agent.get("client") or not agent.get("window"):
        raise HTTPException(status_code=404, detail="no live mirror for this agent")
    demand_dir = Path(
        getattr(request.app.state, "cockpit_demand_dir", None) or default_demand_dir()
    )
    asked = True
    try:
        mark = demand_dir / agent_id
        mark.touch(exist_ok=True)
        os.utime(mark)
    except OSError:
        asked = False
    data = _read_json(_dir(request) / "terminals" / f"{agent_id}.json")
    captured = float((data or {}).get("captured_at") or 0)
    if data is None or time.time() - captured > TERMINAL_FRESH_SECONDS:
        return {
            "available": False,
            "reason": "Waiting for the first capture from the feeder."
            if asked
            else "Seneca cannot ask the feeder for a mirror (demand folder).",
        }
    return {"available": True, **data}


@router.get("/nudges")
def get_nudges(request: Request) -> dict[str, Any]:
    runner: NudgeRunner | None = getattr(request.app.state, "cockpit_nudges", None)
    if runner is None:
        return {"enabled": False, "channel_ready": False}
    return {"enabled": True, **runner.status()}


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


def _captain_ready(app: FastAPI) -> bool:
    bridge = getattr(app.state, "channel_bridge", None)
    if bridge is None:
        return False
    try:
        return CAPTAIN_CHANNEL in bridge.list_channels()
    except Exception:  # noqa: BLE001
        return False


def _send_captain(app: FastAPI, text: str) -> bool:
    bridge = getattr(app.state, "channel_bridge", None)
    if bridge is None:
        return False
    return bool(bridge.send(CAPTAIN_CHANNEL, text))


def nudges_enabled() -> bool:
    return os.environ.get(NUDGES_ENV, "1").strip().lower() not in (
        "0",
        "false",
        "off",
        "no",
    )


def install_cockpit(
    app: FastAPI,
    *,
    cockpit_dir: Path | None = None,
    state_path: Path | None = None,
    start_nudges: bool = True,
    demand_dir: Path | None = None,
) -> None:
    """Attach the cockpit routes and, when enabled, the Discord nudge runner.

    The runner only sends when the channel bridge offers the ``captain``
    alias, and only reads the cockpit folder; its dedupe state lives in
    Seneca's own data dir, never in the read only folder.
    """
    app.state.cockpit_dir = cockpit_dir or default_cockpit_dir()
    app.state.cockpit_demand_dir = demand_dir or default_demand_dir()
    app.state.cockpit_nudges = None
    app.include_router(router)
    if not nudges_enabled():
        return
    if state_path is None:
        from openjarvis.core.paths import get_data_dir

        state_path = get_data_dir() / "cockpit-nudges.json"
    runner = NudgeRunner(
        app.state.cockpit_dir,
        state_path,
        send=lambda text: _send_captain(app, text),
        ready=lambda: _captain_ready(app),
    )
    app.state.cockpit_nudges = runner
    if not start_nudges:
        return

    @app.on_event("startup")
    async def _start_cockpit_nudges() -> None:
        runner.start()

    @app.on_event("shutdown")
    async def _stop_cockpit_nudges() -> None:
        runner.stop()


__all__ = [
    "COCKPIT_DIR_ENV",
    "NUDGES_ENV",
    "default_cockpit_dir",
    "fleet_version",
    "install_cockpit",
    "router",
]
