import json
import threading
from concurrent.futures import Future, ThreadPoolExecutor

import httpx
import pytest
from support import Clock, snapshot
from test_runtime import Api, Hardware

from pompom.api import ApiError, DirectApi
from pompom.config import Settings, SettingsStore
from pompom.runtime import Runtime
from pompom.session import Session
from pompom.usage import UsageMeter


def multi_answer(values):
    return {
        "answers": [
            {
                "name": f"{preset}_{suffix}",
                "type": "predicate",
                "probability": probability,
            }
            for preset, pair in values.items()
            for suffix, probability in zip(("met", "observable"), pair)
        ],
        "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
    }


def test_independent_visibility_and_immediate_reactions_in_one_request(tmp_path):
    clock, requests = Clock(), []
    values = {"phone": (0.95, 0.99), "slouch": (0.95, 0.99), "custom": (1, 0.1)}

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        assert len(body["input"][0]["content"]) == 2
        assert {q["name"] for q in body["questions"]} == {
            f"{p}_{suffix}" for p in values for suffix in ("met", "observable")
        }
        return httpx.Response(200, json=multi_answer(values))

    api = DirectApi(
        httpx.MockTransport(handler), clock, UsageMeter(tmp_path / "usage.json")
    )
    settings = Settings(
        api_key="test",
        condition_presets=("phone", "slouch", "custom"),
        custom_condition="本を顔の前で持つ",
    )
    sid = api.start(settings)["session_id"]
    frames = []
    for second in range(4):
        frames.append(api.frame(settings, sid, str(second), b"jpeg"))
        clock.now += 1
    assert all(
        [r["condition_preset"] for r in frame["conditions"] if r["notify"]]
        == ["phone", "slouch"]
        for frame in frames
    )
    assert all(f["conditions"][2]["state"] == "unknown" for f in frames)
    assert all(f["next_capture_after_ms"] == 60000 for f in frames)
    assert len(requests) == 4
    assert api.usage.snapshot()["requests"] == 4
    assert api.usage.snapshot()["total_tokens"] == 48


@pytest.mark.parametrize("bad", [[], ["phone", "phone"], ["bad"], "phone", [None]])
def test_invalid_selections(bad):
    with pytest.raises(ValueError):
        SettingsStore.updated(Settings(), {"condition_presets": bad})


def test_multi_settings_round_trip_and_legacy_migration(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    store.path.write_text('{"condition_preset":"slouch","api_key":"secret"}')
    assert store.load().public()["condition_presets"] == ["slouch"]
    updated = store.updated(
        store.load(),
        {
            "condition_presets": ["phone", "slouch", "custom"],
            "custom_condition": "本を持つ",
        },
    )
    store.save(updated)
    assert store.load() == updated
    assert "secret" not in str(updated.public())
    assert store.updated(updated, {"condition_preset": "slouch"}).public()[
        "condition_presets"
    ] == ["slouch"]
    with pytest.raises(ValueError):
        store.updated(Settings(), {"condition_presets": ["phone", "custom"]})


def condition_result(preset, probability, visibility=1, **extra):
    return snapshot(
        condition_preset=preset,
        condition_probability=probability,
        observable_probability=visibility,
        reason="phone_use_not_detected"
        if preset == "phone"
        else "condition_not_detected",
        **extra,
    )


def test_focus_requires_every_condition_clear_and_deduplicates_notifications():
    clock = Clock()
    s = Session(Settings(condition_presets=("phone", "slouch")), clock)
    s.begin()
    s.state = "running"

    def apply(phone, slouch, visible=1, **extra):
        s.apply(
            snapshot(
                conditions=[
                    condition_result("phone", phone),
                    condition_result("slouch", slouch, visible, **extra),
                ]
            ),
            s.generation,
            True,
        )

    apply(0.9, 0.9)
    apply(0, 0.9, notify=True, notification_id="slouch")
    apply(0, 0.9, notify=True, notification_id="slouch")
    assert not s.phone_put_down and s.clear_observations == 0
    assert s.notification_count == 1
    apply(0, 0)
    assert s.phone_put_down
    for _ in range(10):
        clock.now += 1
        apply(0, 0)
    assert s.clear_observations == 3
    apply(0, 0, visible=0.1)
    assert s.clear_observations == 0


@pytest.mark.parametrize("stop", ["pause", "end", "deadline"])
def test_multi_late_results_after_stop_are_ignored(tmp_path, stop):
    clock = Clock()
    entered, release = threading.Event(), threading.Event()

    def handler(_):
        entered.set()
        assert release.wait(3)
        return httpx.Response(
            200, json=multi_answer({"phone": (1, 1), "slouch": (1, 1)})
        )

    api = DirectApi(
        httpx.MockTransport(handler), clock, UsageMeter(tmp_path / "usage.json")
    )
    settings = Settings(
        api_key="test", duration_minutes=1, condition_presets=("phone", "slouch")
    )
    sid = api.start(settings)["session_id"]
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(api.frame, settings, sid, "frame", b"jpeg")
        assert entered.wait(2)
        if stop == "deadline":
            clock.now += 61
        else:
            api.control(settings, sid, stop)
        release.set()
        result = future.result(2)
    assert result["state"] == ("paused" if stop == "pause" else "ended")
    assert not result["notify"] and all(not r["notify"] for r in result["conditions"])
    assert api.usage.snapshot()["requests"] == 1


def test_missing_one_condition_fails_closed_and_resets_all(tmp_path):
    values = [{"phone": (1, 1), "slouch": (1, 1)}]
    clock = Clock()
    api = DirectApi(
        httpx.MockTransport(
            lambda _: httpx.Response(200, json=multi_answer(values[0]))
        ),
        clock,
        UsageMeter(tmp_path / "usage.json"),
    )
    settings = Settings(api_key="test", condition_presets=("phone", "slouch"))
    sid = api.start(settings)["session_id"]
    api.frame(settings, sid, "1", b"jpeg")
    clock.now += 1
    values[0] = {"phone": (1, 1)}
    with pytest.raises(ApiError):
        api.frame(settings, sid, "2", b"jpeg")
    values[0] = {"phone": (1, 1), "slouch": (1, 1)}
    clock.now += 1
    result = api.frame(settings, sid, "3", b"jpeg")
    assert all(r["continuous_hold_ms"] == 0 for r in result["conditions"])


@pytest.mark.parametrize(
    "trigger, dancing", [("slouch", False), ("phone", False), ("phone", True)]
)
def test_runtime_selects_recording_from_trigger_not_first_selection(
    tmp_path, trigger, dancing
):
    class RecordingHardware(Hardware):
        def notify(self, settings):
            self.reaction = settings.condition_preset
            return super().notify(settings)

        def is_dancing(self):
            return dancing

    store = SettingsStore(tmp_path / "settings.json")
    store.save(Settings(api_key="test", condition_presets=("phone", "slouch")))
    hw, api = RecordingHardware(), Api()
    runtime = Runtime(hw, api, store)
    try:
        runtime.control("start", consent=True)
        future = Future()
        runtime.pending, runtime.pending_generation = future, runtime.session.generation
        results = [
            condition_result(
                p,
                1 if p == trigger else 0,
                notify=p == trigger and not dancing,
                notification_id=p if p == trigger and not dancing else None,
            )
            for p in ("phone", "slouch")
        ]
        future.set_result(snapshot(conditions=results))
        runtime.step()
        assert hw.reaction == trigger
    finally:
        runtime.close()
