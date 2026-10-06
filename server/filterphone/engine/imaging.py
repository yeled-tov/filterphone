"""פענוח תמונות והכנתן למודלים — הכול בזיכרון, בלי קבצים זמניים."""
from __future__ import annotations

import io
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError

# הגנה מפני "פצצות דחיסה" — תמונה קטנה בבייטים שנפתחת לענקית בזיכרון
Image.MAX_IMAGE_PIXELS = 50_000_000

_DECODE_ERRORS = (UnidentifiedImageError, OSError, ValueError, SyntaxError,
                  Image.DecompressionBombError, EOFError)

MAX_FRAMES = 3                  # בתמונה מונפשת: ראשון, אמצעי ואחרון
TILE_ASPECT = 2.0               # מעל יחס כזה (באנר ארוך) בודקים גם חלקים


@dataclass(slots=True)
class Decoded:
    frames: list[Image.Image]   # RGB, מוקטנות עד max_side
    width: int                  # גודל מקורי
    height: int
    format: str | None

    @property
    def animated(self) -> bool:
        return len(self.frames) > 1

    @property
    def primary(self) -> Image.Image:
        return self.frames[0]


def _to_rgb(im: Image.Image) -> Image.Image:
    """המרה ל-RGB. שקיפות מורכבת על רקע לבן, כמו שהדפדפן מציג."""
    if im.mode == "RGB":
        return im
    if im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info):
        rgba = im.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        return Image.alpha_composite(bg, rgba).convert("RGB")
    return im.convert("RGB")


def _shrink(im: Image.Image, max_side: int) -> Image.Image:
    if max(im.size) > max_side:
        im = im.copy()
        im.thumbnail((max_side, max_side), Image.Resampling.BILINEAR)
    return im


def decode(data: bytes, max_side: int = 1024) -> Decoded | None:
    """מפענח תמונה. מחזיר None אם הפורמט לא נתמך או שהקובץ פגום."""
    try:
        im = Image.open(io.BytesIO(data))
        width, height = im.size
        fmt = im.format
        n_frames = getattr(im, "n_frames", 1) or 1

        if n_frames > 1:
            picks = sorted({0, n_frames // 2, n_frames - 1})[:MAX_FRAMES]
            frames = []
            for i in picks:
                im.seek(i)
                frames.append(_shrink(_to_rgb(im), max_side))
            return Decoded(frames, width, height, fmt)

        # ב-JPEG, draft מפענח ישירות ברזולוציה מוקטנת — חוסך את רוב הזמן
        im.draft("RGB", (max_side, max_side))
        im = ImageOps.exif_transpose(im) or im
        return Decoded([_shrink(_to_rgb(im), max_side)], width, height, fmt)
    except _DECODE_ERRORS:
        return None


# ---------------------------------------------------------------- SigLIP2

def siglip_views(im: Image.Image, size: int) -> list[np.ndarray]:
    """תצוגות של התמונה למודל: התמונה כולה, ובבאנר ארוך גם ריבועים חופפים לאורכו.

    כמו המעבד הרשמי של SigLIP2: מתיחה ל-size×size (בלי חיתוך), ונרמול ל-[-1, 1].
    """
    views = [im]
    w, h = im.size
    long_side, short_side = max(w, h), min(w, h)
    if short_side > 0 and long_side / short_side > TILE_ASPECT:
        n = min(3, int(np.ceil(long_side / short_side)))
        step = (long_side - short_side) / max(n - 1, 1)
        for i in range(n):
            o = int(round(i * step))
            box = (o, 0, o + short_side, h) if w >= h else (0, o, w, o + short_side)
            views.append(im.crop(box))
    return [_siglip_tensor(v, size) for v in views]


def siglip_crop(im: Image.Image, box: tuple[int, int, int, int], size: int) -> np.ndarray:
    """חיתוך (x0, y0, x1, y1) מהתמונה → קלט ל-SigLIP2 (לסיווג אדם אחד בתמונה)."""
    return _siglip_tensor(im.crop(box), size)


def _siglip_tensor(im: Image.Image, size: int) -> np.ndarray:
    im = im.resize((size, size), Image.Resampling.BILINEAR)
    a = np.asarray(im, dtype=np.float32)
    a = a * (2.0 / 255.0) - 1.0
    return a.transpose(2, 0, 1)


# ---------------------------------------------------------------- NudeNet

NUDENET_LABELS = (
    "FEMALE_GENITALIA_COVERED", "FACE_FEMALE", "BUTTOCKS_EXPOSED", "FEMALE_BREAST_EXPOSED",
    "FEMALE_GENITALIA_EXPOSED", "MALE_BREAST_EXPOSED", "ANUS_EXPOSED", "FEET_EXPOSED",
    "BELLY_COVERED", "FEET_COVERED", "ARMPITS_COVERED", "ARMPITS_EXPOSED", "FACE_MALE",
    "BELLY_EXPOSED", "MALE_GENITALIA_EXPOSED", "ANUS_COVERED", "FEMALE_BREAST_COVERED",
    "BUTTOCKS_COVERED",
)


@dataclass(slots=True)
class NudenetInput:
    tensor: np.ndarray          # (3, S, S)
    scale: float                # גודל הריבוע המרופד / S
    width: int
    height: int


def nudenet_input(im: Image.Image, size: int = 320) -> NudenetInput:
    """כמו NudeNet המקורי: ריפוד לריבוע (שחור, ימין ולמטה) ואז הקטנה."""
    w, h = im.size
    side = max(w, h)
    canvas = Image.new("RGB", (side, side), (0, 0, 0))
    canvas.paste(im, (0, 0))
    canvas = canvas.resize((size, size), Image.Resampling.BILINEAR)
    a = np.asarray(canvas, dtype=np.float32) / 255.0
    return NudenetInput(a.transpose(2, 0, 1), side / size, w, h)


def nudenet_decode(output: np.ndarray, inp: NudenetInput,
                   score_min: float = 0.25, iou: float = 0.45) -> list[tuple[str, float, int, int, int, int]]:
    """פלט YOLOv8 של NudeNet → [(תווית, ציון, x, y, w, h)] בקואורדינטות התמונה."""
    rows = output.reshape(output.shape[-2], output.shape[-1]).T      # (N, 4 + classes)
    cls_scores = rows[:, 4:]
    best = cls_scores.argmax(axis=1)
    conf = cls_scores[np.arange(len(rows)), best]
    keep = conf >= score_min
    if not keep.any():
        return []
    rows, best, conf = rows[keep], best[keep], conf[keep]
    cx, cy, bw, bh = (rows[:, i] * inp.scale for i in range(4))
    x1 = np.clip(cx - bw / 2, 0, inp.width)
    y1 = np.clip(cy - bh / 2, 0, inp.height)
    x2 = np.clip(cx + bw / 2, 0, inp.width)
    y2 = np.clip(cy + bh / 2, 0, inp.height)
    order = _nms(x1, y1, x2, y2, conf, iou)
    return [(NUDENET_LABELS[int(best[i])], round(float(conf[i]), 3),
             int(x1[i]), int(y1[i]), int(x2[i] - x1[i]), int(y2[i] - y1[i])) for i in order]


def _nms(x1, y1, x2, y2, scores, iou_thr: float) -> list[int]:
    areas = np.maximum(x2 - x1, 0) * np.maximum(y2 - y1, 0)
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size:
        i = int(order[0])
        keep.append(i)
        rest = order[1:]
        xx1 = np.maximum(x1[i], x1[rest])
        yy1 = np.maximum(y1[i], y1[rest])
        xx2 = np.minimum(x2[i], x2[rest])
        yy2 = np.minimum(y2[i], y2[rest])
        inter = np.maximum(xx2 - xx1, 0) * np.maximum(yy2 - yy1, 0)
        union = areas[i] + areas[rest] - inter
        overlap = np.divide(inter, union, out=np.zeros_like(inter), where=union > 0)
        order = rest[overlap <= iou_thr]
    return keep


# ---------------------------------------------------------------- פלט

def paint_boxes(im: Image.Image, boxes: list[tuple[int, int, int, int]],
                pad: float = 0.12, quality: int = 85) -> bytes:
    """צובע בשחור את האזורים (x, y, w, h), עם שוליים, ומחזיר JPEG."""
    out = im.copy()
    draw = ImageDraw.Draw(out)
    W, H = out.size
    for x, y, w, h in boxes:
        dx, dy = w * pad, h * pad
        draw.rectangle((max(0, x - dx), max(0, y - dy), min(W, x + w + dx), min(H, y + h + dy)),
                       fill=(0, 0, 0))
    buf = io.BytesIO()
    out.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


PLACEHOLDER_COLOR = (226, 228, 232)
PLACEHOLDER_MAX_SIDE = 1600


@lru_cache(maxsize=512)
def placeholder(width: int = 8, height: int = 8) -> bytes:
    """תמונה חלופית לתמונה חסומה: מלבן אפור בהיר, באותן מידות כמו המקור (עד 1600) —
    כך שהעמוד לא "קופץ" ותמונה בלי width/height לא מתכווצת לנקודה. PNG של צבע אחד
    שוקל כמה מאות בייטים בלבד."""
    if width <= 0 or height <= 0:
        width = height = 8
    scale = min(1.0, PLACEHOLDER_MAX_SIDE / max(width, height))
    im = Image.new("P", (max(1, round(width * scale)), max(1, round(height * scale))), 0)
    im.putpalette(PLACEHOLDER_COLOR)
    buf = io.BytesIO()
    im.save(buf, format="PNG", compress_level=9)
    return buf.getvalue()


def image_size(data: bytes) -> tuple[int, int] | None:
    """מידות התמונה מהכותרת בלבד (בלי פענוח מלא)."""
    try:
        return Image.open(io.BytesIO(data)).size
    except _DECODE_ERRORS:
        return None


PLACEHOLDER_PNG = placeholder()
