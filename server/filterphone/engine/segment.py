"""סגמנטציה לרמה 2: לכל פיקסל — רקע, שיער, עור הגוף, עור הפנים, בגד או אביזר.

המודל: MediaPipe selfie-multiclass 256×256 (Google, Apache-2.0), בהמרה ל-ONNX.
בניגוד לגלאי חלקי הגוף (NudeNet), שמחזיר מלבנים לכמה אזורים בלבד, כאן יש מסכה
מדויקת של כל העור החשוף — ידיים, כתפיים, צוואר, גב, בטן ורגליים — כך שהצביעה
עוקבת אחרי צורת הגוף ולא "מלבן שחור במקום הלא נכון".

שני מעברים:
  1. התמונה כולה, מתוחה ל-256×256 (כמו ב-MediaPipe) — איפה יש אנשים.
  2. לכל אדם קטן (עד 3): חיתוך ריבועי סביבו והרצה נוספת. בלי זה, אדם שתופס
     עשירית מהתמונה מקבל מסכה של 25×25 פיקסלים ו"חצי יד" נשארת גלויה.

צובעים רק נשים: אחרי הסגמנטציה מוצאים כל אדם בנפרד (find_people), המנוע מסווג כל אחד
(אישה / גבר) לפי חיתוך סביבו, ואנשים שאינם נשים מסומנים MAN — לא נצבעים ולא נספרים
בחישוב "כמה עור גלוי" (keep_women + stats).

הכול כאן פונקציות טהורות (numpy + Pillow) — הרצת המודלים עצמה ב-models.py וב-service.py.
"""
from __future__ import annotations

import io
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageFilter

BG, HAIR, SKIN, FACE, CLOTHES, OTHER = range(6)
N_CLASSES = 6
MAN = 6                     # במפת התוויות: אדם שזוהה כגבר — לא נצבע ולא נספר
FORMAT = 2                  # גרסת SegStats/מפת התוויות בקאש (2 = הפרדה לנשים/גברים)

WORK_SIDE = 512             # רזולוציית מפת התוויות (הצלע הארוכה)
SKIN_MIN_P = 0.35           # עור "כמעט בטוח" כבר נחשב עור — עדיף לצבוע מעט יותר מאשר לפספס
MAX_CROPS = 3
CROP_MAX_AREA = 0.45        # אדם שתופס יותר מזה מהתמונה כבר מפורט מספיק במעבר הראשון
CROP_PAD = 0.18
GRID = 64                   # רשת גסה למציאת אנשים נפרדים
DILATE = 0.012              # הרחבת האזור הצבוע: 1.2% מהצלע הארוכה — שוליים בלי "קצוות" גלויים
MAX_PEOPLE = 4              # כמה אנשים מסווגים בנפרד (הגדולים); השאר — לפי מי שכן סווג / ציון התמונה
PERSON_TOP = 0.7            # לסיווג אישה/גבר: 70% העליונים של האדם (ראש + פלג גוף עליון), ריבוע הדוק בלי שוליים.
                            # נבדק על COCO מול חיתוך האדם כולו עם שוליים: AUC 0.996 מול 0.970 במקרים הקשים
HEAD_MIN_PX = 20            # "יש ראש" (פנים/שיער) — רק לחלק כזה יש טעם לסווג אישה/גבר;
HEAD_MIN_FRAC = 0.02        # יד או רגל לבד (חלק שהסגמנטציה הפרידה) לא נראית "אישה" גם כשהיא של אישה

Box = tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class Person:
    box: Box                # x, y, w, h בפיקסלים של התמונה
    female: float           # ציון "אישה" של החיתוך סביבו (‎-1 = לא סווג בנפרד — לפי ציון התמונה)
    woman: bool


@dataclass(frozen=True, slots=True)
class SegStats:
    skin: float             # עור גלוי (בלי פנים) מתוך שטח הנשים
    person: float           # שטח הנשים מתוך התמונה
    crops: int = 0
    others: float = 0.0     # שטח האנשים שזוהו כגברים (לא נצבעים) מתוך התמונה
    people: tuple[Person, ...] = ()

    @property
    def women(self) -> tuple[Box, ...]:
        return tuple(p.box for p in self.people if p.woman)

    def dump(self) -> dict:
        return {"v": FORMAT, "skin": round(self.skin, 4), "person": round(self.person, 4), "crops": self.crops,
                "others": round(self.others, 4),
                "people": [[*p.box, round(p.female, 3), int(p.woman)] for p in self.people]}

    @classmethod
    def load(cls, d: dict) -> SegStats | None:
        """None = נשמר בפורמט ישן (בלי הפרדה לנשים) — המנוע יחשב מחדש."""
        if d.get("v") != FORMAT:
            return None
        people = tuple(Person((int(x), int(y), int(w), int(h)), float(f), bool(wo))
                       for x, y, w, h, f, wo in d.get("people", []))
        return cls(float(d["skin"]), float(d["person"]), int(d.get("crops", 0)), float(d.get("others", 0.0)),
                   people)


def softmax(logits: np.ndarray) -> np.ndarray:
    e = np.exp(logits - logits.max(axis=-1, keepdims=True))
    return e / e.sum(axis=-1, keepdims=True)


def seg_input(im: Image.Image, size: int, box: tuple[int, int, int, int] | None = None) -> np.ndarray:
    """התמונה (או חיתוך ממנה) → (size, size, 3) בטווח [0, 1], כמו שהמודל מצפה."""
    if box is not None:
        im = im.crop(box)
    return np.asarray(im.resize((size, size), Image.Resampling.BILINEAR), np.float32) / 255.0


# ---------------------------------------------------------------- מציאת אנשים

def _label_components(grid: np.ndarray) -> tuple[np.ndarray, list[tuple[int, int, int, int, int]]]:
    """רכיבים קשירים (שכנות 8) ברשת בוליאנית קטנה.

    מחזיר מפת מזהים (0 = ריק, k = הרכיב ה-k לפי גודל, 1 = הגדול) ו-[(גודל, y0, x0, y1, x1)] באותו סדר.
    """
    h, w = grid.shape
    raw = np.zeros((h, w), np.int32)
    found = []
    for sy, sx in zip(*np.nonzero(grid), strict=True):
        if raw[sy, sx]:
            continue
        k = len(found) + 1
        raw[sy, sx] = k
        q = deque([(sy, sx)])
        n, y0, x0, y1, x1 = 0, sy, sx, sy, sx
        while q:
            y, x = q.popleft()
            n += 1
            y0, x0, y1, x1 = min(y0, y), min(x0, x), max(y1, y), max(x1, x)
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < h and 0 <= nx < w and grid[ny, nx] and not raw[ny, nx]:
                        raw[ny, nx] = k
                        q.append((ny, nx))
        found.append((n, int(y0), int(x0), int(y1), int(x1)))
    order = sorted(range(len(found)), key=lambda i: -found[i][0])
    remap = np.zeros(len(found) + 1, np.int32)
    for new_id, old in enumerate(order, 1):
        remap[old + 1] = new_id
    return remap[raw], [found[i] for i in order]


def _components(grid: np.ndarray) -> list[tuple[int, int, int, int, int]]:
    """רכיבים קשירים (שכנות 8) ברשת בוליאנית קטנה → [(גודל, y0, x0, y1, x1)], מהגדול לקטן."""
    return _label_components(grid)[1]


def square_box(box: tuple[float, float, float, float], width: int, height: int, pad: float) -> Box:
    """(x0, y0, x1, y1) → ריבוע עם שוליים בתוך התמונה — כדי שהמודל לא יקבל אדם מעוות."""
    bx0, by0, bx1, by1 = box
    side = max(bx1 - bx0, by1 - by0) * (1 + 2 * pad)
    side = min(max(side, 24.0), float(max(width, height)))
    cx, cy = (bx0 + bx1) / 2, (by0 + by1) / 2
    x0 = int(round(min(max(cx - side / 2, 0), max(width - side, 0))))
    y0 = int(round(min(max(cy - side / 2, 0), max(height - side, 0))))
    return x0, y0, min(width, x0 + int(round(side))), min(height, y0 + int(round(side)))


def person_boxes(probs: np.ndarray, width: int, height: int) -> list[tuple[int, int, int, int]]:
    """אנשים קטנים במעבר הראשון → ריבועי חיתוך (בפיקסלים של התמונה) למעבר השני."""
    s = probs.shape[0]
    cell = max(1, s // GRID)
    g = s // cell
    person = probs[: g * cell, : g * cell, BG] < 0.5
    coarse = person.reshape(g, cell, g, cell).any(axis=(1, 3))
    boxes = []
    for n, y0, x0, y1, x1 in sorted(_components(coarse), reverse=True)[:MAX_CROPS]:
        if n < 3:
            continue                                    # רעש
        fx0, fy0, fx1, fy1 = x0 / g, y0 / g, (x1 + 1) / g, (y1 + 1) / g
        if (fx1 - fx0) * (fy1 - fy0) > CROP_MAX_AREA:
            continue
        boxes.append(square_box((fx0 * width, fy0 * height, fx1 * width, fy1 * height), width, height, CROP_PAD))
    return boxes


def find_people(labels: np.ndarray, width: int, height: int) -> tuple[np.ndarray, list[Box], list[bool]]:
    """כל אדם במפת התוויות בנפרד (רכיבים קשירים ברשת גסה), מהגדול לקטן.

    מחזיר מפת מזהים בגודל labels (0 = לא אדם, k = האדם ה-k), תיבה (x, y, w, h) בפיקסלים של
    התמונה לכל אחד, והאם רואים לו ראש (פנים או שיער). שני אנשים שנוגעים זה בזה נחשבים אדם
    אחד; אדם שהסגמנטציה פיצלה (למשל רגל בנפרד) — כמה חלקים, ורק לחלק עם ראש יש מראה של אישה/גבר.
    """
    lh, lw = labels.shape
    cell = max(1, -(-max(lh, lw) // GRID))
    gh, gw = -(-lh // cell), -(-lw // cell)
    occ = np.zeros((gh * cell, gw * cell), bool)
    occ[:lh, :lw] = labels != BG
    ids_grid, comps = _label_components(occ.reshape(gh, cell, gw, cell).any(axis=(1, 3)))
    ids = np.repeat(np.repeat(ids_grid, cell, axis=0), cell, axis=1)[:lh, :lw]
    ids = np.where(labels != BG, ids, 0).astype(np.int16)
    sx, sy = width / lw, height / lh
    boxes = []
    for _, y0, x0, y1, x1 in comps:
        X0, Y0 = int(x0 * cell * sx), int(y0 * cell * sy)
        X1, Y1 = min(width, int(np.ceil((x1 + 1) * cell * sx))), min(height, int(np.ceil((y1 + 1) * cell * sy)))
        boxes.append((X0, Y0, max(1, X1 - X0), max(1, Y1 - Y0)))
    n = len(comps) + 1
    size = np.bincount(ids.ravel(), minlength=n)
    head = np.bincount(ids[(labels == FACE) | (labels == HAIR)], minlength=n)
    headed = [bool(head[k] >= max(HEAD_MIN_PX, HEAD_MIN_FRAC * size[k])) for k in range(1, n)]
    return ids, boxes, headed


def person_view_box(box: Box, width: int, height: int) -> Box:
    """החיתוך לסיווג אדם אחד (x0, y0, x1, y1): החלק העליון שלו, בריבוע הדוק — בלי אנשים אחרים מסביב."""
    x, y, w, h = box
    return square_box((x, y, x + w, y + max(1.0, h * PERSON_TOP)), width, height, 0.0)


def keep_women(labels: np.ndarray, ids: np.ndarray, women: set[int]) -> np.ndarray:
    """מסמן MAN את כל האנשים שאינם ברשימת הנשים (מזהים מ-find_people) — הם לא ייצבעו."""
    out = labels.copy()
    out[(ids > 0) & ~np.isin(ids, sorted(women))] = MAN
    return out


def analyze(model: Callable[[np.ndarray], np.ndarray], im: Image.Image) -> tuple[np.ndarray, SegStats]:
    """שני המעברים על תמונה אחת. model: (S, S, 3) → הסתברויות (S, S, 6)."""
    size = int(getattr(model, "size", 256))
    full = model(seg_input(im, size))
    crops = [(box, model(seg_input(im, size, box))) for box in person_boxes(full, *im.size)]
    labels = merge(full, crops, *im.size)
    return labels, stats(labels, len(crops))


# ---------------------------------------------------------------- איחוד והחלטה

def work_size(width: int, height: int, side: int = WORK_SIDE) -> tuple[int, int]:
    scale = min(1.0, side / max(width, height))
    return max(1, round(width * scale)), max(1, round(height * scale))


def _resize_probs(p: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """(S, S, C) → (h, w, C), בילינארי, ערוץ-ערוץ (Pillow במצב F)."""
    w, h = size
    return np.stack([np.asarray(Image.fromarray(np.ascontiguousarray(p[..., c], dtype=np.float32))
                                .resize((w, h), Image.Resampling.BILINEAR)) for c in range(p.shape[-1])],
                    axis=-1)


def _edge_weights(h: int, w: int, at_edge: tuple[bool, bool, bool, bool],
                  ramp: float = 0.12) -> np.ndarray:
    """משקל 1 במרכז החיתוך שיורד ל-0 בשוליים — מעבר חלק בין החיתוך לתמונה המלאה.

    at_edge: (שמאל, למעלה, ימין, למטה) — צד שנוגע בקצה התמונה לא דועך: אין שם הקשר
    שהמעבר הגס ראה והחיתוך לא.
    """
    def axis(n: int, lo_edge: bool, hi_edge: bool) -> np.ndarray:
        t = (np.arange(n, dtype=np.float32) + 0.5) / n
        lo = np.ones(n, np.float32) if lo_edge else np.clip(t / ramp, 0, 1)
        hi = np.ones(n, np.float32) if hi_edge else np.clip((1 - t) / ramp, 0, 1)
        return np.minimum(lo, hi)
    left, top, right, bottom = at_edge
    return np.minimum(axis(h, top, bottom)[:, None], axis(w, left, right)[None, :])[..., None]


def merge(full: np.ndarray, crops: list[tuple[tuple[int, int, int, int], np.ndarray]],
          width: int, height: int) -> np.ndarray:
    """הסתברויות המעבר הראשון + החיתוכים → מפת תוויות (uint8) ברזולוציית עבודה.

    בתוך חיתוך, התוצאה המפורטת שלו מחליפה את המעבר הגס (שבאדם קטן הוא "כתם"),
    עם מעבר הדרגתי בשוליים — שם לחיתוך חסר הקשר.
    """
    ww, wh = work_size(width, height)
    p = _resize_probs(full, (ww, wh))
    sx, sy = ww / width, wh / height
    for (x0, y0, x1, y1), probs in crops:
        X0, Y0 = int(round(x0 * sx)), int(round(y0 * sy))
        X1, Y1 = min(ww, max(X0 + 1, int(round(x1 * sx)))), min(wh, max(Y0 + 1, int(round(y1 * sy))))
        sub = _resize_probs(probs, (X1 - X0, Y1 - Y0))
        wgt = _edge_weights(Y1 - Y0, X1 - X0, (x0 <= 0, y0 <= 0, x1 >= width, y1 >= height))
        region = p[Y0:Y1, X0:X1]
        p[Y0:Y1, X0:X1] = wgt * sub + (1 - wgt) * region
    return labels_from_probs(p)


def labels_from_probs(p: np.ndarray) -> np.ndarray:
    lab = p.argmax(axis=-1).astype(np.uint8)
    skin = (p[..., SKIN] >= SKIN_MIN_P) & (p[..., SKIN] >= p[..., FACE])
    lab[skin] = SKIN
    return lab


def stats(labels: np.ndarray, crops: int = 0, people: tuple[Person, ...] = ()) -> SegStats:
    """כמה עור גלוי — מתוך שטח הנשים בלבד (גברים מסומנים MAN ולא נספרים)."""
    women = int(np.count_nonzero((labels != BG) & (labels != MAN)))
    skin = int(np.count_nonzero(labels == SKIN))
    men = int(np.count_nonzero(labels == MAN))
    return SegStats(skin / women if women else 0.0, women / labels.size, crops, men / labels.size, people)


# ---------------------------------------------------------------- שמירה בקאש

def encode_labels(labels: np.ndarray) -> bytes:
    """מפת תוויות → PNG קטן (בדרך כלל כמה KB) לשמירה בקאש."""
    buf = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(labels, dtype=np.uint8)).save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def decode_labels(data: bytes) -> np.ndarray | None:
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
        return np.asarray(im.convert("L"), dtype=np.uint8)
    except Exception:
        return None


# ---------------------------------------------------------------- צביעה


def region_mask(labels: np.ndarray, mode: str, size: tuple[int, int],
                extra_boxes: tuple[Box, ...] = (), keep_boxes: tuple[Box, ...] = ()) -> Image.Image:
    """המסכה לצביעה (L, 0/255) בגודל התמונה.

    mode="skin": עור חשוף (בלי פנים).  mode="body": כל הגוף חוץ מהפנים והשיער —
    לבגד ים ולבוש חשוף מאוד, שבהם גם הבגד עצמו לא צריך להיראות.
    extra_boxes: אזורים מגלאי חלקי הגוף (x, y, w, h בקואורדינטות התמונה) — נצבעים רק
    איפה שיש אישה, כדי להשלים פספוס של הסגמנטציה בלי "מלבנים" על הרקע או על גבר.
    גברים (MAN) לא נצבעים בשום מצב.
    keep_boxes: פנים שהגלאי זיהה — לא נצבעות גם אם הסגמנטציה סימנה אותן כעור
    (קורה באנשים קטנים, שבהם המודל מתקשה להבדיל בין פנים לשאר העור).
    """
    W, H = size
    if mode == "body":
        m = np.isin(labels, (SKIN, CLOTHES, OTHER))
    else:
        m = labels == SKIN
    mask = Image.fromarray((m * 255).astype(np.uint8))
    if extra_boxes:
        lh, lw = labels.shape
        person = Image.fromarray((((labels != BG) & (labels != MAN)) * 255).astype(np.uint8))
        person = person.filter(ImageFilter.MaxFilter(_odd(max(lw, lh) * DILATE * 2)))
        boxes = Image.new("L", (lw, lh), 0)
        sx, sy = lw / W, lh / H
        for x, y, w, h in extra_boxes:
            boxes.paste(255, (int(x * sx), int(y * sy), int((x + w) * sx) + 1, int((y + h) * sy) + 1))
        add = np.minimum(np.asarray(boxes), np.asarray(person))
        mask = Image.fromarray(np.maximum(np.asarray(mask), add))
    lh, lw = labels.shape
    mask = mask.filter(ImageFilter.MaxFilter(_odd(max(lw, lh) * DILATE)))
    # הגדלה חלקה לגודל התמונה וסף נדיב: קצוות מעוגלים, בלי מדרגות
    mask = mask.resize((W, H), Image.Resampling.BILINEAR)
    mask = mask.point(lambda v: 255 if v >= 80 else 0)
    for x, y, w, h in keep_boxes:
        # מכווצים מעט מהצדדים ומלמטה: הצוואר והכתפיים לא "מוגנים" בטעות
        mask.paste(0, (int(x + w * 0.08), int(y), int(x + w * 0.92), int(y + h * 0.9)))
    return mask


def paint_regions(im: Image.Image, labels: np.ndarray, mode: str, extra_boxes: tuple[Box, ...] = (),
                  keep_boxes: tuple[Box, ...] = (), quality: int = 85) -> bytes:
    """צובע בשחור את האזורים לפי המסכה ומחזיר JPEG."""
    mask = region_mask(labels, mode, im.size, extra_boxes, keep_boxes)
    out = im.copy()
    out.paste((0, 0, 0), (0, 0, im.size[0], im.size[1]), mask)
    buf = io.BytesIO()
    out.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def _odd(x: float) -> int:
    n = max(3, int(round(x)))
    return n if n % 2 else n + 1
