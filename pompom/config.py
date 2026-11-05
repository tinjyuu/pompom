"""Independent PomPom settings; OpenAI credentials never enter public state."""

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from .conditions import resolve_condition, resolve_conditions


@dataclass(frozen=True)
class Settings:
    api_key: str = field(default="", repr=False)
    duration_minutes: int = 25
    capture_interval_seconds: int = 60
    audio_enabled: bool = True
    voice_enabled: bool = True
    idle_behavior_enabled: bool = True
    motion_enabled: bool = True
    condition_preset: str = "phone"
    custom_condition: str = ""
    condition_presets: tuple[str, ...] = ()

    def public(self):
        return {k: v for k, v in asdict(self).items() if k != "api_key"} | {
            "api_key_configured": bool(self.api_key),
            "condition": resolve_condition(self).public(),
            "conditions": [c.public() for c in resolve_conditions(self)],
            "condition_presets": [c.preset for c in resolve_conditions(self)],
        }


class SettingsStore:
    def __init__(self, path=None):
        self.path = (
            path
            or Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
            / "pompom"
            / "settings.json"
        )

    def load(self):
        if not self.path.exists():
            return Settings(api_key=os.environ.get("OPENAI_API_KEY", ""))
        try:
            return self.updated(Settings(), json.loads(self.path.read_text()))
        except (OSError, ValueError, TypeError, AttributeError):
            raise ValueError("PomPomの設定ファイルを確認してください。") from None

    @staticmethod
    def updated(current, changes):
        if not isinstance(changes, dict) or changes.keys() - set(asdict(current)):
            raise ValueError("未対応の設定です。")
        changes = dict(changes)
        if "condition_preset" in changes and "condition_presets" not in changes:
            changes[
                "condition_presets"
            ] = ()  # Migrate legacy single-condition clients.
        s = replace(current, **changes)
        if (
            not isinstance(s.api_key, str)
            or len(s.api_key) > 512
            or any(c in s.api_key for c in "\r\n")
        ):
            raise ValueError("OpenAI APIキーの形式を確認してください。")
        if type(s.duration_minutes) is not int or not 1 <= s.duration_minutes <= 240:
            raise ValueError("集中時間は1〜240分で指定してください。")
        if (
            type(s.capture_interval_seconds) is not int
            or not 1 <= s.capture_interval_seconds <= 3600
        ):
            raise ValueError("画像の送信間隔は1〜3600秒で指定してください。")
        if any(
            type(v) is not bool
            for v in (
                s.audio_enabled,
                s.voice_enabled,
                s.idle_behavior_enabled,
                s.motion_enabled,
            )
        ):
            raise ValueError("通知設定はtrue/falseで指定してください。")
        if not isinstance(s.condition_preset, str) or s.condition_preset not in (
            "phone",
            "slouch",
            "custom",
        ):
            raise ValueError("反応する条件を選んでください。")
        if (
            not isinstance(s.custom_condition, str)
            or len(s.custom_condition) > 1000
            or any(ord(c) < 32 and c not in "\n\t" for c in s.custom_condition)
        ):
            raise ValueError("自由入力の条件は1000文字以内で入力してください。")
        presets = s.condition_presets
        if (
            not isinstance(presets, (list, tuple))
            or any(
                not isinstance(p, str) or p not in ("phone", "slouch", "custom")
                for p in presets
            )
            or len(set(presets)) != len(presets)
            or (
                "condition_presets" in changes
                and not presets
                and "condition_preset" not in changes
            )
        ):
            raise ValueError("反応する条件を1つ以上選んでください。")
        s = replace(
            s,
            custom_condition=s.custom_condition.strip(),
            motion_enabled=True,
            condition_presets=tuple(presets),
            condition_preset=presets[0] if presets else s.condition_preset,
        )
        resolve_conditions(s)
        return s

    def save(self, settings):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".settings-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w") as file:
                json.dump(asdict(settings), file)
                file.flush()
                os.fsync(file.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)
