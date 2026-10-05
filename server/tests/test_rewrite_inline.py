import base64
import io
import json

from PIL import Image

from filterphone.proxy import inline, rewrite

CHROME_IMG_ACCEPT = "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"


def test_accept_drops_formats_the_engine_does_not_prefer():
    assert rewrite.image_accept(CHROME_IMG_ACCEPT) == "image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"
    assert rewrite.image_accept("image/jxl,image/avif,*/*") == "image/webp,*/*"
    assert rewrite.image_accept("image/webp,*/*") is None
    assert rewrite.image_accept("") is None


def test_aliexpress_avif_suffix_becomes_webp():
    p = "/kf/S3706c2dfc3924874b733db5fb590c4dew.jpg_960x960q90.jpg_.avif"
    assert rewrite.image_path("ae-pic-a1.aliexpress-media.com", p) == p[:-5] + ".webp"
    assert rewrite.image_path("ae01.alicdn.com", p + "?x=1") == p[:-5] + ".webp?x=1"
    assert rewrite.image_path("example.com", p) is None            # רק בשרתים של Alibaba
    assert rewrite.image_path("ae01.alicdn.com", "/kf/a.jpg_.webp") is None


def test_query_and_cloudinary_format_params():
    assert rewrite.image_path("cdn.shop.com", "/i.jpg?w=300&format=avif") == "/i.jpg?w=300&format=webp"
    assert rewrite.image_path("img.x.com", "/a?fm=avif&q=80") == "/a?fm=webp&q=80"
    assert rewrite.image_path("res.cloudinary.com", "/demo/image/upload/f_avif,q_auto/s.jpg") == \
        "/demo/image/upload/f_webp,q_auto/s.jpg"
    assert rewrite.image_path("x.com", "/a?platform=avif-lovers") is None


def _jpeg(size=(120, 90), color=(10, 120, 200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def _noisy_jpeg() -> bytes:
    """JPEG "אמיתי" מספיק גדול (מעל הסף) — רעש לא נדחס טוב."""
    import numpy as np
    a = (np.random.default_rng(1).random((90, 120, 3)) * 255).astype("uint8")
    buf = io.BytesIO()
    Image.fromarray(a).save(buf, format="JPEG", quality=90)
    return buf.getvalue()


async def _block_all(img: bytes):
    return b"\x89PNG-replacement", "image/png"


async def _pass_all(img: bytes):
    return None


async def test_html_inline_image_is_replaced_and_rest_untouched():
    img = _noisy_jpeg()
    b64 = base64.b64encode(img)
    html = b'<p>\xd7\xa9\xd7\x9c\xd7\x95\xd7\x9d</p><img src="data:image/jpeg;base64,' + b64 + b'" width=120>'
    r = await inline.rewrite(html, _block_all, min_bytes=500)
    assert r.found == 1 and r.replaced == 1
    assert r.body.startswith(b'<p>\xd7\xa9\xd7\x9c\xd7\x95\xd7\x9d</p><img src="data:image/png;base64,')
    assert r.body.endswith(b'" width=120>')
    assert base64.b64encode(b"\x89PNG-replacement") in r.body


async def test_json_escaped_inline_image_stays_valid_json():
    img = _noisy_jpeg()
    b64 = base64.b64encode(img).decode()
    # כמו בתשובות של גוגל: '/' כ-‎\/‎ ו-'=' כ-‎=
    escaped = b64.replace("/", "\\/").replace("=", "\\u003d")
    doc = '{"items":[{"thumb":"data:image\\/jpeg;base64,' + escaped + '","title":"x"}]}'
    assert json.loads(doc)["items"][0]["thumb"].startswith("data:image/jpeg;base64,")
    r = await inline.rewrite(doc.encode(), _block_all, min_bytes=500)
    assert r.replaced == 1
    parsed = json.loads(r.body)
    thumb = parsed["items"][0]["thumb"]
    assert thumb.startswith("data:image/png;base64,")
    assert base64.b64decode(thumb.split(",", 1)[1]) == b"\x89PNG-replacement"


def test_js_hex_escapes_are_decoded():
    img = _noisy_jpeg()
    b64 = base64.b64encode(img).decode()
    js = b64.replace("=", "\\x3d")
    m = inline.DATA_URI.search(("var s='data:image/jpeg;base64," + js + "';").encode())
    assert m and inline.decode_payload(m.group("b64")) == img


async def test_small_and_passing_images_are_left_alone():
    small = base64.b64encode(_jpeg((8, 8)))
    body = b'<img src="data:image/jpeg;base64,' + small + b'">'
    r = await inline.rewrite(body, _block_all, min_bytes=5000)
    assert r.body == body and r.replaced == 0
    big = base64.b64encode(_noisy_jpeg())
    body = b'<img src="data:image/jpeg;base64,' + big + b'">'
    r = await inline.rewrite(body, _pass_all, min_bytes=500)
    assert r.body == body and r.found == 1 and r.replaced == 0


async def test_overflow_images_go_to_fallback():
    big = base64.b64encode(_noisy_jpeg())
    body = b"".join(b'<img src="data:image/jpeg;base64,' + big + b'">' for _ in range(3))
    seen = []

    async def judge(img):
        seen.append(1)
        return None

    r = await inline.rewrite(body, judge, min_bytes=500, max_images=1,
                             overflow=lambda img: (b"X", "image/png"))
    assert len(seen) == 1 and r.replaced == 2


def test_scan_scope():
    assert inline.should_scan("text/html; charset=utf-8", "www.ynet.co.il")
    assert inline.should_scan("application/json", "www.google.com")
    assert inline.should_scan("text/javascript", "www.google.co.il")
    assert not inline.should_scan("application/json", "api.example.com")
    assert not inline.should_scan("image/jpeg", "www.google.com")
