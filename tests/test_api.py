import base64
import json
import stat
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from support import Clock

from pompom.api import ENDPOINT, ApiError, DirectApi, parse_decision
from pompom.config import Settings, SettingsStore


def decision(phone=0.95, observable=0.99):
    return {
        "answers": [
            {"type": "predicate", "name": "phone_use", "probability": phone},
            {"type": "predicate", "name": "observable", "probability": observable},
        ]
    }


def test_direct_endpoint_images_and_hold_without_cooldown():
    requests = []
    clock = Clock()

    def handler(request):
        requests.append(request)
        assert str(request.url) == ENDPOINT
        assert request.headers["authorization"] == "Bearer test-secret"
        body = json.loads(request.content)
        assert body["model"] == "gpt-6-luna"
        assert (
            body["input"][0]["content"][1]["image_url"]
            == "data:image/jpeg;base64," + base64.b64encode(b"jpeg").decode()
        )
        assert {q["name"] for q in body["questions"]} == {"phone_use", "observable"}
        return httpx.Response(200, json=decision())

    api = DirectApi(httpx.MockTransport(handler), clock)
    settings = Settings(api_key="test-secret")
    sid = api.start(settings)["session_id"]
    assert not api.frame(settings, sid, "first", b"jpeg")["notify"]
    clock.now += 1
    assert not api.frame(settings, sid, "second", b"jpeg")["notify"]
    clock.now += 1
    result = api.frame(settings, sid, "third", b"jpeg")
    assert result["notify"] and result["continuous_hold_ms"] == 2000
    assert result["cooldown_remaining_ms"] == 0
    assert not api.get(settings, sid)["notify"]
    clock.now += 1
    assert api.frame(settings, sid, "fourth", b"jpeg")["continuous_hold_ms"] == 0
    assert len(requests) == 4


@pytest.mark.parametrize(
    "bad",
    [
        None,
        {},
        {"answers": []},
        decision(True),
        decision(float("nan")),
        decision(2),
        {"refusal": "private"},
        {"answers": [decision()["answers"][0]] * 2},
    ],
)
def test_invalid_or_refused_decisions_fail_closed(bad):
    with pytest.raises(ApiError):
        parse_decision(bad)


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "unauthorized"),
        (403, "permission"),
        (404, "model_unavailable"),
        (429, "rate_limited"),
        (500, "api_error"),
    ],
)
def test_error_messages_never_echo_credentials_or_images(status, code):
    api = DirectApi(
        httpx.MockTransport(
            lambda _: httpx.Response(status, text="private-credential-image")
        )
    )
    s = Settings(api_key="private")
    sid = api.start(s)["session_id"]
    with pytest.raises(ApiError) as error:
        api.frame(s, sid, "one", b"jpeg")
    assert str(error.value) == code


def test_low_visibility_error_and_long_gap_reset_hold():
    clock = Clock()
    value = [decision()]
    api = DirectApi(
        httpx.MockTransport(lambda _: httpx.Response(200, json=value[0])), clock
    )
    s = Settings(api_key="test")
    sid = api.start(s)["session_id"]
    api.frame(s, sid, "1", b"jpeg")
    clock.now += 1
    value[0] = decision(1, 0.1)
    assert api.frame(s, sid, "2", b"jpeg")["state"] == "unknown"
    value[0] = decision()
    clock.now += 1
    assert api.frame(s, sid, "3", b"jpeg")["continuous_hold_ms"] == 0
    clock.now += 11
    assert not api.frame(s, sid, "4", b"jpeg")["notify"]
    value[0] = {}
    with pytest.raises(ApiError):
        api.frame(s, sid, "5", b"jpeg")
    value[0] = decision()
    clock.now += 1
    assert api.frame(s, sid, "6", b"jpeg")["continuous_hold_ms"] == 0


@pytest.mark.parametrize("stop", ["pause", "end", "deadline"])
def test_late_inference_after_stop_cannot_notify(stop):
    clock = Clock()
    entered, release = threading.Event(), threading.Event()

    def handler(_):
        entered.set()
        assert release.wait(3)
        return httpx.Response(200, json=decision())

    api = DirectApi(httpx.MockTransport(handler), clock)
    s = Settings(api_key="test", duration_minutes=1)
    sid = api.start(s)["session_id"]
    with ThreadPoolExecutor(1) as pool:
        pending = pool.submit(api.frame, s, sid, "one", b"jpeg")
        assert entered.wait(2)
        if stop == "deadline":
            clock.now += 61
        else:
            api.control(s, sid, stop)
        release.set()
        result = pending.result(timeout=2)
    assert not result["notify"]
    assert result["state"] == ("paused" if stop == "pause" else "ended")


def test_secret_storage_is_private_and_independent(tmp_path):
    store = SettingsStore(tmp_path / "pompom" / "settings.json")
    s = Settings(api_key="private-secret")
    store.save(s)
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert store.load() == s
    assert "private-secret" not in str(s.public())
    assert "private-secret" not in repr(s)
    assert SettingsStore().path.parent.name == "pompom"
    with pytest.raises(ValueError):
        SettingsStore.updated(s, {"api_url": "https://untrusted.example"})


def test_oversize_image_and_missing_key_never_reach_openai():
    api = DirectApi(httpx.MockTransport(lambda _: pytest.fail("no request")))
    sid = api.start(Settings())["session_id"]
    with pytest.raises(ApiError):
        api.frame(Settings(), sid, "1", b"x" * 1048577)
    with pytest.raises(ApiError):
        api.frame(Settings(), sid, "2", b"jpeg")
