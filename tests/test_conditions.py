import json
from concurrent.futures import Future
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from support import Clock, snapshot
from test_runtime import Api, Hardware

from pompom.api import DirectApi
from pompom.conditions import resolve_condition
from pompom.config import Settings, SettingsStore
from pompom.hardware import ReachyHardware
from pompom.runtime import Runtime
from pompom.session import Session
from pompom.web import attach_routes


def answer(probability=0.95, visible=0.99):
    return {
        "answers": [
            {"name": "condition_met", "type": "predicate", "probability": probability},
            {"name": "observable", "type": "predicate", "probability": visible},
        ]
    }


@pytest.mark.parametrize("preset", ["slouch", "custom"])
def test_condition_prompt_and_immediate_reaction(preset):
    clock = Clock()
    frames, bodies = [answer()], []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        assert body["questions"][1]["name"] == "condition_met"
        return httpx.Response(200, json=frames[0])

    s = Settings(
        api_key="test",
        condition_preset=preset,
        custom_condition="本を顔の前で持っている",
    )
    api = DirectApi(httpx.MockTransport(handler), clock)
    sid = api.start(s)["session_id"]
    for second in range(3):
        assert api.frame(s, sid, str(second), b"jpeg")["notify"]
        clock.now += 1
    result = api.frame(s, sid, "4", b"jpeg")
    assert result["notify"] and result["required_hold_ms"] == 0
    assert (
        result["condition_preset"] == preset and result["condition_probability"] == 0.95
    )
    if preset == "custom":
        assert "本を顔の前で持っている" in bodies[0]["questions"][1]["instructions"]
    else:
        assert "torso" in bodies[0]["questions"][0]["instructions"]
    frames[0] = answer(1, 0.1)
    clock.now += 1
    assert api.frame(s, sid, "5", b"jpeg")["state"] == "unknown"
    frames[0] = answer()
    clock.now += 1
    assert api.frame(s, sid, "6", b"jpeg")["notify"]


def test_preset_migration_validation_and_saved_custom_condition(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"duration_minutes":25,"api_key":"secret"}')
    store = SettingsStore(path)
    assert store.load().condition_preset == "phone"
    updated = store.updated(
        store.load(),
        {"condition_preset": "custom", "custom_condition": "  顔を手で隠している  "},
    )
    store.save(updated)
    assert store.load().custom_condition == "顔を手で隠している"
    assert "secret" not in str(updated.public())
    for changes in (
        {"condition_preset": "bad"},
        {"condition_preset": []},
        {"condition_preset": "custom", "custom_condition": "  "},
        {"custom_condition": None},
        {"custom_condition": "x" * 1001},
    ):
        with pytest.raises(ValueError):
            store.updated(Settings(), changes)
    assert resolve_condition(Settings(condition_preset="slouch")).hold_ms == 0


def test_condition_clear_celebration_and_focus_streak():
    clock = Clock()
    s = Session(Settings(condition_preset="slouch"), clock)
    s.begin()
    s.state = "running"
    s.apply(
        snapshot(
            state="suspected",
            reason="condition_suspected",
            condition_probability=0.9,
            observable_probability=0.99,
        ),
        s.generation,
        True,
    )
    clear = snapshot(
        reason="condition_not_detected",
        condition_probability=0.05,
        observable_probability=0.99,
    )
    s.apply(clear, s.generation, True)
    assert s.phone_put_down
    for _ in range(10):
        clock.now += 1
        s.apply(clear, s.generation, True)
    assert s.clear_observations == 3 and not s.phone_put_down
    s.apply(
        snapshot(
            state="unknown", condition_probability=None, observable_probability=0.1
        ),
        s.generation,
        True,
    )
    assert s.clear_observations == 0


def test_settings_change_discards_old_condition_notification(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    store.save(Settings(api_key="test"))
    hw = Hardware()
    rt = Runtime(hw, Api(), store)
    try:
        rt.control("start", consent=True)
        old = rt.session.generation
        future = Future()
        rt.pending, rt.pending_generation = future, old
        app = FastAPI()
        attach_routes(app, lambda: rt)
        with TestClient(app) as client:
            response = client.post(
                "/api/settings",
                headers={"X-Focus-Control": "1"},
                json={"condition_preset": "slouch"},
            )
            assert response.status_code == 200
            assert response.json()["settings"]["condition"]["label"] == "猫背"
        future.set_result(
            snapshot(
                notify=True,
                notification_id="late",
                phone_probability=1,
                observable_probability=1,
            )
        )
        rt.step()
        assert hw.notifications == 0
        assert rt.session.settings.condition_preset == "slouch"
        assert rt.session.state == "ended"
    finally:
        rt.close()


@pytest.mark.parametrize("preset", ["slouch", "custom"])
def test_non_phone_reaction_never_queues_phone_recording(preset):
    mini = SimpleNamespace(
        media=SimpleNamespace(stop_playing=lambda: None),
        get_current_head_pose=lambda: __import__("numpy").eye(4),
        get_present_antenna_joint_positions=lambda: [0, 0],
    )
    hardware = ReachyHardware(mini)
    hardware.fear = object()
    hardware.notify(Settings(condition_preset=preset, custom_condition="頭を手で隠す"))
    assert hardware.emotion["audio"] and not hardware.emotion["speech"]
    assert hardware.queued_speech is None
