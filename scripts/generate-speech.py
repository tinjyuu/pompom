"""Regenerate bundled fixed reminders on macOS with the installed Kyoko voice.

This writes audio files, without playback, credentials or external API calls.
"""

import subprocess
import sys
import tempfile
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pompom.speech import ASSETS, PHRASES  # noqa: E402

with tempfile.TemporaryDirectory(prefix="pompom-speech-") as tmp:
    for name, text in PHRASES.items():
        source = str(Path(tmp) / (name + ".aiff"))
        destination = str(ASSETS / ("speech-" + name + ".wav"))
        subprocess.run(["/usr/bin/say", "-v", "Kyoko", "-r", "175", "-o", source, text], check=True)
        subprocess.run(["/usr/bin/afconvert", "-f", "WAVE", "-d", "LEI16@24000", "-c", "1", source, destination], check=True)
        with wave.open(destination) as wav:
            if wav.getnframes() / wav.getframerate() < 0.5:
                raise RuntimeError("Speech synthesis produced empty audio; check macOS voice-service access")
        print(name)
