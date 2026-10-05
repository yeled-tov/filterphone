"""סגמנטציה לרמה 2: לכל פיקסל — רקע, שיער, עור הגוף, עור הפנים, בגד או אביזר.

המודל: MediaPipe selfie-multiclass 256×256 (Google, Apache-2.0), בהמרה ל-ONNX.
בניגוד לגלאי חלקי הגוף (NudeNet), שמחזיר מלבנים לכמה אזורים בלבד, כאן יש מסכה
מדויקת של כל העור החשוף — ידיים, כתפיים, צוואר, גב, בטן ורגליים — כך שהצביעה
עוקבת אחרי צורת הגוף ולא "מלבן שחור במקום הלא נכון".

שני מעברים:
  1. התמונה כולה, מתוחה ל-256×256 (כמו ב-MediaPipe) — איפה יש אנשים.
  2. לכל אדם קטן (עד 3): חיתוך ריבועי סביבו והרצה נוספת. בלי זה, אדם שתופס
     עשירית מהתמונה מקבל מסכה של 25×25 פיקסלים ו"חצי יד" נשארת גלויה.

הכול כאן פונקציות טהורות (numpy + Pillow) — הרצת המודל עצמה ב-models.py.
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

WORK_SIDE = 512             # רזולוציית מפת התוויות (הצלע הארוכה)
SKIN_MIN_P = 0.35           # עור "כמעט בטוח" כבר נחשב עור — עדיף לצבוע מעט יותר מאשר לפספס
MAX_CROPS = 3
CROP_MAX_AREA = 0.45        # אדם שתופס יותר מזה מהתמונה כבר מפורט מספיק במעבר הראשון
CROP_PAD = 0.18
GRID = 64                   # רשת גסה למציאת אנשים נפרדים
DILATE = 0.012              # הרחבת האזור הצבוע: 1.2% מהצלע הארוכה — שוליים בלי "קצוות" גלויים


@dataclass(frozen=True, slots=True)
class SegStats:
    skin: float             # עור הגוף (בלי פנים) מתוך שטח האנשים
    person: float           # שטח האנשים מתוך התמונה
    crops: int = 0

    def dump(self) -> dict:
        return {"skin": round(self.skin, 4), "person": round(self.person, 4), "crops": self.crops}

    @classmethod
    def load(cls, d: dict) -> SegStats:
        return cls(float(d["skin"]), float(d["person"]), int(d.get("crops", 0)))


def softmax(logits: np.ndarray) -> np.ndarray:
    e = np.exp(logits - logits.max(axis=-1, keepdims=True))
    return e / e.sum(axis=-1, keepdims=True)


def seg_input(im: Image.Image, size: int, box: tuple[int, int, int, int] | None = None) -> np.ndarray:
    """התמונה (או חיתוך ממנה) → (size, size, 3) בטווח [0, 1], כמו שהמודל מצפה."""
    if box is not None:
        im = im.crop(box)
    return np.asarray(im.resize((size, size), Image.Resampling.BILINEAR), np.float32) / 255.0


# ---------------------------------------------------------------- מציאת אנשים

def _components(grid: np.ndarray) -> list[tuple[int, int, int, int, int]]:
    """רכיבים קשירים (שכנות 8) ברשת בוליאנית קטנה → [(גודל, y0, x0, y1, x1)]."""
    h, w = grid.shape
    seen = np.zeros_like(grid, dtype=bool)
    out = []
    for sy, sx in zip(*np.nonzero(grid), strict=True):
        if seen[sy, sx]:
            continue
        seen[sy, sx] = True
        q = deque([(sy, sx)])
        n, y0, x0, y1, x1 = 0, sy, sx, sy, sx
        while q:
            y, x = q.popleft()
            n += 1
            y0, x0, y1, x1 = min(y0, y), min(x0, x), max(y1, y), max(x1, x)
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < h and 0 <= nx < w and grid[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        q.append((ny, nx))
        out.append((n, int(y0), int(x0), int(y1), int(x1)))
    return out


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
        # לפיקסלים, עם שוליים, ואז לריבוע — כדי שהמודל לא יקבל אדם מעוות
        bx0, bx1 = fx0 * width, fx1 * width
        by0, by1 = fy0 * height, fy1 * height
        side = max(bx1 - bx0, by1 - by0) * (1 + 2 * CROP_PAD)
        side = min(max(side, 24.0), float(max(width, height)))
        cx, cy = (bx0 + bx1) / 2, (by0 + by1) / 2
        x0p = int(round(min(max(cx - side / 2, 0), max(width - side, 0))))
        y0p = int(round(min(max(cy - side / 2, 0), max(height - side, 0))))
        boxes.append((x0p, y0p, min(width, x0p + int(round(side))), min(height, y0p + int(round(side)))))
    return boxes


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


def stats(labels: np.ndarray, crops: int = 0) -> SegStats:
    person = int(np.count_nonzero(labels != BG))
    skin = int(np.count_nonzero(labels == SKIN))
    return SegStats(skin / person if person else 0.0, person / labels.size, crops)


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

Box = tuple[int, int, int, int]


def region_mask(labels: np.ndarray, mode: str, size: tuple[int, int],
                extra_boxes: tuple[Box, ...] = (), keep_boxes: tuple[Box, ...] = ()) -> Image.Image:
    """המסכה לצביעה (L, 0/255) בגודל התמונה.

    mode="skin": עור חשוף (בלי פנים).  mode="body": כל הגוף חוץ מהפנים והשיער —
    לבגד ים ולבוש חשוף מאוד, שבהם גם הבגד עצמו לא צריך להיראות.
    extra_boxes: אזורים מגלאי חלקי הגוף (x, y, w, h בקואורדינטות התמונה) — נצבעים רק
    איפה שיש אדם, כדי להשלים פספוס של הסגמנטציה בלי "מלבנים" על הרקע.
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
        person = Image.fromarray(((labels != BG) * 255).astype(np.uint8))
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
