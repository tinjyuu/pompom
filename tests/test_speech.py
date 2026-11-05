import itertools
import wave
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from support import snapshot
from test_runtime import Api, Hardware

from pompom.config import Settings, SettingsStore
from pompom.hardware import REMINDER_SOUNDS, ReachyHardware
from pompom.runtime import Runtime
from pompom.speech import PHRASES, speech_key, speech_path

COMBINATIONS = [
    p
    for n in range(1, 4)
    for p in itertools.combinations(("phone", "slouch", "custom"), n)
]


def mini(played):
    return SimpleNamespace(
        media=SimpleNamespace(
            audio=object(), play_sound=played.append, stop_playing=lambda: None
        ),
        get_current_head_pose=lambda: np.eye(4),
        get_present_antenna_joint_positions=lambda: [0, 0],
        set_target=lambda **kw: None,
    )


@pytest.mark.parametrize("presets", COMBINATIONS)
def test_each_detected_combination_plays_matching_local_speech(presets):
    played = []
    h = ReachyHardware(mini(played), clock=lambda: 0)
    h.notify(Settings(condition_preset=presets[0], condition_presets=presets))
    assert len(played) == 1
    path = Path(played[0])
    assert (
        path in REMINDER_SOUNDS
        if presets == ("phone",)
        else path == speech_path(presets)
    )
    with wave.open(str(path)) as wav:
        assert wav.getnchannels() == 1 and wav.getsampwidth() == 2
        assert wav.getframerate() == 24000
        assert 0.5 < wav.getnframes() / wav.getframerate() < 15
        assert (
            np.abs(
                np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16)
            ).max()
            > 100
        )


@pytest.mark.parametrize("audio,voice", [(False, False), (False, True), (True, False)])
def test_muted_or_voice_disabled_never_speaks(audio, voice):
    played = []
    h = ReachyHardware(mini(played))
    h.notify(
        Settings(condition_preset="slouch", audio_enabled=audio, voice_enabled=voice)
    )
    assert played == [] and h.queued_speech is None


def test_slouch_speech_follows_emotion_and_stop_discards_queue():
    played, now = [], [0.0]

    class Fear:
        duration = 2
        timestamps = [0, 2]
        sound_path = "fear.ogg"

        def evaluate(self, _):
            return np.eye(4), np.zeros(2), 0

    h = ReachyHardware(mini(played), clock=lambda: now[0])
    h._idle_baseline()
    h.fear = Fear()
    s = Settings(condition_preset="slouch")
    h.notify(s)
    now[0] = 0.81
    h.step()
    assert played == ["fear.ogg"]
    assert h.queued_speech[1] == ("slouch",)
    now[0] = 3.1
    h.step()
    assert played[-1] == str(speech_path(("slouch",)))
    h.cancel()
    h.notify(s)
    now[0] += 0.81
    h.step()
    count = len(played)
    h.cancel()
    now[0] += 10
    h.step()
    assert len(played) == count and h.queued_speech is None


@pytest.mark.parametrize("triggers", [("slouch",), ("phone", "slouch"), ("custom",)])
def test_runtime_passes_only_actual_detected_conditions_to_speech(tmp_path, triggers):
    class RecordingHardware(Hardware):
        def notify(self, settings):
            self.spoken = settings.condition_presets
            return super().notify(settings)

    store = SettingsStore(tmp_path / "settings.json")
    store.save(
        Settings(
            api_key="test",
            condition_presets=("phone", "slouch", "custom"),
            custom_condition="本を持つ",
        )
    )
    h = RecordingHardware()
    rt = Runtime(h, Api(), store)
    try:
        rt.control("start", consent=True)
        results = [
            snapshot(
                condition_preset=p,
                phone_probability=1 if p in triggers else 0,
                observable_probability=1,
                notify=p in triggers,
                notification_id=p if p in triggers else None,
            )
            for p in ("phone", "slouch", "custom")
        ]
        future = Future()
        future.set_result(snapshot(conditions=results))
        rt.pending, rt.pending_generation = future, rt.session.generation
        rt.step()
        assert h.spoken == triggers
    finally:
        rt.close()


def test_voice_setting_migrates_and_validates(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    store.path.write_text('{"audio_enabled":false}')
    assert store.load().voice_enabled and not store.load().audio_enabled
    s = store.updated(store.load(), {"voice_enabled": False})
    store.save(s)
    assert not store.load().voice_enabled
    with pytest.raises(ValueError):
        store.updated(s, {"voice_enabled": "true"})
    assert speech_key(("slouch", "phone")) == "phone-slouch"
    assert "猫背" in PHRASES["slouch"]
