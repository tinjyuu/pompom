"""Camera, quiet idle motion and the official 😨 fear1 recorded emotion."""

import io
import math
import random
import threading
import time
import wave
from pathlib import Path

from PIL import Image

from .speech import speech_path

EMOTIONS_DATASET = "pollen-robotics/reachy-mini-emotions-library"
FEAR_MOVE = "fear1"
HAPPY_MOVE = "cheerful1"
DANCE_MOVE = "dance1"
IDLE_MOVES = ("curious1", "attentive1", "inquiring1")
REMINDER_SOUNDS = tuple(
    Path(__file__).parent / "assets" / name
    for name in ("reminder-1.wav", "reminder-2.wav")
)


class ReachyHardware:
    def __init__(self, mini, clock=time.monotonic):
        self.mini = mini
        self.clock = clock
        self.camera_lock = threading.Lock()
        self.fear = None
        self.happy = None
        self.dance = None
        self.audio_enabled = True
        self.idle_behavior_enabled = True
        self.idle_moves = {}
        self.next_idle_reaction = self.clock() + random.uniform(12, 25)
        self.idle_reaction_allowed = False
        self.capture_in = None
        self.test_dance_until = 0.0
        self.last_reaction = None
        self.emotion = None
        self.returning = None
        self.motion = None
        self.audio_until = 0.0
        self.queued_speech = None
        self.idle_enabled = False
        self.idle_head = None
        self.idle_antennas = None
        self.idle_since = self.clock()
        self.focus_energy = 0.0
        self.target_energy = 0.0
        self.idle_phase = 0.0
        self.last_step = self.clock()

    def capabilities(self):
        actions = (["reminder"] if self.mini.media.audio is not None else []) + [
            "antenna_wiggle",
            "idle_motion",
        ]
        if self.fear is not None:
            actions.append("fear1")
        if self.happy is not None:
            actions.append(HAPPY_MOVE)
        if self.dance is not None:
            actions.append(DANCE_MOVE)
        return {
            "camera": self.mini.media.camera is not None,
            "actions": actions,
            "emotion_sounds": {
                name: bool(
                    move is not None
                    and move.sound_path
                    and Path(move.sound_path).is_file()
                )
                for name, move in (
                    (FEAR_MOVE, self.fear),
                    (HAPPY_MOVE, self.happy),
                    (DANCE_MOVE, self.dance),
                )
            },
            "idle_reactions": list(self.idle_moves),
        }

    def _idle_baseline(self):
        self.idle_head = self.mini.get_current_head_pose().copy()
        self.idle_antennas = list(self.mini.get_present_antenna_joint_positions())
        self.idle_since = self.clock()

    def prepare_camera(self):
        from reachy_mini.motion.recorded_move import RecordedMoves
        from reachy_mini.reachy_mini import (
            INIT_ANTENNAS_JOINT_POSITIONS,
            INIT_HEAD_POSE,
        )

        self.mini.enable_motors()
        self.mini.goto_target(
            head=INIT_HEAD_POSE, antennas=INIT_ANTENNAS_JOINT_POSITIONS, duration=2.0
        )
        self._idle_baseline()
        # Keep the camera's reference independent of servo settling or an interrupted emotion.
        self.idle_head = INIT_HEAD_POSE.copy()
        self.idle_antennas = list(INIT_ANTENNAS_JOINT_POSITIONS)
        try:
            # The daemon preloads this official library. Playback uses its actual trajectory.
            moves = RecordedMoves(EMOTIONS_DATASET, token=False)
        except Exception:
            return  # Capabilities explicitly report unavailable presets.
        for attr, name in (
            ("fear", FEAR_MOVE),
            ("happy", HAPPY_MOVE),
            ("dance", DANCE_MOVE),
        ):
            try:
                setattr(self, attr, moves.get(name))
            except Exception:
                setattr(self, attr, None)

        for name in IDLE_MOVES:
            try:
                move = moves.get(name)
                if move.sound_path is not None and Path(move.sound_path).is_file():
                    self.idle_moves[name] = move
            except Exception:
                pass

    def configure(self, settings):
        self.idle_enabled = settings.motion_enabled
        self.audio_enabled = settings.audio_enabled
        self.idle_behavior_enabled = settings.idle_behavior_enabled

    def set_idle_context(self, allowed, capture_in=None):
        self.idle_reaction_allowed = allowed
        self.capture_in = capture_in
        if not allowed:
            self.next_idle_reaction = self.clock() + random.uniform(12, 25)

    def _idle_reaction_step(self):
        if (
            not self.idle_reaction_allowed
            or not self.idle_enabled
            or not self.idle_behavior_enabled
            or not self.idle_moves
            or self.clock() < self.next_idle_reaction
            or self.emotion is not None
            or self.returning is not None
            or self.audio_until
            or self.queued_speech is not None
        ):
            return []
        candidates = [
            (name, move)
            for name, move in self.idle_moves.items()
            if self.capture_in is None or self.capture_in > move.duration + 3
        ]
        if not candidates:
            return []  # Keep the reference pose settled before the next camera sample.
        name, move = random.choice(candidates)
        self.next_idle_reaction = self.clock() + random.uniform(15, 35)
        self._start_emotion(move, name, self.audio_enabled)
        return [{"action": name, "status": "started"}]

    def is_dancing(self):
        return self.emotion is not None and self.emotion.get("kind") == DANCE_MOVE

    def _start_emotion(self, move, kind, audio, speech=False):
        self.cancel()
        self.returning = None
        self.last_reaction = kind
        self.emotion = {
            "move": move,
            "kind": kind,
            "started": self.clock(),
            "head": self.mini.get_current_head_pose().copy(),
            "antennas": list(self.mini.get_present_antenna_joint_positions()),
            "sound_started": False,
            "audio": audio,
            "speech": speech,
        }

    def set_focus_progress(self, seconds):
        # The caller maps each clear camera observation to a ten-point level.
        self.target_energy = min(1.0, max(0.0, math.floor(seconds / 10) / 3))
        if self.is_dancing() and self.clock() < self.test_dance_until:
            return []
        if seconds < 10 and self.is_dancing():
            self.cancel()
            return [{"action": DANCE_MOVE, "status": "cancelled"}]
        if (
            seconds >= 10
            and self.idle_enabled
            and self.dance is not None
            and self.emotion is None
            and self.returning is None
            and not self.audio_until
            and self.queued_speech is None
        ):
            idle_action = self._idle_reaction_step()
            if idle_action:
                return idle_action
            self._start_emotion(self.dance, DANCE_MOVE, self.audio_enabled)
            return [{"action": DANCE_MOVE, "status": "started"}]
        return []

    def capture(self):
        with self.camera_lock:
            frame = self.mini.media.get_frame()
        if frame is None:
            raise RuntimeError("camera_unavailable")
        image = Image.fromarray(frame[:, :, ::-1])
        image.thumbnail((640, 640))
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=75)
        return output.getvalue()

    def _play_sound(self, sound, duration=None):
        if self.mini.media.audio is None:
            raise RuntimeError("audio_unavailable")
        if duration is None:
            with wave.open(str(sound)) as wav:
                duration = wav.getnframes() / wav.getframerate()
        self.mini.media.play_sound(str(sound))
        self.audio_until = self.clock() + duration + 0.2

    def _play_reminder(self, presets=("phone",)):
        sound = (
            random.choice(REMINDER_SOUNDS)
            if set(presets) == {"phone"}
            else speech_path(presets)
        )
        self._play_sound(sound)

    def notify(self, settings):
        # A confirmed API notification must not restart a fear triggered by a dance interruption.
        if self.last_reaction == FEAR_MOVE and (
            self.emotion is not None
            or self.queued_speech is not None
            or self.audio_until
        ):
            return [{"action": FEAR_MOVE, "status": "already_playing"}]
        self.cancel()
        presets = settings.condition_presets or (settings.condition_preset,)
        speech = presets if settings.audio_enabled and settings.voice_enabled else ()
        results = []
        if settings.motion_enabled and self.fear is not None:
            self._start_emotion(
                self.fear,
                FEAR_MOVE,
                settings.audio_enabled,
                speech=speech,
            )
            results.append({"action": FEAR_MOVE, "status": "started"})
        elif settings.motion_enabled:
            results.append({"action": FEAR_MOVE, "status": "unavailable"})
        # Fear keeps its original sound and queues the Japanese reminder after it.
        if speech and self.emotion is None:
            try:
                self._play_reminder(speech)
                results.append({"action": "reminder", "status": "started"})
            except Exception:
                results.append({"action": "reminder", "status": "failed"})
        return results

    def test_dance(self, settings):
        if self.dance is None:
            raise ValueError("ダンスを読み込めませんでした。")
        self._start_emotion(self.dance, DANCE_MOVE, settings.audio_enabled)
        self.test_dance_until = self.clock() + self.dance.duration + 1.8
        return [{"action": DANCE_MOVE, "status": "started"}]

    def celebrate(self, settings):
        if self.happy is None:
            return [{"action": HAPPY_MOVE, "status": "unavailable"}]
        self._start_emotion(self.happy, HAPPY_MOVE, settings.audio_enabled)
        return [{"action": HAPPY_MOVE, "status": "started"}]

    def _emotion_step(self):
        import numpy as np
        from reachy_mini.utils.interpolation import linear_pose_interpolation

        e = self.emotion
        move = e["move"]
        t = self.clock() - e["started"]
        transition = 0.8
        first_head, first_antennas, first_body = move.evaluate(0)
        if t < transition:
            alpha = (1 - math.cos(math.pi * t / transition)) / 2
            head = linear_pose_interpolation(e["head"], first_head, alpha)
            antennas = np.array(e["antennas"]) * (1 - alpha) + first_antennas * alpha
            body = first_body * alpha
        elif t < transition + move.duration:
            if not e["sound_started"]:
                e["sound_started"] = True
                if e["audio"]:
                    if move.sound_path:
                        # Official presets may use OGG, not the bundled PCM WAV format.
                        self._play_sound(move.sound_path, duration=move.duration)
                        if e.get("speech"):
                            self.queued_speech = (self.audio_until, e["speech"])
                    elif e.get("speech"):
                        self._play_reminder(e["speech"])
            head, antennas, body = move.evaluate(
                min(t - transition, move.timestamps[-1] - 1e-6)
            )
        else:
            last_head, last_antennas, last_body = move.evaluate(
                move.timestamps[-1] - 1e-6
            )
            alpha = min(1, (t - transition - move.duration) / 1.0)
            alpha = (1 - math.cos(math.pi * alpha)) / 2
            head = linear_pose_interpolation(last_head, self.idle_head, alpha)
            antennas = (
                last_antennas * (1 - alpha) + np.array(self.idle_antennas) * alpha
            )
            body = last_body * (1 - alpha)
            if alpha >= 1:
                self.emotion = None
                self.idle_since = self.clock()
        self.mini.set_target(head=head, antennas=antennas, body_yaw=body)

    def step(self):
        now = self.clock()
        dt = min(0.1, max(0.0, now - self.last_step))
        self.last_step = now
        difference = self.target_energy - self.focus_energy
        self.focus_energy += max(-dt / 2, min(dt / 2, difference))
        if self.emotion is not None:
            self._emotion_step()
        elif self.returning is not None:
            import numpy as np
            from reachy_mini.utils.interpolation import linear_pose_interpolation

            start, head, antennas = self.returning
            alpha = min(1.0, max(0.0, now - start))
            alpha = (1 - math.cos(math.pi * alpha)) / 2
            self.mini.set_target(
                head=linear_pose_interpolation(head, self.idle_head, alpha),
                antennas=np.array(antennas) * (1 - alpha)
                + np.array(self.idle_antennas) * alpha,
                body_yaw=0,
            )
            if alpha >= 1:
                self.returning = None
                self.idle_phase = 0.0
        elif self.idle_enabled and self.idle_head is not None:
            self.idle_phase += dt * (1 + self.focus_energy)
            t = self.idle_phase
            # Build a livelier rhythm gradually, while preserving the camera's forward pose.
            head = self.idle_head.copy()
            head[2, 3] += (0.0015 + 0.0015 * self.focus_energy) * math.sin(
                2 * math.pi * t / 6
            )
            gain = 1 + 2 * self.focus_energy
            antennas = [
                self.idle_antennas[0] + 0.065 * gain * math.sin(2 * math.pi * t / 5),
                self.idle_antennas[1] + 0.055 * gain * math.sin(2 * math.pi * t / 7),
            ]
            self.mini.set_target(head=head, antennas=antennas)
        if self.audio_until and self.clock() >= self.audio_until:
            self.mini.media.stop_playing()
            self.audio_until = 0
        if self.queued_speech is not None and self.clock() >= self.queued_speech[0]:
            _, presets = self.queued_speech
            self.queued_speech = None
            self._play_reminder(presets)
        return self._idle_reaction_step()

    def cancel(self):
        self.test_dance_until = 0.0
        self.queued_speech = None
        had_emotion = self.emotion is not None
        self.emotion = None
        try:
            # Keep cancellation compatible with any pending legacy antenna gesture.
            if self.motion:
                _, baseline = self.motion
                self.motion = None
                self.mini.set_target(antennas=baseline)
            if had_emotion and self.idle_head is not None:
                self.returning = (
                    self.clock(),
                    self.mini.get_current_head_pose().copy(),
                    list(self.mini.get_present_antenna_joint_positions()),
                )
        finally:
            if self.audio_until:
                self.audio_until = 0
                self.mini.media.stop_playing()

    def shutdown(self):
        self.idle_enabled = False
        self.cancel()
        self.returning = None
