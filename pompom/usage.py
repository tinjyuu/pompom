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


class UsageMeter:
    def __init__(self, path=None):
        self.path = path
        self.lock = threading.RLock()
        self.storage_error = False
        self.totals = dict.fromkeys(COUNTERS, 0)
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
                self.totals = {k: data[k] for k in (*COUNTERS, "since")}
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
            }
