"""מדיניות הסינון: מציונים → החלטה. פונקציות טהורות, בלי I/O — קל לבדוק.

רמות:
    0 — כבוי (בלי סינון תמונות)
    1 — מחמיר: כל תמונה של אישה/ילדה נחסמת, גם בלבוש צנוע
    2 — בינוני: נשים בלבוש צנוע עוברות; אזורים חשופים נצבעים בשחור; עירום נחסם
    3 — בסיסי: רק עירום מפורש נחסם

החישוב בשני שלבים: קודם ציוני SigLIP2 (זול, לכל תמונה). רק כשהציונים באזור
הספק, המדיניות מבקשת את גלאי NudeNet (NEED_DETECTOR) ומחליטה שוב עם התוצאות שלו.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

Detection = tuple[str, float, int, int, int, int]   # תווית, ציון, x, y, w, h

EXPLICIT = frozenset({
    "FEMALE_BREAST_EXPOSED", "FEMALE_GENITALIA_EXPOSED", "MALE_GENITALIA_EXPOSED",
    "BUTTOCKS_EXPOSED", "ANUS_EXPOSED",
})
FEMALE_MARKERS = frozenset({
    "FACE_FEMALE", "FEMALE_BREAST_COVERED", "FEMALE_GENITALIA_COVERED",
    "FEMALE_BREAST_EXPOSED", "FEMALE_GENITALIA_EXPOSED",
})
# אזורים שנצבעים בשחור ברמה 2 כשהלבוש לא צנוע (בגד ים, בטן חשופה וכו')
PAINT_PARTS = frozenset({
    "FEMALE_BREAST_COVERED", "FEMALE_GENITALIA_COVERED", "BUTTOCKS_COVERED",
    "BELLY_EXPOSED", "ARMPITS_EXPOSED",
})

DETECTOR_MIN_SCORE = 0.35
EXPLICIT_MIN_SCORE = 0.45


class Action(StrEnum):
    PASS = "pass"
    BLOCK = "block"
    PAINT = "paint"
    NEED_DETECTOR = "need_detector"


@dataclass(frozen=True, slots=True)
class Thresholds:
    person: float = 0.5
    female_strict: float = 0.5      # רמה 1: מעל זה — חסימה מיידית
    female_check: float = 0.2       # רמה 1: בין זה ל-strict — מאמתים עם NudeNet
    female: float = 0.5             # רמה 2: אישה בתמונה
    immodest: float = 0.5           # רמה 2: לבוש לא צנוע → צביעה
    immodest_high: float = 0.85     # חסימה גם בלי אזורים לצבוע
    nsfw_check: float = 0.15        # מעל זה — מאמתים עירום עם NudeNet
    nsfw_block: float = 0.9         # מעל זה — חסימה בלי אימות

    @classmethod
    def from_dict(cls, d: dict[str, float]) -> Thresholds:
        known = {k: float(v) for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)


@dataclass(frozen=True, slots=True)
class Decision:
    action: Action
    reason: str
    boxes: tuple[tuple[int, int, int, int], ...] = field(default=())


def _parts(dets: list[Detection], labels: frozenset[str], min_score: float) -> list[Detection]:
    return [d for d in dets if d[0] in labels and d[1] >= min_score]


def decide(level: int, s: dict[str, float], t: Thresholds,
           dets: list[Detection] | None = None) -> Decision:
    """s: ציונים לפי שם (person/female/immodest/nsfw). dets: תוצאות NudeNet, אם כבר חושבו."""
    if level <= 0:
        return Decision(Action.PASS, "level 0")

    # --- עירום מפורש: חוסמים בכל הרמות ---
    if s["nsfw"] >= t.nsfw_block:
        return Decision(Action.BLOCK, f"nsfw {s['nsfw']:.2f}")
    if dets is not None and _parts(dets, EXPLICIT, EXPLICIT_MIN_SCORE):
        return Decision(Action.BLOCK, "explicit (detector)")
    if s["nsfw"] >= t.nsfw_check and dets is None:
        return Decision(Action.NEED_DETECTOR, "nsfw check")

    if level >= 3:
        # לבוש חשוף מאוד (בגד ים וכו') — מוודאים עם NudeNet שאין עירום
        if s["immodest"] >= t.immodest_high and s["person"] >= t.person and dets is None:
            return Decision(Action.NEED_DETECTOR, "explicit check")
        return Decision(Action.PASS, "clean")

    if level == 2:
        if s["female"] >= t.female and s["immodest"] >= t.immodest:
            if dets is None:
                return Decision(Action.NEED_DETECTOR, "immodest check")
            parts = _parts(dets, PAINT_PARTS, DETECTOR_MIN_SCORE)
            if parts:
                return Decision(Action.PAINT, f"immodest {s['immodest']:.2f}",
                                tuple((x, y, w, h) for _, _, x, y, w, h in parts))
            if s["immodest"] >= t.immodest_high:
                return Decision(Action.BLOCK, f"immodest {s['immodest']:.2f}")
        return Decision(Action.PASS, "clean")

    # --- רמה 1 ---
    if s["female"] >= t.female_strict:
        return Decision(Action.BLOCK, f"female {s['female']:.2f}")
    # גבר עובר תמיד (גם בלי חולצה) — הבדיקות כאן תלויות בציון "אישה"
    if s["female"] >= t.female_check and s["immodest"] >= t.immodest_high:
        return Decision(Action.BLOCK, f"immodest {s['immodest']:.2f}")
    if s["female"] >= t.female_check and s["person"] >= t.person:
        if dets is None:
            return Decision(Action.NEED_DETECTOR, "female check")
        if _parts(dets, FEMALE_MARKERS, DETECTOR_MIN_SCORE):
            return Decision(Action.BLOCK, "female (detector)")
    return Decision(Action.PASS, "clean")


def on_failure(level: int) -> Decision:
    """כשאי אפשר לבדוק (שגיאה, תמונה שלא מתפענחת): ברמה 1 חוסמים, בשאר מעבירים."""
    if level == 1:
        return Decision(Action.BLOCK, "unverifiable")
    return Decision(Action.PASS, "unverifiable")
