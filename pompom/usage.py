"""Local lifetime API counters. No images, prompts or credentials are retained."""

import json
import os
import tempfile
import threading
from datetime import datetime, timezone

COUNTERS = (
    "requests",
    "successful_requests",
    "failed_requests",
    "usage_reported_requests",
    "input_tokens",
    "output_tokens",
    "total_tokens",
)


# Decisions API rates checked 2026-11-05. Output is free on this endpoint.
# https://developers.openai.com/api/docs/guides/decisions#pricing-and-availability
# Store integer billionths of USD to avoid rounding on each small image request.
COST_COUNTERS = (
    "estimated_cost_nanodollars",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "estimated_without_cache_tokens",
)
PRICING_DATE = "2026-11-05"


class UsageMeter:
    def __init__(self, path=None):
        self.path = path
        self.lock = threading.RLock()
        self.storage_error = False
        self.totals = dict.fromkeys(COUNTERS + COST_COUNTERS, 0)
        self.totals["since"] = datetime.now(timezone.utc).isoformat()
        if path is not None and path.exists():
            try:
                data = json.loads(path.read_text())
                if any(type(data[k]) is not int or data[k] < 0 for k in COUNTERS):
                    raise ValueError("Invalid usage counters")
                datetime.fromisoformat(data["since"])
                if (
                    data["requests"]
                    != data["successful_requests"] + data["failed_requests"]
                    or data["usage_reported_requests"] > data["requests"]
                ):
                    raise ValueError("Inconsistent usage counters")
                self.totals.update({k: data[k] for k in (*COUNTERS, "since")})
                if any(k in data for k in COST_COUNTERS):
                    if any(
                        type(data.get(k)) is not int or data[k] < 0
                        for k in COST_COUNTERS
                    ):
                        raise ValueError("Invalid cost counters")
                    if (
                        data["cached_input_tokens"]
                        + data["cache_write_input_tokens"]
                        + data["estimated_without_cache_tokens"]
                        > data["input_tokens"]
                    ):
                        raise ValueError("Inconsistent cost counters")
                    self.totals.update({k: data[k] for k in COST_COUNTERS})
                else:
                    # Old files retained only total input, so cache discounts/writes
                    # cannot be recovered. Label that portion as a standard-rate estimate.
                    self.totals["estimated_cost_nanodollars"] = (
                        data["input_tokens"] * 100
                    )
                    self.totals["estimated_without_cache_tokens"] = data["input_tokens"]
            except (OSError, ValueError, KeyError, TypeError):
                # Preserve the damaged file for recovery; do not silently overwrite it.
                self.storage_error = True

    def record(self, success, usage):
        with self.lock:
            self.totals["requests"] += 1
            self.totals["successful_requests" if success else "failed_requests"] += 1
            if isinstance(usage, dict):
                incoming, outgoing = (
                    usage.get("input_tokens"),
                    usage.get("output_tokens"),
                )
                total = usage.get(
                    "total_tokens",
                    incoming + outgoing
                    if type(incoming) is int and type(outgoing) is int
                    else None,
                )
                if (
                    all(type(v) is int and v >= 0 for v in (incoming, outgoing, total))
                    and total >= incoming + outgoing
                ):
                    self.totals["usage_reported_requests"] += 1
                    self.totals["input_tokens"] += incoming
                    self.totals["output_tokens"] += outgoing
                    self.totals["total_tokens"] += total
                    details = usage.get("input_tokens_details")
                    cached = (
                        details.get("cached_tokens")
                        if isinstance(details, dict)
                        else None
                    )
                    written = (
                        details.get("cache_write_tokens")
                        if isinstance(details, dict)
                        else None
                    )
                    if (
                        type(cached) is not int
                        or type(written) is not int
                        or min(cached, written) < 0
                        or cached + written > incoming
                    ):
                        cached = written = 0
                        self.totals["estimated_without_cache_tokens"] += incoming
                    self.totals["cached_input_tokens"] += cached
                    self.totals["cache_write_input_tokens"] += written
                    multiplier = 2 if incoming > 272000 else 1
                    self.totals["estimated_cost_nanodollars"] += multiplier * (
                        (incoming - cached - written) * 100
                        + cached * 10
                        + written * 125
                    )

            if self.path is not None and not self.storage_error:
                try:
                    self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    fd, name = tempfile.mkstemp(prefix=".usage-", dir=self.path.parent)
                    try:
                        with os.fdopen(fd, "w") as file:
                            json.dump(self.totals, file)
                        os.replace(name, self.path)
                    finally:
                        if os.path.exists(name):
                            os.unlink(name)
                except OSError:
                    self.storage_error = True

    def snapshot(self):
        with self.lock:
            return self.totals | {
                "unreported_requests": self.totals["requests"]
                - self.totals["usage_reported_requests"],
                "storage_error": self.storage_error,
                "estimated_cost_usd": self.totals["estimated_cost_nanodollars"]
                / 1_000_000_000,
                "pricing_date": PRICING_DATE,
                "pricing": {
                    "input_per_million_usd": 0.10,
                    "cached_input_per_million_usd": 0.01,
                    "cache_write_per_million_usd": 0.125,
                    "output_per_million_usd": 0,
                },
            }
