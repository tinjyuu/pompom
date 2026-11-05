from concurrent.futures import Future

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from support import Clock, snapshot
from test_runtime import Api, Hardware

from pompom.config import Settings, SettingsStore
from pompom.runtime import Runtime
from pompom.usage import UsageMeter
from pompom.web import attach_routes


class NoApi:
    def __init__(self):
        self.usage = UsageMeter()

    def start(self, *args):
        pytest.fail("Free mode must not start API sessions")

    def control(self, *args):
        pytest.fail("Free mode must not call API controls")

    def frame(self, *args):
        pytest.fail("Free mode must not send frames")


class IdleHardware(Hardware):
    def configure(self, settings):
        self.motion_enabled = settings.motion_enabled

    def set_idle_context(self, allowed, capture_in=None):
        self.idle_allowed = allowed
        self.capture_in = capture_in

    def capture(self):
        pytest.fail("Free mode must never capture an image")


def free_runtime(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    store.save(Settings(api_key="", duration_minutes=1))
    return Runtime(IdleHardware(), NoApi(), store, Clock())


def test_free_mode_no_key_no_consent_no_camera_no_api_and_no_deadline(tmp_path):
    rt = free_runtime(tmp_path)
    try:
        result = rt.control("free")
        assert result["state"] == "free" and result["unlimited"]
        assert result["remaining_seconds"] is None and result["session_id"] is None
        for elapsed in (0, 60, 3600, 86400, 86400 * 30):
            rt.session.clock.now += elapsed
            rt.step()
            assert rt.snapshot()["state"] == "free"
            assert rt.hardware.idle_allowed and rt.hardware.capture_in is None
            assert rt.pending is None and rt.api.usage.snapshot()["requests"] == 0
        with pytest.raises(ValueError, match="カメラ"):
            rt.preview()
        with pytest.raises(ValueError):
            rt.control("start", consent=True)
    finally:
        rt.close()


def test_free_pause_resume_and_end_remain_local(tmp_path):
    rt = free_runtime(tmp_path)
    try:
        rt.control("free")
        rt.step()
        assert rt.control("pause")["state"] == "free_paused"
        rt.session.clock.now += 86400
        rt.step()
        assert not rt.hardware.idle_allowed and not rt.hardware.motion_enabled
        assert rt.snapshot()["unlimited"]
        rt.control("resume")
        rt.step()
        assert rt.hardware.idle_allowed and rt.hardware.motion_enabled
        assert rt.control("end")["state"] == "ended"
        rt.step()
        assert not rt.hardware.idle_allowed and rt.api.usage.snapshot()["requests"] == 0
    finally:
        rt.close()


def test_free_settings_changes_preserve_unlimited_mode_and_pause(tmp_path):
    rt = free_runtime(tmp_path)
    try:
        rt.control("free")
        assert (
            rt.configure({"duration_minutes": 1, "audio_enabled": False})["state"]
            == "free"
        )
        rt.session.clock.now += 86400
        assert rt.snapshot()["state"] == "free"
        rt.control("pause")
        assert rt.configure({"audio_enabled": True})["state"] == "free_paused"
        assert not rt.hardware.motion_enabled
    finally:
        rt.close()


def test_late_paid_reply_cannot_move_or_restore_old_session_after_switch(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    store.save(Settings(api_key="test"))
    hw = Hardware()
    rt = Runtime(hw, Api(), store, Clock())
    try:
        rt.control("start", consent=True)
        future = Future()
        rt.pending, rt.pending_generation = future, rt.session.generation
        result = rt.control("free")
        assert result["state"] == "free" and result["session_id"] is None
        future.set_result(
            snapshot(
                notify=True,
                notification_id="late",
                phone_probability=1,
                observable_probability=1,
            )
        )
        rt.step()
        assert hw.notifications == 0 and rt.snapshot()["state"] == "free"
        assert rt.pending is None and hw.captures == 0
    finally:
        rt.close()


def test_free_web_controls_and_preview_contract(tmp_path):
    rt = free_runtime(tmp_path)
    try:
        app = FastAPI()
        attach_routes(app, lambda: rt)
        with TestClient(app) as client:
            headers = {"X-Focus-Control": "1"}
            assert client.post("/api/session/free", headers=headers, json={}).json()[
                "unlimited"
            ]
            assert (
                client.post("/api/preview", headers=headers, json={}).status_code == 400
            )
            assert (
                client.post("/api/session/pause", headers=headers, json={}).json()[
                    "state"
                ]
                == "free_paused"
            )
            assert (
                client.post("/api/session/resume", headers=headers, json={}).json()[
                    "state"
                ]
                == "free"
            )
            assert (
                client.post("/api/session/end", headers=headers, json={}).json()[
                    "state"
                ]
                == "ended"
            )
    finally:
        rt.close()
