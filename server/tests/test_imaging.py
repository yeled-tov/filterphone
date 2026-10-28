import io

import numpy as np
from PIL import Image

from filterphone.engine.imaging import (
    PLACEHOLDER_PNG,
    decode,
    nudenet_decode,
    nudenet_input,
    paint_boxes,
    siglip_views,
)


def _img(fmt: str, size=(300, 200), mode="RGB", color=(200, 120, 90)) -> bytes:
    buf = io.BytesIO()
    Image.new(mode, size, color if mode == "RGB" else color + (128,)).save(buf, format=fmt)
    return buf.getvalue()


def test_decode_common_formats():
    for fmt in ("JPEG", "PNG", "WEBP", "GIF", "BMP"):
        d = decode(_img(fmt))
        assert d is not None, fmt
        assert (d.width, d.height) == (300, 200)
        assert d.primary.mode == "RGB"


def test_decode_transparent_png_on_white():
    d = decode(_img("PNG", mode="RGBA", color=(0, 0, 0)))
    r, g, b = d.primary.getpixel((5, 5))
    assert r > 100 and g > 100 and b > 100          # שחור חצי שקוף על לבן → אפור


def test_decode_garbage_returns_none():
    assert decode(b"not an image at all") is None
    assert decode(b"\xff\xd8\xff\xe0" + b"\x00" * 50) is None


def test_decode_shrinks_large_images():
    d = decode(_img("JPEG", size=(4000, 3000)), max_side=1024)
    assert max(d.primary.size) <= 1024
    assert (d.width, d.height) == (4000, 3000)


def test_animated_gif_samples_frames():
    frames = [Image.new("RGB", (100, 100), (i * 40, 0, 0)) for i in range(5)]
    buf = io.BytesIO()
    frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:], duration=50)
    d = decode(buf.getvalue())
    assert d.animated and 2 <= len(d.frames) <= 3


def test_siglip_views_shape_and_range():
    im = Image.new("RGB", (640, 480), (255, 0, 0))
    v = siglip_views(im, 256)
    assert len(v) == 1 and v[0].shape == (3, 256, 256)
    assert np.isclose(v[0][0].max(), 1.0) and np.isclose(v[0][1].min(), -1.0)


def test_siglip_views_tiles_long_banners():
    assert len(siglip_views(Image.new("RGB", (1200, 200)), 256)) > 1


def test_nudenet_roundtrip_box_scaling():
    im = Image.new("RGB", (640, 320))
    inp = nudenet_input(im, 320)
    assert inp.tensor.shape == (3, 320, 320) and inp.scale == 2.0
    # פלט מדומה: 2 מועמדים, 18 מחלקות; הראשון FACE_FEMALE במרכז (160,80) בגודל 40x40 בקנה 320
    out = np.zeros((1, 22, 2), np.float32)
    out[0, :4, 0] = [80, 40, 20, 20]
    out[0, 4 + 1, 0] = 0.9
    out[0, 4 + 1, 1] = 0.1                 # מתחת לסף — מסונן
    dets = nudenet_decode(out, inp)
    assert len(dets) == 1
    label, score, x, y, w, h = dets[0]
    assert label == "FACE_FEMALE" and score == 0.9
    assert (x, y, w, h) == (140, 60, 40, 40)


def test_paint_boxes_greys_out_region():
    im = Image.new("RGB", (100, 100), (255, 255, 255))
    out = Image.open(io.BytesIO(paint_boxes(im, [(40, 40, 20, 20)])))
    assert all(abs(c - 150) <= 12 for c in out.getpixel((50, 50)))
    assert min(out.getpixel((5, 5))) > 225


def test_placeholder_is_valid_png():
    assert Image.open(io.BytesIO(PLACEHOLDER_PNG)).format == "PNG"
