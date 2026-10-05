"""
מודול החלפת תמונות - גרסה משופרת
מחזיר תמונה שחורה שמכסה הכל, גם כ-background-image
"""
from PIL import Image
import io

# תמונה שחורה בגודל סטנדרטי עם quality נמוך = קובץ קטן
def create_solid_black(width, height):
    img = Image.new("RGB", (width, height), (0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=30, optimize=True)
    return buf.getvalue()

# קובץ שחור 10x10 - קטן אבל עובד כ-background
BLOCKED_IMAGE_BYTES = create_solid_black(10, 10)

# במידה וצריך PNG
def create_solid_black_png(width, height):
    img = Image.new("RGB", (width, height), (0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()

BLOCKED_PNG_BYTES = create_solid_black_png(10, 10)


def get_blocked_response(original_size=None, content_type="image/jpeg"):
    """
    מחזיר bytes של תמונת החסימה
    אם content_type מבקש PNG - מחזיר PNG
    אחרת - JPEG
    """
    if "png" in content_type.lower():
        return BLOCKED_PNG_BYTES, "image/png"
    elif "webp" in content_type.lower():
        return BLOCKED_IMAGE_BYTES, "image/jpeg"
    else:
        return BLOCKED_IMAGE_BYTES, "image/jpeg"
