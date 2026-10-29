"""מדיניות הסינון: מציונים → החלטה. פונקציות טהורות, בלי I/O — קל לבדוק.

רמות:
    0 — כבוי (בלי סינון תמונות)
    1 — מחמיר: כל תמונה של אישה/ילדה נחסמת, גם בלבוש צנוע, בכל גודל
    2 — בינוני: נשים בלבוש צנוע עוברות — אבל הפנים וכל עור חשוף שלהן נצבעים באפור לפי צורת הגוף —
        גם חשיפה קטנה (רק בטן, רק מעל החזה); אישה קטנה בתמונה נצבעת כולה ("צללית"); חשופה ברובה
        (seg_block, 70% עור) — התמונה נחסמת כולה; עירום נחסם
    3 — בסיסי: רק עירום מפורש נחסם

החישוב בשלבים, כל שלב רק כשצריך:
  1. ציוני SigLIP2 (זול, לכל תמונה).
  2. גלאי NudeNet (NEED_DETECTOR) — כשהציונים באזור הספק: עירום? פנים של אישה?
  3. סגמנטציה (NEED_SEGMENT): מסכה מדויקת של העור החשוף, וכל אדם בתמונה בנפרד — אישה או גבר
     (women_among). ברמה 2: צובעים רק נשים — גבר לא נצבע אף פעם, גם באותה תמונה עם אישה.
     ברמה 1: כשהציון של התמונה כולה לא חד-משמעי, אישה אחת שנמצאה — והתמונה נחסמת (אישה קטנה
     בתמונה גדולה "נבלעת" בציון הכללי, ולכן נבדקת לבד).

תמונה קטנה (small — הצלע הקצרה עד SMALL_IMAGE_PX): הסגמנטציה מתקשה למצוא בה אנשים. כשהיא לא
מצאה אף אחד אבל הציונים מצביעים על אישה — חוסמים (מלבן אפור), במקום להעביר בלי בדיקה.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from .segment import SegStats

Detection = tuple[str, float, int, int, int, int]   # תווית, ציון, x, y, w, h
Box = tuple[int, int, int, int]

EXPLICIT = frozenset({
    "FEMALE_BREAST_EXPOSED", "FEMALE_GENITALIA_EXPOSED", "MALE_GENITALIA_EXPOSED",
    "BUTTOCKS_EXPOSED", "ANUS_EXPOSED",
})
FEMALE_MARKERS = frozenset({
    "FACE_FEMALE", "FEMALE_BREAST_COVERED", "FEMALE_GENITALIA_COVERED",
    "FEMALE_BREAST_EXPOSED", "FEMALE_GENITALIA_EXPOSED",
})
FACES = frozenset({"FACE_FEMALE", "FACE_MALE"})
FEMALE_FACE = frozenset({"FACE_FEMALE"})
# אזורים מהגלאי שמשלימים את הסגמנטציה (נצבעים רק בתוך שטח האדם):
#   עור חשוף — תמיד; אזורים "מכוסים" (NudeNet מזהה כך גם חולצה רגילה!) — רק כשברור שזה בגד ים.
PAINT_EXPOSED = frozenset({"BELLY_EXPOSED", "ARMPITS_EXPOSED"})
PAINT_COVERED = frozenset({"FEMALE_BREAST_COVERED", "FEMALE_GENITALIA_COVERED", "BUTTOCKS_COVERED"})

DETECTOR_MIN_SCORE = 0.35
EXPLICIT_MIN_SCORE = 0.45
FACE_MIN_SCORE = 0.3
SMALL_IMAGE_PX = 200            # תמונה ממוזערת (תוצאות חיפוש, רשת מוצרים, וריאציות צבע)
SILHOUETTE_PX = 128             # אישה שהצלע הארוכה שלה בתמונה קצרה מזה — נצבעת כולה (צללית)


class Action(StrEnum):
    PASS = "pass"
    BLOCK = "block"
    PAINT = "paint"
    NEED_DETECTOR = "need_detector"
    NEED_SEGMENT = "need_segment"


class PaintMode(StrEnum):
    SKIN = "skin"           # עור חשוף לפי מסכת הסגמנטציה
    BODY = "body"           # כל הגוף חוץ מפנים ושיער (בגד ים / לבוש חשוף מאוד)
    BOXES = "boxes"         # מלבנים מהגלאי — רק כשאין מסכה (הסגמנטציה לא מצאה אדם / לא זמינה)


@dataclass(frozen=True, slots=True)
class Thresholds:
    person: float = 0.5
    female_strict: float = 0.5      # רמה 1: מעל זה — חסימה מיידית
    female_check: float = 0.2       # רמה 1: בין זה ל-strict — מאמתים עם NudeNet. רמה 2: שער לסגמנטציה
    female: float = 0.5             # אישה בתמונה (כיסוי 90%)
    immodest: float = 0.5           # לבוש לא צנוע (כיסוי 85%)
    immodest_check: float = 0.2     # רמה 2: שער לסגמנטציה — נמוך מ-immodest כדי לא לפספס
    immodest_high: float = 0.85     # בגד ים ברור
    immodest_body: float = 0.9      # רמה 2: ציון כזה + "אזור מכוסה" מהגלאי = בגד ים → כל הגוף
    nsfw_check: float = 0.15        # מעל זה — מאמתים עירום עם NudeNet
    nsfw_block: float = 0.9         # מעל זה — חסימה בלי אימות
    seg_skin: float = 0.005         # עור גלוי (בלי פנים) מתוך שטח האישה — מעל זה צובעים עור. כמעט כל עור: גם
                                    # חשיפה קטנה (בטן, מעל החזה) נצבעת. מתחת לזה — רעש של המודל (כמה פיקסלים)
    seg_body: float = 0.4           # מעל זה — חשוף מאוד: צובעים את כל הגוף (כויל על COCO: 50% מ"בגד ים", 1% מ"לבוש מלא")
    seg_block: float = 0.7          # מעל זה — חשופה ברובה: לא צובעים, התמונה נחסמת כולה
    seg_min_person: float = 0.004   # מתחת לזה הסגמנטציה "לא מצאה אדם"
    female_small: float = 0.25      # תמונה ממוזערת שהסגמנטציה לא מצאה בה אף אדם: מעל זה — חוסמים
    # רמה 2 — מי מהאנשים אישה (צובעים רק נשים). כויל על COCO val2017 (תמונות של אדם אחד + "גבר ואישה"):
    # נשים שנשארות לצביעה: 98.5% כמו קודם; גברים שנצבעו: 13.7% במקום 43% (docs/REVIEW.md)
    person_female: float = 0.4      # אדם עם ראש גלוי, לפי החלק העליון שלו בלבד: מעל זה — אישה
    headless_female: float = 0.3    # אף אחד לא מראה ראש (גב, ראש מחוץ לתמונה): לפי התמונה כולה — מעל זה כולם "אישה"

    @classmethod
    def from_dict(cls, d: dict[str, float]) -> Thresholds:
        known = {k: float(v) for k, v in d.items() if k in cls.__dataclass_fields__}
        t = cls(**known)
        if "immodest_check" not in known and "immodest" in known:
            # ראשים ישנים בלי סף שער מפורש: חצי מסף הכיסוי-85%
            t = cls(**{**known, "immodest_check": round(known["immodest"] / 2, 4)})
        return t


@dataclass(frozen=True, slots=True)
class Decision:
    action: Action
    reason: str
    boxes: tuple[Box, ...] = field(default=())      # אזורים נוספים לצביעה (מהגלאי)
    mode: PaintMode = PaintMode.SKIN
    keep: tuple[Box, ...] = field(default=())       # אזורים שלא ייצבעו (כרגע לא בשימוש — גם הפנים נצבעות)
    fill: tuple[Box, ...] = field(default=())       # נשים קטנות — כל הדמות נצבעת (צללית)


def _parts(dets: list[Detection], labels: frozenset[str], min_score: float) -> list[Detection]:
    return [d for d in dets if d[0] in labels and d[1] >= min_score]


def _inside(d: Detection, boxes: tuple[Box, ...]) -> bool:
    cx, cy = d[2] + d[4] / 2, d[3] + d[5] / 2
    return any(x <= cx <= x + w and y <= cy <= y + h for x, y, w, h in boxes)


def women_among(female: list[float], headed: list[bool], image_female: float, t: Thresholds) -> list[bool]:
    """מי מהאנשים בתמונה אישה (ברמה 2 צובעים רק נשים).

    female[i]: ציון "אישה" של האדם ה-i — החלק העליון שלו בלבד (‎-1 = לא סווג).
    headed[i]: רואים לו ראש (פנים/שיער). רק לחלק כזה יש מראה של אישה או גבר; חלק בלי ראש
    (יד, רגל, אדם שהראש שלו מחוץ לתמונה) הולך לפי האחרים: אם יש בתמונה אישה — גם הוא נצבע.
    פספוס של אישה גרוע יותר מצביעה מיותרת של גבר — ולכן:
      * הספים כוילו כך שכמעט אף אישה לא נופלת (person_female, headless_female), ו
      * כשכל התמונה בבירור של אישה (female_strict) ואף אחד לא סווג כאישה — המתאים ביותר נחשב אישה.
    את הפנים מהגלאי לא משתפים: בבדיקה על COCO הוא סימן "פנים של אישה" כמעט באותה תדירות על גברים.
    """
    judged = [i for i, (f, h) in enumerate(zip(female, headed, strict=True)) if h and f >= 0]
    woman = [False] * len(female)
    for i in judged:
        woman[i] = female[i] >= t.person_female
    if judged and not any(woman[i] for i in judged) and image_female >= t.female_strict:
        woman[max(judged, key=lambda i: female[i])] = True
    any_woman = any(woman[i] for i in judged)
    for i in range(len(female)):
        if i not in judged:
            woman[i] = any_woman if judged else image_female >= t.headless_female
    return woman


def _boxes(dets: list[Detection]) -> tuple[Box, ...]:
    return tuple((x, y, w, h) for _, _, x, y, w, h in dets)


def decide(level: int, s: dict[str, float], t: Thresholds,
           dets: list[Detection] | None = None, seg: SegStats | None = None, small: bool = False) -> Decision:
    """s: ציונים לפי שם (person/female/immodest/nsfw). dets: תוצאות NudeNet, אם כבר חושבו.
    seg: סטטיסטיקות הסגמנטציה, אם כבר חושבה. small: תמונה ממוזערת (ראו SMALL_IMAGE_PX)."""
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
        return _level2(s, t, dets, seg, small)

    # --- רמה 1 ---
    if s["female"] >= t.female_strict:
        return Decision(Action.BLOCK, f"female {s['female']:.2f}")
    # גבר עובר תמיד (גם בלי חולצה) — הבדיקות כאן תלויות בציון "אישה"
    if s["female"] < t.female_check:
        return Decision(Action.PASS, "clean")
    if s["immodest"] >= t.immodest_high:
        return Decision(Action.BLOCK, f"immodest {s['immodest']:.2f}")
    # ציון לא חד-משמעי: קודם הגלאי (זול) — פנים / גוף של אישה; ואז כל אדם בתמונה בנפרד
    if dets is None:
        return Decision(Action.NEED_DETECTOR, "female check")
    if _parts(dets, FEMALE_MARKERS, DETECTOR_MIN_SCORE):
        return Decision(Action.BLOCK, "female (detector)")
    if seg is None:
        return Decision(Action.NEED_SEGMENT, "female check (people)")
    if seg.person >= t.seg_min_person:
        return Decision(Action.BLOCK, f"female (person {max((p.female for p in seg.people if p.woman), default=-1):.2f})")
    if seg.others < t.seg_min_person and (s["female"] >= t.female or (small and s["female"] >= t.female_small)):
        # אף אדם לא נמצא (איור, תקריב, תמונה קטנה) — לפי הציון של התמונה
        return Decision(Action.BLOCK, f"female {s['female']:.2f} (no person found)")
    return Decision(Action.PASS, "clean")


def _level2(s: dict[str, float], t: Thresholds, dets: list[Detection] | None,
            seg: SegStats | None, small: bool = False) -> Decision:
    # שער רחב בכוונה (כיסוי ~97% לאישה): את ההחלטה האמיתית — יש עור גלוי? — עושה הסגמנטציה.
    # בלי שער של "לבוש לא צנוע": חשיפה קטנה (רק בטן, רק מעל החזה) לא מזיזה את הציון של התמונה כולה
    if s["female"] < t.female_check:
        return Decision(Action.PASS, "clean")
    if dets is None:
        return Decision(Action.NEED_DETECTOR, "immodest check")    # קודם: אין עירום מפורש?
    if seg is None:
        return Decision(Action.NEED_SEGMENT, "segment")

    # רק מה שהגלאי מצא על נשים (כשיודעים איפה הן) — עור חשוף של גבר לא מפעיל צביעה
    mine = dets if not seg.people else [d for d in dets if _inside(d, seg.women)]
    # בגד ים / הלבשה: ציון גבוה מאוד, או ציון גבוה + הגלאי רואה אזורים "מכוסים" (בגד צמוד)
    swimwear = s["immodest"] >= t.immodest_body and bool(_parts(mine, PAINT_COVERED, 0.5))
    very = s["immodest"] >= t.immodest_high or swimwear
    exposed = _parts(mine, PAINT_EXPOSED, DETECTOR_MIN_SCORE)
    covered = _parts(mine, PAINT_COVERED, DETECTOR_MIN_SCORE) if very else []
    # פנים מהגלאי (על הנשים) — נצבעות גם כשהסגמנטציה סימנה אותן כרקע / שיער (משלים את המסכה)
    faces = _parts(mine, FACES, FACE_MIN_SCORE)

    if seg.person >= t.seg_min_person:          # יש נשים בתמונה (שטח הנשים בלבד)
        # חשופה ברובה — לא "מתקנים" בצביעה: התמונה כולה נחסמת
        if seg.skin >= t.seg_block:
            return Decision(Action.BLOCK, f"exposed skin={seg.skin:.2f}")
        # אישה קטנה בתמונה — כל הדמות נצבעת (בגודל כזה העור והבגד לא נבדלים במדויק)
        tiny = tuple(p.box for p in seg.people if p.woman and max(p.box[2], p.box[3]) < SILHOUETTE_PX)
        # "חשוף מאוד" לפי הציון של כל התמונה — רק אם גם לאישה עצמה יש עור גלוי
        # (אחרת הציון בא ממישהו אחר בתמונה, ואין סיבה לצבוע את כל הגוף של אישה בלבוש מלא)
        if (very and seg.skin >= t.seg_skin) or seg.skin >= t.seg_body:
            return Decision(Action.PAINT, f"body skin={seg.skin:.2f} immodest={s['immodest']:.2f}",
                            _boxes(exposed + covered + faces), PaintMode.BODY, (), tiny)
        # תמיד צובעים: הפנים של כל אישה, וכל עור גלוי (גם מעט)
        return Decision(Action.PAINT, f"skin={seg.skin:.2f} immodest={s['immodest']:.2f}"
                        + (f" silhouettes={len(tiny)}" if tiny else ""),
                        _boxes(exposed + faces), PaintMode.SKIN, (), tiny)
    if seg.others >= t.seg_min_person:          # אנשים — אבל אף אחד מהם לא אישה
        return Decision(Action.PASS, "men only")

    # הסגמנטציה לא מצאה אדם (איור, תקריב, תמונה קטנה, או שהמודל לא זמין) — חוזרים לגלאי,
    # ורק כשיש סימן לאישה (ציון, או פנים/גוף של אישה מהגלאי)
    markers = bool(_parts(dets, FEMALE_MARKERS, DETECTOR_MIN_SCORE))
    if small and (markers or s["female"] >= t.female_small):
        # בתמונה קטנה הסגמנטציה לא מוצאת אנשים — אי אפשר לצבוע במדויק, ולכן לא מעבירים
        return Decision(Action.BLOCK, f"female {s['female']:.2f} (small image)")
    if s["female"] < t.female and not markers:
        return Decision(Action.PASS, "no woman found")
    if very:
        return Decision(Action.BLOCK, f"immodest {s['immodest']:.2f}")
    # גם כאן הפנים של אישה לא נראות: מלבן על כל פנים שהגלאי זיהה כאישה
    paint = (exposed if s["immodest"] >= t.immodest else []) + _parts(dets, FEMALE_FACE, FACE_MIN_SCORE)
    if paint:
        return Decision(Action.PAINT, f"immodest {s['immodest']:.2f} (boxes)", _boxes(paint), PaintMode.BOXES)
    return Decision(Action.PASS, "clean")


def on_failure(level: int) -> Decision:
    """כשאי אפשר לבדוק (שגיאה, פורמט שלא מתפענח): ברמות 1–2 חוסמים, ברמה 3 מעבירים.

    רמה 2 מבטיחה "בלי לפספס" — תמונה שלא ניתן לבדוק לא עוברת כמו שהיא.
    (פורמטים שקשה לפענח, כמו AVIF, מומרים מראש בפרוקסי — ראו addon.py.)
    """
    if level in (1, 2):
        return Decision(Action.BLOCK, "unverifiable")
    return Decision(Action.PASS, "unverifiable")
