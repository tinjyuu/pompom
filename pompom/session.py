"""Local safety/timer state; condition policy is provided by the on-robot Decisions adapter."""

import time
import uuid
from collections import OrderedDict, deque


class Session:
    def __init__(self, settings, clock=time.monotonic):
        self.settings = settings
        self.clock = clock
        self.state = "idle"
        self.generation = 0
        self.session_id = None
        self.deadline = 0.0
        self.remote = None
        self.error = None
        self.notification_count = 0
        self.events = deque(maxlen=500)
        self.event_images = OrderedDict()
        self.seen = set()
        self.detected_conditions = set()
        self.phone_in_use = False
        self.phone_put_down = False
        self.clear_observations = 0
        self.last_focus_sample = None

    def reset_focus(self):
        self.clear_observations = 0
        self.last_focus_sample = None

    def log(self, event, image=None, **values):
        event_id = str(uuid.uuid4())
        if image:
            self.event_images[event_id] = image
            while len(self.event_images) > 20:
                self.event_images.popitem(last=False)
        self.events.append(
            {
                "id": event_id,
                "time": time.time(),
                "device": "reachy_mini",
                "event": event,
                **values,
            }
        )

    def invalidate(self):
        self.generation += 1
        self.reset_focus()
        self.detected_conditions = set()
        self.phone_in_use = False
        self.phone_put_down = False
        if self.remote:
            self.remote = self.remote | {
                "notify": False,
                "notification_id": None,
                "continuous_hold_ms": 0,
                "consecutive_count": 0,
            }

    def begin(self):
        if self.state in ("starting", "running", "paused", "resuming"):
            raise ValueError("進行中のセッションを終了してください。")
        self.invalidate()
        self.state = "starting"
        self.session_id = None
        self.remote = None
        self.error = None
        self.deadline = self.clock() + self.settings.duration_minutes * 60
        self.notification_count = 0
        self.events.clear()
        self.event_images.clear()
        self.seen.clear()
        return self.generation

    def stop_local(self, state, event):
        self.invalidate()
        self.state = state
        self.log(event)

    def tick(self):
        if (
            self.last_focus_sample is not None
            and self.clock() - self.last_focus_sample
            >= max(10, self.settings.capture_interval_seconds + 5)
        ):
            self.reset_focus()
        if (
            self.state in ("starting", "running", "paused", "resuming")
            and self.clock() >= self.deadline
        ):
            self.stop_local("completed", "session_completed")
            return True
        return False

    def apply(self, snapshot, generation, from_frame=False):
        self.phone_put_down = False
        self.tick()
        if generation != self.generation:
            return None
        if from_frame and self.state != "running":
            return None
        self.error = None
        self.remote = snapshot | {"notify": False, "notification_id": None}
        if snapshot["state"] == "ended":
            self.stop_local(
                "completed" if snapshot["reason"] == "session_expired" else "ended",
                "session_completed"
                if snapshot["reason"] == "session_expired"
                else "session_ended",
            )
            return None
        if snapshot["state"] == "paused":
            self.state = "paused"
            return None
        results = snapshot.get("conditions", [snapshot])
        if from_frame:
            all_clear = True
            cleared = False
            for result in results:
                preset = result.get("condition_preset", self.settings.condition_preset)
                probability = result.get(
                    "condition_probability", result.get("phone_probability")
                )
                observable = result["observable_probability"]
                visible = observable is not None and observable >= 0.7
                is_clear = (
                    visible
                    and probability is not None
                    and probability <= 0.2
                    and result["reason"]
                    in ("phone_use_not_detected", "condition_not_detected")
                )
                all_clear = all_clear and is_clear
                if visible and probability is not None and probability >= 0.8:
                    self.detected_conditions.add(preset)
                elif is_clear and preset in self.detected_conditions:
                    self.detected_conditions.remove(preset)
                    cleared = True
            if all_clear:
                now = self.clock()
                # Each clear observation enables one dance level; this is not a
                # measurement of unobserved time between camera samples.
                self.clear_observations = min(3, self.clear_observations + 1)
                self.last_focus_sample = now
            else:
                self.reset_focus()
            self.phone_in_use = bool(self.detected_conditions)
            # Do not celebrate over another condition or an uncertain observation.
            self.phone_put_down = cleared and all_clear
        notified = False
        for result in results:
            nid = result.get("notification_id")
            if from_frame and result["notify"] and nid and nid not in self.seen:
                self.seen.add(nid)
                notified = notified or nid
                self.notification_count += 1
                preset = result.get("condition_preset", self.settings.condition_preset)
                self.log(
                    "phone_detected" if preset == "phone" else "condition_detected",
                    notification_id=nid,
                    condition_label=result.get(
                        "condition_label", self.settings.public()["condition"]["label"]
                    ),
                )
        return notified or None

    def fail(self, reason):
        self.reset_focus()
        self.error = reason
        if self.remote:
            self.remote = self.remote | {
                "notify": False,
                "notification_id": None,
                "state": "unknown",
                "reason": reason,
                "continuous_hold_ms": 0,
                "consecutive_count": 0,
            }
        self.log("api_failed", reason=reason)

    def snapshot(self):
        return {
            "state": self.state,
            "session_id": self.session_id,
            "remaining_seconds": max(0, self.deadline - self.clock())
            if self.state in ("starting", "running", "paused", "resuming")
            else 0,
            "notification_count": self.notification_count,
            "clear_observations": self.clear_observations,
            "server": self.remote,
            "error": self.error,
            "events": [
                event
                | (
                    {"image_url": f"/api/events/{event['id']}/image"}
                    if event["id"] in self.event_images
                    else {}
                )
                for event in self.events
            ],
        }
