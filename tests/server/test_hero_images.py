"""Tests for the rotating hero photography (scheduler, cache, fallback).

The Unsplash API is always mocked through ``httpx.MockTransport``; nothing in
this module reaches the network.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import httpx
import pytest

pytest.importorskip("fastapi")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from openjarvis.server.hero_images import (  # noqa: E402
    FALLBACK_DIR,
    RETRY_SECONDS,
    ROTATION_SECONDS,
    THEMES,
    HeroImageService,
    HeroScheduler,
    install_hero_images,
)

KEY = "test-access-key-not-real"
JPEG = b"\xff\xd8\xff\xe0fake-jpeg-bytes"


class FakeClock:
    def __init__(self, now: float = 1_800_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeUnsplash:
    """A tiny stand in for api.unsplash.com and images.unsplash.com."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.counter = 0
        self.fail_api = False
        self.fail_ping = False
        self.pings: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host = request.url.host
        path = request.url.path
        if host == "api.unsplash.com" and path == "/photos/random":
            if self.fail_api:
                return httpx.Response(503, json={"errors": ["down"]})
            self.counter += 1
            pid = f"photo{self.counter}"
            return httpx.Response(
                200,
                json={
                    "id": pid,
                    "color": "#0c0c0d",
                    "alt_description": "a dark ridge at dusk",
                    "urls": {
                        "raw": f"https://images.unsplash.com/{pid}?ixid=abc",
                    },
                    "links": {
                        "html": f"https://unsplash.com/photos/{pid}",
                        "download_location": (
                            f"https://api.unsplash.com/photos/{pid}/download?ixid=abc"
                        ),
                    },
                    "user": {
                        "name": "Ana Fotógrafa",
                        "username": "ana",
                        "links": {"html": "https://unsplash.com/@ana"},
                    },
                },
            )
        if host == "api.unsplash.com" and path.endswith("/download"):
            if self.fail_ping:
                return httpx.Response(500)
            self.pings.append(path)
            return httpx.Response(200, json={"url": "https://images.unsplash.com/x"})
        if host == "images.unsplash.com":
            return httpx.Response(
                200, content=JPEG, headers={"content-type": "image/jpeg"}
            )
        return httpx.Response(404)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))

    def api_calls(self) -> int:
        return sum(1 for r in self.requests if r.url.path == "/photos/random")


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def unsplash() -> FakeUnsplash:
    return FakeUnsplash()


def make_service(tmp_path: Path, clock: FakeClock, unsplash: FakeUnsplash, key=KEY):
    return HeroImageService(
        tmp_path / "hero", access_key=key, client=unsplash.client(), clock=clock
    )


# -- fallback -------------------------------------------------------------


def test_bundled_fallbacks_exist_and_are_credited():
    manifest = json.loads((FALLBACK_DIR / "manifest.json").read_text())
    assert len(manifest) >= 3
    for entry in manifest:
        assert (FALLBACK_DIR / entry["file"]).is_file()
        assert entry["credit"] and entry["credit_url"].startswith("https://")
        assert entry["license"]


def test_without_key_serves_fallback_and_never_fetches(tmp_path, clock, unsplash):
    svc = make_service(tmp_path, clock, unsplash, key="")
    assert not svc.configured
    assert not svc.is_due()
    assert svc.refresh() is False
    cur = svc.current()
    assert cur["source"] == "fallback"
    assert cur["credit"].startswith("NASA")
    assert svc.image_path().parent == FALLBACK_DIR
    assert unsplash.requests == []


def test_fallback_rotates_every_six_hours(tmp_path, clock, unsplash):
    svc = make_service(tmp_path, clock, unsplash, key="")
    clock.now = 100 * ROTATION_SECONDS  # start of a slot
    first = svc.current()["id"]
    clock.advance(ROTATION_SECONDS - 1)
    assert svc.current()["id"] == first
    clock.advance(1)
    assert svc.current()["id"] != first


def test_offline_with_no_cache_falls_back(tmp_path, clock, unsplash):
    unsplash.fail_api = True
    svc = make_service(tmp_path, clock, unsplash)
    assert svc.refresh() is False
    assert svc.last_error.startswith("HTTPStatusError")
    assert svc.current()["source"] == "fallback"
    assert svc.image_path().parent == FALLBACK_DIR


def test_missing_fallback_dir_reports_none(tmp_path, clock, unsplash):
    svc = HeroImageService(
        tmp_path / "hero",
        access_key="",
        client=unsplash.client(),
        clock=clock,
        fallback_dir=tmp_path / "nowhere",
    )
    assert svc.current() == {"source": "none"}
    assert svc.image_path() is None


# -- fetching and cache ----------------------------------------------------


def test_refresh_caches_image_and_metadata(tmp_path, clock, unsplash):
    svc = make_service(tmp_path, clock, unsplash)
    assert svc.is_due()
    assert svc.refresh() is True

    cache = tmp_path / "hero"
    assert (cache / "current.jpg").read_bytes() == JPEG
    meta = json.loads((cache / "current.json").read_text())
    assert meta["id"] == "photo1"
    assert meta["theme"] == "aerial"
    assert meta["download_tracked"] is True

    cur = svc.current()
    assert cur["source"] == "unsplash"
    assert cur["photographer"] == "Ana Fotógrafa"
    assert cur["photographer_url"] == (
        "https://unsplash.com/@ana?utm_source=seneca&utm_medium=referral"
    )
    assert cur["photo_url"].endswith("?utm_source=seneca&utm_medium=referral")
    assert cur["unsplash_url"] == (
        "https://unsplash.com/?utm_source=seneca&utm_medium=referral"
    )
    assert cur["image_url"] == "/v1/hero/image?v=photo1"
    assert svc.image_path() == cache / "current.jpg"


def test_query_comes_from_theme_and_key_stays_on_the_api(tmp_path, clock, unsplash):
    svc = make_service(tmp_path, clock, unsplash)
    svc.update_settings(theme="night-city")
    svc.refresh()
    api = [r for r in unsplash.requests if r.url.path == "/photos/random"][0]
    assert api.url.params["query"] == THEMES["night-city"][1]
    assert api.url.params["orientation"] == "landscape"
    assert api.headers["authorization"] == f"Client-ID {KEY}"
    image = [r for r in unsplash.requests if r.url.host == "images.unsplash.com"][0]
    assert "authorization" not in image.headers
    assert KEY not in str(image.url)
    # The key is never written to the cache.
    for f in (tmp_path / "hero").iterdir():
        assert KEY.encode() not in f.read_bytes()


def test_download_location_triggered_once_per_image(tmp_path, clock, unsplash):
    svc = make_service(tmp_path, clock, unsplash)
    svc.refresh()
    svc.track_pending_download()
    svc.run_due()
    assert unsplash.pings == ["/photos/photo1/download"]
    clock.advance(ROTATION_SECONDS)
    svc.run_due()
    assert unsplash.pings == ["/photos/photo1/download", "/photos/photo2/download"]


def test_failed_download_ping_is_retried_later(tmp_path, clock, unsplash):
    unsplash.fail_ping = True
    svc = make_service(tmp_path, clock, unsplash)
    svc.refresh()
    meta = json.loads((tmp_path / "hero" / "current.json").read_text())
    assert meta["download_tracked"] is False
    assert svc.seconds_until_due() <= RETRY_SECONDS
    unsplash.fail_ping = False
    assert svc.track_pending_download() is True
    assert svc.track_pending_download() is False
    assert unsplash.pings == ["/photos/photo1/download"]


def test_rejects_non_image_response(tmp_path, clock):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.unsplash.com":
            return httpx.Response(
                200,
                json={
                    "id": "p",
                    "urls": {"raw": "https://images.unsplash.com/p"},
                    "links": {},
                    "user": {},
                },
            )
        return httpx.Response(200, text="<html>", headers={"content-type": "text/html"})

    svc = HeroImageService(
        tmp_path / "hero",
        access_key=KEY,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        clock=clock,
    )
    assert svc.refresh() is False
    assert not (tmp_path / "hero" / "current.jpg").exists()
    assert svc.current()["source"] == "fallback"


def test_rejects_image_url_off_unsplash(tmp_path, clock):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"id": "p", "urls": {"raw": "https://evil.example/p.jpg"}},
        )

    svc = HeroImageService(
        tmp_path / "hero",
        access_key=KEY,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        clock=clock,
    )
    assert svc.refresh() is False


def test_serving_does_not_wait_for_a_slow_fetch(tmp_path, clock, unsplash):
    svc = make_service(tmp_path, clock, unsplash)
    svc.refresh()  # photo1 cached
    release = threading.Event()
    entered = threading.Event()

    def slow(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/photos/random":
            entered.set()
            release.wait(5)
        return unsplash.handler(request)

    svc._client = httpx.Client(transport=httpx.MockTransport(slow))
    clock.advance(ROTATION_SECONDS)
    worker = threading.Thread(target=svc.refresh)
    worker.start()
    try:
        assert entered.wait(2)
        # A second refresh while one is in flight is a no op, not a queue.
        assert svc.refresh() is False
        done = threading.Event()
        result: dict = {}

        def read() -> None:
            result["id"] = svc.current()["id"]
            result["path"] = svc.image_path()
            done.set()

        threading.Thread(target=read).start()
        assert done.wait(1), "current() blocked on the in-flight fetch"
        assert result["id"] == "photo1"
    finally:
        release.set()
        worker.join(5)
    assert svc.current()["id"] == "photo2"


# -- scheduling ------------------------------------------------------------


def test_one_fetch_every_six_hours(tmp_path, clock, unsplash):
    svc = make_service(tmp_path, clock, unsplash)
    svc.run_due()
    assert unsplash.api_calls() == 1
    for _ in range(5):
        clock.advance(ROTATION_SECONDS / 6)
        svc.run_due()
    assert unsplash.api_calls() == 1
    clock.advance(ROTATION_SECONDS / 6)
    assert svc.is_due()
    svc.run_due()
    assert unsplash.api_calls() == 2
    assert svc.current()["id"] == "photo2"


def test_failure_keeps_cache_and_backs_off(tmp_path, clock, unsplash):
    svc = make_service(tmp_path, clock, unsplash)
    svc.run_due()
    clock.advance(ROTATION_SECONDS)
    unsplash.fail_api = True
    svc.run_due()
    assert unsplash.api_calls() == 2
    # The old photo keeps showing while offline.
    assert svc.current()["id"] == "photo1"
    # No retry storm: the next attempt waits for the backoff.
    clock.advance(RETRY_SECONDS - 1)
    svc.run_due()
    assert unsplash.api_calls() == 2
    assert svc.seconds_until_due() >= 60
    clock.advance(1)
    unsplash.fail_api = False
    svc.run_due()
    assert unsplash.api_calls() == 3
    assert svc.current()["id"] == "photo2"


def test_disabling_rotation_freezes_current_photo(tmp_path, clock, unsplash):
    svc = make_service(tmp_path, clock, unsplash)
    svc.run_due()
    svc.update_settings(enabled=False)
    clock.advance(10 * ROTATION_SECONDS)
    assert not svc.is_due()
    svc.run_due()
    assert unsplash.api_calls() == 1
    assert svc.current()["id"] == "photo1"
    assert svc.seconds_until_due() == ROTATION_SECONDS
    svc.update_settings(enabled=True)
    assert svc.is_due()


def test_theme_change_fetches_promptly(tmp_path, clock, unsplash):
    svc = make_service(tmp_path, clock, unsplash)
    svc.run_due()
    clock.advance(60)
    svc.update_settings(theme="coast")
    assert svc.is_due()
    svc.run_due()
    assert unsplash.api_calls() == 2
    meta = json.loads((tmp_path / "hero" / "current.json").read_text())
    assert meta["theme"] == "coast"


def test_seconds_until_due_tracks_next_rotation(tmp_path, clock, unsplash):
    svc = make_service(tmp_path, clock, unsplash)
    assert svc.seconds_until_due() == 60  # due now, clamped to the minimum
    svc.run_due()
    assert svc.seconds_until_due() == ROTATION_SECONDS
    clock.advance(ROTATION_SECONDS - 600)
    assert svc.seconds_until_due() == 600


def test_settings_persist_and_validate(tmp_path, clock, unsplash):
    svc = make_service(tmp_path, clock, unsplash)
    svc.update_settings(enabled=False, theme="terrain")
    again = make_service(tmp_path, clock, unsplash)
    assert again.settings().enabled is False
    assert again.settings().theme == "terrain"
    with pytest.raises(ValueError):
        svc.update_settings(theme="kittens")


def test_cache_survives_restart_without_refetch(tmp_path, clock, unsplash):
    svc = make_service(tmp_path, clock, unsplash)
    svc.run_due()
    clock.advance(3600)
    again = make_service(tmp_path, clock, unsplash)
    again.run_due()
    assert unsplash.api_calls() == 1
    assert again.current()["id"] == "photo1"


def test_scheduler_thread_runs_and_wakes():
    class StubService:
        def __init__(self) -> None:
            self.runs = 0
            self.ran = threading.Event()

        def run_due(self) -> bool:
            self.runs += 1
            self.ran.set()
            return False

        def seconds_until_due(self) -> float:
            return 3600.0

    stub = StubService()
    sched = HeroScheduler(stub)  # type: ignore[arg-type]
    sched.start()
    try:
        assert stub.ran.wait(2)
        stub.ran.clear()
        sched.wake()
        assert stub.ran.wait(2)
        assert stub.runs == 2
    finally:
        sched.stop()
    assert stub.runs == 2


# -- routes ---------------------------------------------------------------


def make_app(service: HeroImageService) -> TestClient:
    app = FastAPI()
    install_hero_images(app, service=service, start_scheduler=False)
    return TestClient(app)


def test_routes_serve_fallback_without_key(tmp_path, clock, unsplash):
    client = make_app(make_service(tmp_path, clock, unsplash, key=""))
    body = client.get("/v1/hero/current").json()
    assert body["photo"]["source"] == "fallback"
    assert body["unsplash_configured"] is False
    assert body["rotation_hours"] == 6
    assert {t["id"] for t in body["themes"]} == set(THEMES)
    img = client.get(body["photo"]["image_url"])
    assert img.status_code == 200
    assert img.headers["content-type"] == "image/jpeg"
    assert img.content[:2] == b"\xff\xd8"


def test_routes_serve_cache_and_never_leak_key(tmp_path, clock, unsplash):
    svc = make_service(tmp_path, clock, unsplash)
    svc.refresh()
    client = make_app(svc)
    res = client.get("/v1/hero/current")
    assert KEY not in res.text
    body = res.json()
    assert body["photo"]["source"] == "unsplash"
    assert body["unsplash_configured"] is True
    img = client.get("/v1/hero/image")
    assert img.content == JPEG
    assert "max-age" in img.headers["cache-control"]


def test_settings_route(tmp_path, clock, unsplash):
    client = make_app(make_service(tmp_path, clock, unsplash, key=""))
    res = client.put("/v1/hero/settings", json={"enabled": False, "theme": "coast"})
    assert res.status_code == 200
    assert res.json()["settings"] == {"enabled": False, "theme": "coast"}
    assert client.put("/v1/hero/settings", json={"theme": "kittens"}).status_code == 422
    assert client.put("/v1/hero/settings", json={"enabled": "yes"}).status_code == 422
    assert client.put("/v1/hero/settings", json={"other": 1}).status_code == 422


def test_install_without_key_starts_no_scheduler(tmp_path, monkeypatch):
    monkeypatch.delenv("UNSPLASH_ACCESS_KEY", raising=False)
    app = FastAPI()
    install_hero_images(app, cache_dir=tmp_path / "hero")
    assert app.state.hero_scheduler is None
    assert app.state.hero_service.configured is False


def test_install_with_key_creates_scheduler_without_network(tmp_path, monkeypatch):
    monkeypatch.setenv("UNSPLASH_ACCESS_KEY", KEY)
    app = FastAPI()
    install_hero_images(app, cache_dir=tmp_path / "hero", start_scheduler=False)
    assert app.state.hero_service.configured is True
    assert app.state.hero_scheduler is None
