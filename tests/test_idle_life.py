from types import SimpleNamespace

import numpy as np
import pytest

from pompom.config import Settings, SettingsStore
from pompom.hardware import ReachyHardware


class Move:
    duration = 2
    timestamps = [0, 2]
    sound_path = "curious1.ogg"

    def evaluate(self, _):
        return np.eye(4), np.zeros(2), 0


def hardware(monkeypatch):
    monkeypatch.setattr("pompom.hardware.random.uniform", lambda low, high: 20)
    now, played = [0.0], []
    mini = SimpleNamespace(
        media=SimpleNamespace(
            audio=object(), play_sound=played.append, stop_playing=lambda: None
        ),
        get_current_head_pose=lambda: np.eye(4),
        get_present_antenna_joint_positions=lambda: [0, 0],
        set_target=lambda **kw: None,
    )
    h = ReachyHardware(mini, clock=lambda: now[0])
    h._idle_baseline()
    h.configure(Settings())
    h.idle_moves = {"curious1": Move()}
    return h, now, played


def test_idle_gesture_uses_its_original_sound_without_speech(monkeypatch):
    h, now, played = hardware(monkeypatch)
    h.set_idle_context(True)
    now[0] = 20
    actions = h.step()
    assert actions == [{"action": "curious1", "status": "started"}]
    now[0] += 0.81
    h.step()
    assert played == ["curious1.ogg"]
    assert not h.emotion["speech"] and h.queued_speech is None
    assert h.next_idle_reaction == 40


@pytest.mark.parametrize(
    "blocker", ["near_capture", "busy", "reaction", "speech", "disabled"]
)
def test_idle_never_overrides_detection_capture_or_explicit_disable(
    monkeypatch, blocker
):
    h, now, played = hardware(monkeypatch)
    h.set_idle_context(True, 1 if blocker == "near_capture" else 60)
    now[0] = 20
    if blocker == "busy":
        h.set_idle_context(False)
    if blocker == "reaction":
        h.fear = Move()
        h.notify(Settings(condition_preset="slouch"))
    if blocker == "speech":
        h.audio_until = 30
    if blocker == "disabled":
        h.configure(Settings(idle_behavior_enabled=False))
    assert h._idle_reaction_step() == []


def test_mute_preserves_motion_and_voice_off_does_not_mute_nonverbal_sound(monkeypatch):
    h, now, played = hardware(monkeypatch)
    h.configure(Settings(audio_enabled=False))
    h.set_idle_context(True)
    now[0] = 20
    h.step()
    now[0] += 0.81
    h.step()
    assert h.emotion is not None and played == []
    h.cancel()
    h.configure(Settings(voice_enabled=False))
    h.next_idle_reaction = 0
    h.returning = None
    h.step()
    now[0] += 0.81
    h.step()
    assert played == ["curious1.ogg"]


def test_idle_gets_a_turn_between_repeated_dances(monkeypatch):
    h, now, _ = hardware(monkeypatch)
    h.dance = Move()
    h.set_idle_context(True, 60)
    now[0] = 20
    assert h.set_focus_progress(10) == [{"action": "curious1", "status": "started"}]
    assert not h.is_dancing()


def test_dance_test_retains_original_audio(monkeypatch):
    h, now, played = hardware(monkeypatch)
    h.dance = Move()
    h.dance.sound_path = "dance1.ogg"
    h.test_dance(Settings())
    now[0] = 0.81
    h.step()
    assert played == ["dance1.ogg"]


def test_idle_setting_migration_and_validation(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    store.path.write_text("{}")
    assert store.load().idle_behavior_enabled
    store.save(store.updated(store.load(), {"idle_behavior_enabled": False}))
    assert not store.load().idle_behavior_enabled
    with pytest.raises(ValueError):
        store.updated(Settings(), {"idle_behavior_enabled": "yes"})


def test_manual_dance_is_not_cancelled_by_idle_runtime_progress(monkeypatch):
    h, now, played = hardware(monkeypatch)
    h.dance = Move()
    h.dance.sound_path = "dance1.ogg"
    h.test_dance(Settings())
    h.set_focus_progress(0)
    assert h.is_dancing()
    now[0] = 0.81
    h.step()
    assert played == ["dance1.ogg"]
    h.cancel()
    assert h.test_dance_until == 0 and not h.is_dancing()
