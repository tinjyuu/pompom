"""Bundled Japanese TTS reminders; playback never calls a speech API."""

from pathlib import Path

ASSETS = Path(__file__).parent / "assets"
PHRASES = {
    "slouch": "猫背になっているみたい。少し姿勢を戻そう。",
    "custom": "設定した条件に当てはまっているよ。少し気をつけよう。",
    "phone-slouch": "スマホを置いて、姿勢も少し戻そう。",
    "phone-custom": "スマホを置こう。設定した条件にも当てはまっているよ。",
    "slouch-custom": "少し姿勢を戻そう。設定した条件にも当てはまっているよ。",
    "phone-slouch-custom": "スマホを置いて、姿勢を戻そう。設定した条件にも当てはまっているよ。",
}
ORDER = ("phone", "slouch", "custom")


def speech_key(presets):
    return "-".join(p for p in ORDER if p in presets)


def speech_path(presets):
    key = speech_key(presets)
    if key not in PHRASES:
        raise ValueError("unsupported_speech_condition")
    return ASSETS / ("speech-" + key + ".wav")
