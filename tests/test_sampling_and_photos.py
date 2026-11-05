import io
from concurrent.futures import Future

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from support import Clock, snapshot
from test_api import decision
from test_runtime import Api, Hardware

from pompom.api import ApiError, DirectApi
from pompom.config import Settings, SettingsStore
from pompom.runtime import Runtime
from pompom.session import Session
from pompom.web import attach_routes


def jpeg():
    output = io.BytesIO()
    Image.new("RGB", (640, 480), "green").save(output, format="JPEG")
    return output.getvalue()


def test_interval_defaults_migrates_and_validates(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    store.path.write_text('{"api_key":"test","condition_preset":"slouch"}')
    assert store.load().capture_interval_seconds == 60
    for value in [1, 60, 3600]:
        store.save(store.updated(store.load(), {"capture_interval_seconds": value}))
        assert store.load().capture_interval_seconds == value
    for value in [0, 3601, True, 1.5, "60", None]:
        with pytest.raises(ValueError):
            store.updated(store.load(), {"capture_interval_seconds": value})


@pytest.mark.parametrize("interval", [1, 60, 120])
def test_sampling_delay_uses_capture_start_and_first_sample_notifies(interval):
    clock = Clock()

    def handler(_):
        clock.now += 0.5
        return httpx.Response(200, json=decision())

    api = DirectApi(httpx.MockTransport(handler), clock)
    settings = Settings(api_key="test", capture_interval_seconds=interval)
    sid = api.start(settings)["session_id"]
    assert api.get(settings, sid)["next_capture_after_ms"] == 0
    result = api.frame(settings, sid, "first", b"jpeg")
    assert result["notify"]  # No second sample or hold-time accumulation.
    assert result["next_capture_after_ms"] == (interval - 0.5) * 1000
    clock.now += 0.5
    assert api.get(settings, sid)["next_capture_after_ms"] == (interval - 1) * 1000


def test_runtime_waits_full_interval_and_retries_no_faster(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    store.save(Settings(api_key="test", capture_interval_seconds=60))
    clock = Clock()
    api = DirectApi(
        httpx.MockTransport(lambda _: httpx.Response(200, json=decision())), clock
    )
    rt = Runtime(Hardware(), api, store, clock)
    try:
        rt.control("start", consent=True)
        rt.step()
        rt.pending.result(2)
        rt.step()
        assert rt.hardware.captures == 1
        clock.now += 59
        rt.step()
        assert rt.hardware.captures == 1
        clock.now += 1
        rt.step()
        rt.pending.result(2)
        rt.step()
        assert rt.hardware.captures == 2
        future = Future()
        future.set_exception(ApiError("rate_limited"))
        rt.pending, rt.pending_generation = future, rt.session.generation
        rt.step()
        assert rt.next_capture == clock.now + 60
    finally:
        rt.close()


def test_event_photos_bounded_in_memory_and_absent_from_status_payload():
    s = Session(Settings(), Clock())
    s.begin()
    for _ in range(25):
        s.log("action_executed", image=jpeg())
    state = s.snapshot()
    assert len(s.event_images) == 20
    assert sum("image_url" in e for e in state["events"]) == 20
    assert all("image_url" not in e for e in state["events"][:5])
    assert "data:image" not in str(state)
    s.stop_local("ended", "session_ended")
    s.begin()
    assert not s.event_images and not s.events


def test_photo_matches_inference_frame_and_is_served_no_store(tmp_path):
    image = jpeg()

    class Camera(Hardware):
        def capture(self):
            self.captures += 1
            return image

    class SampleApi(Api):
        def frame(self, settings, sid, fid, data):
            assert data == image
            self.frames.append(fid)
            return snapshot(
                sid,
                notify=True,
                notification_id="photo",
                phone_probability=1,
                observable_probability=1,
            )

    store = SettingsStore(tmp_path / "settings.json")
    store.save(Settings(api_key="test"))
    rt = Runtime(Camera(), SampleApi(), store)
    try:
        rt.control("start", consent=True)
        rt.step()
        rt.pending.result(2)
        rt.step()
        assert rt.hardware.captures == 1 and len(rt.api.frames) == 1
        event = next(
            e for e in rt.snapshot()["events"] if e["event"] == "action_executed"
        )
        app = FastAPI()
        attach_routes(app, lambda: rt)
        with TestClient(app) as client:
            response = client.get(event["image_url"])
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store"
            with Image.open(io.BytesIO(response.content)) as review:
                assert max(review.size) <= 512
                assert review.getpixel((20, 20))[1] > 100
            rt.control("end")
            rt.control("start", consent=True)
            assert client.get(event["image_url"]).status_code == 404
        assert not list(tmp_path.glob("*.jpg"))
        assert "image" not in store.path.read_text()
    finally:
        rt.close()


def test_late_photo_after_settings_change_is_never_retained(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    store.save(Settings(api_key="test"))
    rt = Runtime(Hardware(), Api(), store)
    try:
        rt.control("start", consent=True)
        future = Future()
        rt.pending, rt.pending_generation = future, rt.session.generation
        rt.configure({"capture_interval_seconds": 120})
        future.set_result(
            snapshot(notify=True, notification_id="late", _review_image=jpeg())
        )
        rt.step()
        assert not rt.session.event_images and rt.hardware.notifications == 0
    finally:
        rt.close()
