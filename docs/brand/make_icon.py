"""ממיר את האייקון (רקע לבן) לשכבות של אנדרואיד: חזית שקופה (adaptive), מונוכרום, וגרסה רגילה."""
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

src, out = Path(sys.argv[1]), Path(sys.argv[2])
img = np.asarray(Image.open(src).convert("RGB")).astype(np.float32) / 255.0

# "צבע לשקיפות" (כמו ב-GIMP) ללבן: פיקסל לבן → שקוף; צל אפור → חצי שקוף; הצבע מתוקן כדי שלא יולבן
a = np.max(1.0 - img, axis=2)                       # המרחק הגדול ביותר מלבן בכל ערוץ
a = np.clip(a * 1.15, 0, 1)
safe = np.where(a > 1e-4, a, 1.0)[..., None]
rgb = np.clip((img - (1.0 - a[..., None])) / safe, 0, 1)
rgba = np.dstack([rgb, a])
fg = Image.fromarray((rgba * 255).astype(np.uint8), "RGBA")

# חיתוך לשוליים של התוכן
alpha = np.asarray(fg)[..., 3]
ys, xs = np.where(alpha > 10)
fg = fg.crop((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1))
w, h = fg.size
side = max(w, h)
sq = Image.new("RGBA", (side, side), (0, 0, 0, 0))
sq.paste(fg, ((side - w) // 2, (side - h) // 2))
sq.save(out / "trimmed.png")


def fit(canvas: int, frac: float) -> Image.Image:
    c = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    n = round(canvas * frac)
    c.paste(sq.resize((n, n), Image.LANCZOS), ((canvas - n) // 2, (canvas - n) // 2), sq.resize((n, n), Image.LANCZOS))
    return c


res = out / "res"
dens = {"mdpi": 1, "hdpi": 1.5, "xhdpi": 2, "xxhdpi": 3, "xxxhdpi": 4}
for d, k in dens.items():
    p = res / f"mipmap-{d}"
    p.mkdir(parents=True, exist_ok=True)
    fg108 = fit(round(108 * k), 0.64)            # האזור הבטוח: 66dp מתוך 108dp (גם במסכה עגולה)
    fg108.save(p / "ic_launcher_foreground.png", optimize=True)
    mono = Image.new("RGBA", fg108.size, (0, 0, 0, 0))
    m = np.asarray(fg108)[..., 3]
    mono.putalpha(Image.fromarray(np.where(m > 90, 255, 0).astype(np.uint8)))
    mono = Image.merge("RGBA", (*[Image.new("L", fg108.size, 255)] * 3, mono.getchannel("A")))
    mono.save(p / "ic_launcher_monochrome.png", optimize=True)
    legacy = Image.new("RGBA", (round(48 * k),) * 2, (255, 255, 255, 0))
    s = round(48 * k)
    r = ImageDraw.Draw(legacy)
    r.rounded_rectangle((0, 0, s - 1, s - 1), radius=round(s * 0.22), fill=(255, 255, 255, 255))
    inner = fit(s, 0.86)
    legacy.alpha_composite(inner)
    legacy.save(p / "ic_launcher.png", optimize=True)

# תצוגה מקדימה: בגודל אמיתי במסך הבית (48dp ב-xxhdpi = 144px), עגול ומרובע מעוגל, רקע בהיר וכהה
prev = Image.new("RGB", (900, 420), (250, 250, 250))
draw = ImageDraw.Draw(prev)
draw.rectangle((450, 0, 900, 420), fill=(24, 28, 34))
for i, (shape, x0) in enumerate((("circle", 40), ("squircle", 230), ("circle", 490), ("squircle", 680))):
    s = 144
    layer = Image.new("RGBA", (s, s), (255, 255, 255, 255))
    layer.alpha_composite(fit(round(s * 108 / 72), 0.64).resize((round(s * 108 / 72),) * 2).crop(
        ((round(s * 108 / 72) - s) // 2,) * 2 + ((round(s * 108 / 72) + s) // 2,) * 2).resize((s, s)))
    mask = Image.new("L", (s, s), 0)
    md = ImageDraw.Draw(mask)
    if shape == "circle":
        md.ellipse((0, 0, s - 1, s - 1), fill=255)
    else:
        md.rounded_rectangle((0, 0, s - 1, s - 1), radius=40, fill=255)
    prev.paste(layer.convert("RGB"), (x0, 60), mask)
    small = layer.resize((48, 48), Image.LANCZOS)
    sm = mask.resize((48, 48), Image.LANCZOS)
    prev.paste(small.convert("RGB"), (x0 + 48, 260), sm)
prev.save(out / "preview.png")
print("ok", sq.size)
