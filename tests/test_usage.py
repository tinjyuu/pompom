import json
import stat
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
from support import Clock
from test_api import decision

from pompom.api import ApiError, DirectApi
from pompom.config import Settings
from pompom.usage import UsageMeter


def test_usage_accumulates_real_reported_values_and_survives_restart(tmp_path):
    path = tmp_path / "usage.json"
    meter = UsageMeter(path)
    meter.record(True, {"input_tokens": 100, "output_tokens": 2, "total_tokens": 102})
    meter.record(True, {"input_tokens": 30, "output_tokens": 0, "total_tokens": 30})
    meter.record(False, None)
    result = UsageMeter(path).snapshot()
    assert result["requests"] == 3 and result["successful_requests"] == 2
    assert result["failed_requests"] == 1 and result["unreported_requests"] == 1
    assert (
        result["input_tokens"],
        result["output_tokens"],
        result["total_tokens"],
    ) == (130, 2, 132)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert set(json.loads(path.read_text())) == {
        "since",
        "requests",
        "successful_requests",
        "failed_requests",
        "usage_reported_requests",
        "input_tokens",
        "output_tokens",
        "total_tokens",
    }


def test_missing_or_invalid_usage_is_not_mistaken_for_zero_report(tmp_path):
    m = UsageMeter(tmp_path / "usage.json")
    for value in [
        None,
        {},
        {"input_tokens": True, "output_tokens": 0},
        {"input_tokens": -1, "output_tokens": 0},
        {"input_tokens": 1, "output_tokens": 2, "total_tokens": 1},
    ]:
        m.record(True, value)
    assert m.snapshot()["usage_reported_requests"] == 0
    assert m.snapshot()["unreported_requests"] == 5


def test_usage_counts_paid_late_reply_even_after_end(tmp_path):
    entered, release = threading.Event(), threading.Event()

    def handler(_):
        entered.set()
        assert release.wait(3)
        return httpx.Response(
            200,
            json=decision()
            | {"usage": {"input_tokens": 42, "output_tokens": 0, "total_tokens": 42}},
        )

    api = DirectApi(
        httpx.MockTransport(handler), Clock(), UsageMeter(tmp_path / "usage.json")
    )
    s = Settings(api_key="secret")
    sid = api.start(s)["session_id"]
    with ThreadPoolExecutor(1) as pool:
        pending = pool.submit(api.frame, s, sid, "one", b"private-image")
        assert entered.wait(2)
        api.control(s, sid, "end")
        release.set()
        assert not pending.result(timeout=2)["notify"]
    assert api.usage.snapshot()["requests"] == 1
    assert api.usage.snapshot()["total_tokens"] == 42
    assert "secret" not in (tmp_path / "usage.json").read_text()
    assert "private-image" not in (tmp_path / "usage.json").read_text()


def test_failures_count_and_local_rejections_do_not():
    api = DirectApi(httpx.MockTransport(lambda _: httpx.Response(401)))
    s = Settings(api_key="test")
    sid = api.start(s)["session_id"]
    for settings, image in [(s, b"jpeg"), (Settings(), b"jpeg"), (s, b"x" * 1048577)]:
        try:
            api.frame(settings, sid, "one", image)
        except ApiError:
            pass
    assert api.usage.snapshot()["requests"] == 1
    assert api.usage.snapshot()["failed_requests"] == 1


def test_damaged_or_unwritable_usage_file_does_not_stop_app(tmp_path):
    path = tmp_path / "usage.json"
    path.write_text("invalid")
    m = UsageMeter(path)
    m.record(False, None)
    assert m.snapshot()["storage_error"]
    assert path.read_text() == "invalid"
    blocked = tmp_path / "file"
    blocked.write_text("blocked")
    m = UsageMeter(blocked / "usage.json")
    m.record(True, None)
    assert m.snapshot()["requests"] == 1 and m.snapshot()["storage_error"]
