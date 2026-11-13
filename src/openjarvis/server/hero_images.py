"""Rotating hero photography for the web UI.

The dashboard hero and the empty chat show one large photo, darkened into the
UI palette. The photo changes every six hours, like a lock screen:

* The **backend** fetches one random photo from Unsplash for the chosen theme
  (a curated search query) using ``UNSPLASH_ACCESS_KEY``. The key never leaves
  this process: it is not logged, not stored, and not sent to the browser.
* The image file and its metadata are cached under ``<data dir>/hero/`` and
  served from there, so the browser only ever talks to this server.
* Per the Unsplash API guidelines the photo's ``download_location`` is
  triggered once per image, and the frontend credits
  "Photo by <name> on Unsplash" with ``utm_source=seneca&utm_medium=referral``
  links.
* Without a key, while offline, or before the first fetch succeeds, a few
  bundled public domain NASA photos (``hero_fallback/``) rotate on the same
  six hour clock.

Rotation can be switched off (the current photo stays) and the theme can be
changed from Settings, through ``PUT /v1/hero/settings``.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode, urlsplit

import httpx
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, StrictBool

logger = logging.getLogger(__name__)

ROTATION_SECONDS = 6 * 60 * 60
RETRY_SECONDS = 30 * 60
MIN_WAIT_SECONDS = 60
MAX_IMAGE_BYTES = 15 * 1024 * 1024
UNSPLASH_API = "https://api.unsplash.com"
UTM_QUERY = "utm_source=seneca&utm_medium=referral"
UNSPLASH_HOME = f"https://unsplash.com/?{UTM_QUERY}"
ACCESS_KEY_ENV = "UNSPLASH_ACCESS_KEY"

# Theme id -> (label shown in Settings, Unsplash search query). The queries
# lean dark and aerial so the photo sits well under the near black palette.
THEMES: dict[str, tuple[str, str]] = {
    "aerial": ("Dark aerial landscapes", "dark aerial landscape"),
    "terrain": ("Mountains and terrain", "dark mountain terrain aerial"),
    "coast": ("Coastlines", "moody coastline aerial"),
    "night-city": ("Night cities", "city lights at night aerial"),
}
DEFAULT_THEME = "aerial"

FALLBACK_DIR = Path(__file__).parent / "hero_fallback"

_IMAGE_FILE = "current.jpg"
_META_FILE = "current.json"
_SETTINGS_FILE = "settings.json"


@dataclass
class HeroSettings:
    enabled: bool = True
    theme: str = DEFAULT_THEME


def _with_utm(url: str) -> str:
    """Append the Unsplash referral parameters to an unsplash.com link."""
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{UTM_QUERY}"


def _is_unsplash_url(url: Any, *hosts: str) -> bool:
    if not isinstance(url, str):
        return False
    parts = urlsplit(url)
    return parts.scheme == "https" and parts.hostname in hosts


class HeroImageService:
    """Fetch, cache and serve the current hero photo.

    ``client`` and ``clock`` are injectable so tests never reach the network.
    """

    def __init__(
        self,
        cache_dir: Path,
        *,
        access_key: str | None = None,
        client: httpx.Client | None = None,
        clock: Callable[[], float] = time.time,
        fallback_dir: Path = FALLBACK_DIR,
    ) -> None:
        self._dir = Path(cache_dir)
        self._key = (access_key or "").strip()
        self._client = client
        self._clock = clock
        self._fallback_dir = Path(fallback_dir)
        self._lock = threading.RLock()
        self._fetch_lock = threading.Lock()
        self._last_attempt: float | None = None
        self._last_error: str = ""
        self._fallbacks = self._load_fallbacks()
        self._settings = self._load_settings()

    # -- configuration --------------------------------------------------

    @property
    def configured(self) -> bool:
        return bool(self._key)

    @property
    def last_error(self) -> str:
        return self._last_error

    def settings(self) -> HeroSettings:
        with self._lock:
            return HeroSettings(**asdict(self._settings))

    def update_settings(
        self, *, enabled: bool | None = None, theme: str | None = None
    ) -> HeroSettings:
        with self._lock:
            if theme is not None:
                if theme not in THEMES:
                    raise ValueError(f"unknown theme: {theme}")
                if theme != self._settings.theme:
                    # A new theme should show up promptly, not after backoff.
                    self._last_attempt = None
                self._settings.theme = theme
            if enabled is not None:
                self._settings.enabled = bool(enabled)
            self._write_json(self._dir / _SETTINGS_FILE, asdict(self._settings))
            return self.settings()

    # -- scheduling -----------------------------------------------------

    def is_due(self, now: float | None = None) -> bool:
        """True when a new photo should be fetched from Unsplash."""
        now = self._clock() if now is None else now
        with self._lock:
            if not self._key or not self._settings.enabled:
                return False
            if self._last_attempt is not None and (
                now - self._last_attempt < RETRY_SECONDS
            ):
                return False
            meta = self._cached_meta()
            if meta is None:
                return True
            if meta.get("theme") != self._settings.theme:
                return True
            return now >= float(meta.get("fetched_at", 0)) + ROTATION_SECONDS

    def seconds_until_due(self, now: float | None = None) -> float:
        """How long the scheduler may sleep before checking again."""
        now = self._clock() if now is None else now
        with self._lock:
            if not self._key or not self._settings.enabled:
                return float(ROTATION_SECONDS)
            meta = self._cached_meta()
            if meta is None or meta.get("theme") != self._settings.theme:
                next_rotation = 0.0
            else:
                next_rotation = (
                    float(meta.get("fetched_at", 0)) + ROTATION_SECONDS - now
                )
            retry_floor = (
                self._last_attempt + RETRY_SECONDS - now
                if self._last_attempt is not None
                else 0.0
            )
            wait = max(next_rotation, retry_floor)
            if meta is not None and not meta.get("download_tracked", True):
                wait = min(wait, float(RETRY_SECONDS))
            return max(float(MIN_WAIT_SECONDS), min(wait, float(ROTATION_SECONDS)))

    def run_due(self) -> bool:
        """Fetch if due, and retry a pending download ping. Never raises."""
        fetched = False
        try:
            if self.is_due():
                fetched = self.refresh()
            self.track_pending_download()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Hero image refresh failed: %s", type(exc).__name__)
        return fetched

    # -- fetching -------------------------------------------------------

    def refresh(self) -> bool:
        """Fetch a new photo now. Returns True when the cache was replaced.

        Network I/O happens outside the state lock, so serving the current
        photo never waits on Unsplash; ``_fetch_lock`` keeps fetches serial.
        """
        if not self._key:
            return False
        if not self._fetch_lock.acquire(blocking=False):
            return False  # another fetch is already running
        try:
            with self._lock:
                now = self._clock()
                self._last_attempt = now
                theme = self._settings.theme
            try:
                meta, image = self._fetch_photo(theme)
            except Exception as exc:
                # The message of an httpx error may carry request details, so
                # log only the type and status; never the headers.
                status = getattr(getattr(exc, "response", None), "status_code", "")
                with self._lock:
                    self._last_error = f"{type(exc).__name__} {status}".strip()
                logger.info("Hero image fetch failed: %s", self._last_error)
                return False
            meta["fetched_at"] = now
            meta["theme"] = theme
            meta["download_tracked"] = False
            with self._lock:
                self._write_bytes(self._dir / _IMAGE_FILE, image)
                self._write_json(self._dir / _META_FILE, meta)
                self._last_error = ""
        finally:
            self._fetch_lock.release()
        self.track_pending_download()
        return True

    def track_pending_download(self) -> bool:
        """Trigger the photo's download_location once (Unsplash guideline)."""
        with self._lock:
            meta = self._cached_meta()
            if not self._key or meta is None or meta.get("download_tracked", True):
                return False
            url = meta.get("download_location")
            photo_id = meta.get("id")
            if not _is_unsplash_url(url, "api.unsplash.com"):
                meta["download_tracked"] = True
                self._write_json(self._dir / _META_FILE, meta)
                return False
        try:
            resp = self._http().get(url, headers=self._auth_headers())
            resp.raise_for_status()
        except Exception as exc:
            logger.info("Unsplash download ping failed: %s", type(exc).__name__)
            return False
        with self._lock:
            meta = self._cached_meta()
            if meta is None or meta.get("id") != photo_id:
                return True  # a newer photo replaced it meanwhile
            meta["download_tracked"] = True
            self._write_json(self._dir / _META_FILE, meta)
        return True

    def _fetch_photo(self, theme: str) -> tuple[dict[str, Any], bytes]:
        client = self._http()
        query = THEMES[theme][1]
        resp = client.get(
            f"{UNSPLASH_API}/photos/random",
            params={
                "query": query,
                "orientation": "landscape",
                "content_filter": "high",
            },
            headers=self._auth_headers(),
        )
        resp.raise_for_status()
        photo = resp.json()
        if isinstance(photo, list):  # count=N returns a list
            photo = photo[0]
        urls = photo.get("urls") or {}
        user = photo.get("user") or {}
        links = photo.get("links") or {}
        user_links = user.get("links") or {}
        raw = urls.get("raw")
        if not _is_unsplash_url(raw, "images.unsplash.com"):
            raise ValueError("photo has no usable image url")
        sep = "&" if "?" in raw else "?"
        image_url = (
            raw + sep + urlencode({"w": 2400, "q": 80, "fm": "jpg", "fit": "max"})
        )
        # The image CDN needs no credentials; do not send the key there.
        img = client.get(image_url)
        img.raise_for_status()
        ctype = img.headers.get("content-type", "")
        if not ctype.startswith("image/"):
            raise ValueError("unexpected content type")
        data = img.content
        if not data or len(data) > MAX_IMAGE_BYTES:
            raise ValueError("image size out of range")
        photo_page = links.get("html")
        user_page = user_links.get("html")
        meta = {
            "source": "unsplash",
            "id": str(photo.get("id", "")),
            "alt": photo.get("alt_description") or photo.get("description") or "",
            "color": photo.get("color") or "",
            "photographer": user.get("name") or user.get("username") or "Unknown",
            "photographer_url": _with_utm(user_page)
            if _is_unsplash_url(user_page, "unsplash.com")
            else UNSPLASH_HOME,
            "photo_url": _with_utm(photo_page)
            if _is_unsplash_url(photo_page, "unsplash.com")
            else UNSPLASH_HOME,
            "download_location": links.get("download_location", ""),
        }
        return meta, data

    def _http(self) -> httpx.Client:
        with self._lock:
            if self._client is None:
                self._client = httpx.Client(timeout=20.0, follow_redirects=True)
            return self._client

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Client-ID {self._key}", "Accept-Version": "v1"}

    # -- serving --------------------------------------------------------

    def current(self, now: float | None = None) -> dict[str, Any]:
        """Metadata for the photo to show right now (cache or fallback)."""
        now = self._clock() if now is None else now
        with self._lock:
            meta = self._cached_meta()
            if meta is not None and (self._dir / _IMAGE_FILE).is_file():
                fetched_at = float(meta.get("fetched_at", now))
                return {
                    "source": "unsplash",
                    "id": meta.get("id", ""),
                    "alt": meta.get("alt", ""),
                    "color": meta.get("color", ""),
                    "photographer": meta.get("photographer", ""),
                    "photographer_url": meta.get("photographer_url", UNSPLASH_HOME),
                    "photo_url": meta.get("photo_url", UNSPLASH_HOME),
                    "unsplash_url": UNSPLASH_HOME,
                    "fetched_at": fetched_at,
                    "image_url": f"/v1/hero/image?v={meta.get('id', '')}",
                }
            fb = self._fallback(now)
            if fb is None:
                return {"source": "none"}
            slot = int(now // ROTATION_SECONDS)
            return {
                "source": "fallback",
                "id": fb["id"],
                "alt": fb.get("alt", ""),
                "color": fb.get("color", ""),
                "credit": fb.get("credit", ""),
                "credit_url": fb.get("credit_url", ""),
                "license": fb.get("license", ""),
                "fetched_at": float(slot * ROTATION_SECONDS),
                "image_url": f"/v1/hero/image?v={fb['id']}",
            }

    def image_path(self, now: float | None = None) -> Path | None:
        now = self._clock() if now is None else now
        with self._lock:
            path = self._dir / _IMAGE_FILE
            if self._cached_meta() is not None and path.is_file():
                return path
            fb = self._fallback(now)
            return (self._fallback_dir / fb["file"]) if fb else None

    def status(self) -> dict[str, Any]:
        settings = self.settings()
        return {
            "photo": self.current(),
            "settings": asdict(settings),
            "themes": [{"id": k, "label": v[0]} for k, v in THEMES.items()],
            "rotation_hours": ROTATION_SECONDS // 3600,
            "unsplash_configured": self.configured,
        }

    # -- storage helpers -----------------------------------------------

    def _fallback(self, now: float) -> dict[str, Any] | None:
        if not self._fallbacks:
            return None
        slot = int(now // ROTATION_SECONDS)
        return self._fallbacks[slot % len(self._fallbacks)]

    def _load_fallbacks(self) -> list[dict[str, Any]]:
        try:
            entries = json.loads(
                (self._fallback_dir / "manifest.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            return []
        return [
            e
            for e in entries
            if isinstance(e, dict)
            and (self._fallback_dir / str(e.get("file"))).is_file()
        ]

    def _load_settings(self) -> HeroSettings:
        try:
            raw = json.loads((self._dir / _SETTINGS_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return HeroSettings()
        theme = raw.get("theme") if raw.get("theme") in THEMES else DEFAULT_THEME
        return HeroSettings(enabled=bool(raw.get("enabled", True)), theme=theme)

    def _cached_meta(self) -> dict[str, Any] | None:
        try:
            meta = json.loads((self._dir / _META_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return meta if isinstance(meta, dict) and meta.get("id") else None

    def _write_bytes(self, path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        self._write_bytes(path, json.dumps(payload, indent=2).encode("utf-8"))


class HeroScheduler:
    """Background thread that keeps the cached photo fresh."""

    def __init__(self, service: HeroImageService) -> None:
        self._service = service
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="hero-image-scheduler", daemon=True
        )
        self._thread.start()

    def wake(self) -> None:
        self._wake.set()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._service.run_due()
            self._wake.wait(self._service.seconds_until_due())
            self._wake.clear()


class HeroSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool | None = None
    theme: str | None = None


def _service(request: Request) -> HeroImageService:
    svc = getattr(request.app.state, "hero_service", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="hero imagery unavailable")
    return svc


router = APIRouter(prefix="/v1/hero", tags=["hero"])


@router.get("/current")
def get_current(request: Request) -> dict[str, Any]:
    return _service(request).status()


@router.get("/image")
def get_image(request: Request) -> FileResponse:
    path = _service(request).image_path()
    if path is None:
        raise HTTPException(status_code=404, detail="no hero image")
    # The URL carries the photo id, so a long private cache is safe.
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"Cache-Control": f"private, max-age={ROTATION_SECONDS}"},
    )


@router.put("/settings")
def put_settings(body: HeroSettingsUpdate, request: Request) -> dict[str, Any]:
    svc = _service(request)
    try:
        svc.update_settings(enabled=body.enabled, theme=body.theme)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    scheduler = getattr(request.app.state, "hero_scheduler", None)
    if scheduler is not None:
        scheduler.wake()
    return svc.status()


def install_hero_images(
    app: FastAPI,
    *,
    cache_dir: Path | None = None,
    access_key: str | None = None,
    service: HeroImageService | None = None,
    start_scheduler: bool = True,
) -> HeroImageService:
    """Attach the hero service, routes and (with a key) the scheduler."""
    if service is None:
        if cache_dir is None:
            from openjarvis.core.paths import get_data_dir

            cache_dir = get_data_dir() / "hero"
        if access_key is None:
            access_key = os.environ.get(ACCESS_KEY_ENV, "")
        service = HeroImageService(cache_dir, access_key=access_key)
    app.state.hero_service = service
    app.state.hero_scheduler = None
    app.include_router(router)

    if start_scheduler and service.configured:
        scheduler = HeroScheduler(service)
        app.state.hero_scheduler = scheduler

        @app.on_event("startup")
        async def _start_hero_scheduler() -> None:
            scheduler.start()

        @app.on_event("shutdown")
        async def _stop_hero_scheduler() -> None:
            scheduler.stop()

    return service


__all__ = [
    "ACCESS_KEY_ENV",
    "DEFAULT_THEME",
    "ROTATION_SECONDS",
    "THEMES",
    "HeroImageService",
    "HeroScheduler",
    "HeroSettings",
    "install_hero_images",
    "router",
]
