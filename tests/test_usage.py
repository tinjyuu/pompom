import json
import stat
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
from support import Clock
from test_api import decision

from pompom.api import ApiError, DirectApi
from pompom.config import Settings
from pompom.usage import COST_COUNTERS, UsageMeter


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
    assert set(json.loads(path.read_text())) == set(COST_COUNTERS) | {
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


def test_decisions_cost_cache_breakdown_output_free_and_restart(tmp_path):
    import pytest

    path = tmp_path / "usage.json"
    m = UsageMeter(path)
    m.record(
        True,
        {
            "input_tokens": 10000,
            "output_tokens": 123,
            "input_tokens_details": {"cached_tokens": 2000, "cache_write_tokens": 3000},
        },
    )
    # 5k uncached * $0.10/M + 2k cache reads * $0.01/M + 3k writes * $0.125/M.
    assert m.snapshot()["estimated_cost_usd"] == pytest.approx(0.000895)
    m.record(
        False,
        {
            "input_tokens": 1000,
            "output_tokens": 0,
            "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
        },
    )
    result = UsageMeter(path).snapshot()
    assert result["estimated_cost_usd"] == pytest.approx(0.000995)
    assert result["cached_input_tokens"] == 2000
    assert result["cache_write_input_tokens"] == 3000
    assert result["estimated_without_cache_tokens"] == 0


def test_legacy_totals_are_migrated_without_resetting_counts(tmp_path):
    path = tmp_path / "usage.json"
    original = {
        "since": "2026-11-05T00:00:00+00:00",
        "requests": 215,
        "successful_requests": 179,
        "failed_requests": 36,
        "usage_reported_requests": 185,
        "input_tokens": 212915,
        "output_tokens": 0,
        "total_tokens": 212915,
    }
    path.write_text(json.dumps(original))
    m = UsageMeter(path)
    s = m.snapshot()
    assert s["estimated_cost_usd"] == 0.0212915
    assert s["estimated_without_cache_tokens"] == 212915
    assert s["unreported_requests"] == 30
    assert not s["storage_error"]
    m.record(True, {"input_tokens": 10, "output_tokens": 0})
    s = UsageMeter(path).snapshot()
    assert s["requests"] == 216 and s["since"] == original["since"]
    assert s["estimated_without_cache_tokens"] == 212925
    assert s["estimated_cost_usd"] == 0.0212925


def test_cost_invalid_cache_details_fall_back_and_unknown_usage_is_not_free():
    m = UsageMeter()
    m.record(
        True,
        {
            "input_tokens": 100,
            "output_tokens": 0,
            "input_tokens_details": {"cached_tokens": 100, "cache_write_tokens": 100},
        },
    )
    m.record(False, None)
    assert m.snapshot()["estimated_cost_usd"] == 0.00001
    assert m.snapshot()["estimated_without_cache_tokens"] == 100
    assert m.snapshot()["unreported_requests"] == 1
