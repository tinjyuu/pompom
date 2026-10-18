"""Independent PomPom settings; OpenAI credentials never enter public state."""

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    api_key: str = field(default="", repr=False)
    duration_minutes: int = 25
    audio_enabled: bool = True
    motion_enabled: bool = True

    def public(self):
        return {k: v for k, v in asdict(self).items() if k != "api_key"} | {
            "api_key_configured": bool(self.api_key)
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
        s = replace(current, **changes)
        if (
            not isinstance(s.api_key, str)
            or len(s.api_key) > 512
            or any(c in s.api_key for c in "\r\n")
        ):
            raise ValueError("OpenAI APIキーの形式を確認してください。")
        if type(s.duration_minutes) is not int or not 1 <= s.duration_minutes <= 240:
            raise ValueError("集中時間は1〜240分で指定してください。")
        if type(s.audio_enabled) is not bool or type(s.motion_enabled) is not bool:
            raise ValueError("通知設定はtrue/falseで指定してください。")
        return replace(s, motion_enabled=True)

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
