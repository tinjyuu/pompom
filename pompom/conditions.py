"""Visible, user-selected conditions and their detection requirements."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Condition:
    preset: str
    label: str
    description: str
    instructions: str
    visibility: str
    hold_ms: int

    @property
    def question_name(self):
        return "phone_use" if self.preset == "phone" else "condition_met"

    def public(self):
        return {
            "preset": self.preset,
            "label": self.label,
            "description": self.description,
            "hold_seconds": self.hold_ms / 1000,
        }


PRESETS = {
    "phone": Condition(
        "phone",
        "スマホ",
        "スマホを手に持っていたら反応する。机に置いたスマホは対象外。",
        "A person visibly holds a smartphone in a hand, even if not looking at or operating it. A smartphone resting on a desk is false. A book, computer use or an ambiguous object is not sufficient evidence. Do not infer purpose.",
        "A person and their hands or relevant work area are sufficiently visible to assess smartphone holding. False for an empty scene, hidden hands, covered camera, blur or darkness.",
        2000,
    ),
    "slouch": Condition(
        "slouch",
        "猫背",
        "背中が丸まり、肩や頭が前へ出た姿勢が続いたら反応する。上半身が見える位置で使う。",
        "The visible person has a clearly rounded upper back together with shoulders or head noticeably leaning forward relative to their torso. Require visible evidence from the upper-body silhouette. Looking down with only the eyes or briefly tilting the head is not sufficient. Do not diagnose a medical condition or infer pain, intent or concentration.",
        "The person's head, shoulders and enough of their torso or upper-back silhouette are clearly visible to assess the described posture. False if only a face is visible, the torso is occluded, camera perspective is ambiguous, or blur or darkness prevents assessment.",
        3000,
    ),
}


def resolve_condition(settings, preset=None):
    preset = preset or settings.condition_preset
    if preset in PRESETS:
        return PRESETS[preset]
    if preset != "custom" or not settings.custom_condition.strip():
        raise ValueError("自由入力の条件を入力してください。")
    return Condition(
        "custom",
        "自由入力",
        settings.custom_condition.strip(),
        settings.custom_condition.strip(),
        "A person and the body parts or objects needed to assess the supplied visible condition are clearly visible. False if the condition cannot be judged from this image alone, requires hidden information, a diagnosis, identity, intent, emotion, concentration or another internal mental state, or if the evidence is ambiguous, occluded, blurred or dark.",
        3000,
    )


def resolve_conditions(settings):
    return [
        resolve_condition(settings, preset)
        for preset in (settings.condition_presets or (settings.condition_preset,))
    ]
