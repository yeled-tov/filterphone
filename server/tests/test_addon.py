"""בדיקות לתוסף הפרוקסי עצמו: הקריאות של mitmproxy עם זרימות מדומות, מנוע מדומה ו-Redis מדומה."""
import base64
import io
from types import SimpleNamespace

import fakeredis
import numpy as np
import pytest
from mitmproxy import http
from mitmproxy.test import tflow
from PIL import Image

from filterphone.proxy import addon as A
from filterphone.proxy.policy import DevicePolicy, compile_hosts
from filterphone.proxy.web import WebState

IP = "10.8.0.20"
CHROME_IMG_ACCEPT = "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"


def jpeg(noisy=True) -> bytes:
    a = (np.random.default_rng(2).random((120, 160, 3)) * 255).astype("uint8") if noisy \
        else np.zeros((120, 160, 3), "uint8")
    buf = io.BytesIO()
    Image.fromarray(a).save(buf, format="JPEG", quality=90)
    return buf.getvalue()


class FakeEngine:
    def __init__(self, action="block"):
        self.action = action
        self.calls = []

    async def classify(self, body, header):
        self.calls.append(header)
        if self.action == "pass":
            return {"action": "pass", "reason": "clean", "ct": ""}, b""
        if self.action == "replace":
            return {"action": "replace", "reason": "skin=0.2", "ct": "image/jpeg"}, b"\xff\xd8painted"
        return {"action": "block", "reason": "female 0.9", "ct": "image/png"}, b"\x89PNGsized"


@pytest.fixture
def fp():
    a = A.FilterPhone()
    a.redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    a.web = WebState(a.redis)
    a.engine = FakeEngine()
    a.policies.set(IP, DevicePolicy(level=2))
    return a


def mkflow(url: str, req_headers: dict | None = None, ct: str | None = None, body: bytes = b"") -> http.HTTPFlow:
    f = tflow.tflow(resp=ct is not None)
    f.client_conn.peername = (IP, 40000)
    f.request.url = url
    f.request.headers.clear()
    for k, v in (req_headers or {}).items():
        f.request.headers[k] = v
    if ct is not None:
        f.response = http.Response.make(200, body, {"content-type": ct})
    return f


async def drain(a: A.FilterPhone):
    for t in list(a._background):
        await t


# ------------------------------------------------------------------ חסימת אתרים

async def test_site_block_is_logged_right_after_server_boot(fp, monkeypatch):
    """time.monotonic() = שניות מאז האתחול. מיד אחרי אתחול השרת (פחות מדקה) — עדיין רושמים חסימה."""
    clock = [5.0]
    monkeypatch.setattr(A.time, "monotonic", lambda: clock[0])
    fp.policies.set(IP, DevicePolicy(level=2, hosts=compile_hosts(["tiktok.com"])))
    for _ in range(3):                                  # 3 בקשות באותה דקה — אירוע אחד
        fp.requestheaders(mkflow("https://www.tiktok.com/", {"sec-fetch-dest": "document"}))
    clock[0] += A.HOST_EVENT_EVERY_S + 1                # דקה אחר כך — עוד אירוע
    fp.requestheaders(mkflow("https://www.tiktok.com/", {"sec-fetch-dest": "document"}))
    await drain(fp)
    events = await fp.redis.xrange(A.EVENTS_KEY)
    assert [e[1]["action"] for e in events] == ["site", "site"]


async def test_blocked_site_gets_block_page_for_navigation(fp):
    fp.policies.set(IP, DevicePolicy(level=2, hosts=compile_hosts(["instagram.com"])))
    f = mkflow("https://www.instagram.com/explore", {"sec-fetch-dest": "document"})
    fp.requestheaders(f)
    assert f.response.status_code == 403
    assert "האתר חסום".encode() in f.response.content and b"www.instagram.com" in f.response.content
    img = mkflow("https://scontent.instagram.com/a.jpg", {"sec-fetch-dest": "image"})
    fp.requestheaders(img)
    assert img.response.status_code == 200 and img.response.headers["content-type"] == "image/png"
    await drain(fp)
    events = await fp.redis.xrange(A.EVENTS_KEY)
    assert len(events) == 2 and events[0][1]["action"] == "site"


async def test_blocked_site_is_not_bypassed_even_if_in_static_passthrough(fp):
    # *.whatsapp.net ברשימת ה-passthrough הקבועה — אבל המנהל חסם תמונות פרופיל (pps)
    fp.policies.set(IP, DevicePolicy(level=1, hosts=compile_hosts(["pps.whatsapp.net"])))
    hello = SimpleNamespace(context=SimpleNamespace(client=SimpleNamespace(peername=(IP, 1))),
                            client_hello=SimpleNamespace(sni="pps.whatsapp.net"), ignore_connection=False)
    fp.tls_clienthello(hello)
    assert hello.ignore_connection is False
    other = SimpleNamespace(context=hello.context, client_hello=SimpleNamespace(sni="g.whatsapp.net"),
                            ignore_connection=False)
    fp.tls_clienthello(other)
    assert other.ignore_connection is True
    conn = SimpleNamespace(server=SimpleNamespace(sni="pps.whatsapp.net", error=None),
                           client=SimpleNamespace(peername=(IP, 1)))
    fp.server_connect(conn)
    assert conn.server.error


async def test_blocked_or_image_hosts_are_not_learned(fp):
    fp.policies.set(IP, DevicePolicy(level=2, hosts=compile_hosts(["tiktok.com"]), app_images="block"))
    err = "The client does not trust the proxy's certificate"
    for sni, learned in (("api.tiktok.com", False), ("ae01.alicdn.com", False), ("api.bank.example", True)):
        data = SimpleNamespace(conn=SimpleNamespace(sni=sni, error=err),
                               context=SimpleNamespace(client=SimpleNamespace(peername=(IP, 1))))
        fp.tls_failed_client(data)
        assert fp.hosts.should_passthrough(IP, sni) is learned, sni
    await drain(fp)
    assert list(await fp.redis.hkeys(A.PASSTHROUGH_KEY)) == [f"{IP}|api.bank.example"]


CHROME_HELLO = dict(cipher_suites=[0x3A3A, 0x1301, 0x1302], extensions=[(0x4A4A, b""), (0, b""), (16, b"")])
OKHTTP_HELLO = dict(cipher_suites=[0x1301, 0x1302, 0xC02B], extensions=[(0, b""), (16, b"")])


async def test_hosts_learned_from_an_app_stay_filtered_in_chrome(fp):
    """אפליקציה שלא סומכת על התעודה "לימדה" שרת תמונות / חיפוש / חנות — האפליקציה ממשיכה לעבור, אבל
    כרום (מזוהה לפי GREASE ב-ClientHello) עדיין נבדק. כך אליאקספרס / אפליקציית גוגל לא פותחות חור בדפדפן."""
    fp.policies.set(IP, DevicePolicy(level=1, app_images="allow"))
    ctx = SimpleNamespace(client=SimpleNamespace(peername=(IP, 1)))
    err = "The client does not trust the proxy's certificate"
    for sni in ("ae-pic-a1.aliexpress-media.com", "encrypted-tbn0.gstatic.com", "www.google.com", "api.bank.example"):
        fp.tls_failed_client(SimpleNamespace(conn=SimpleNamespace(sni=sni, error=err), context=ctx))

    def hello(sni, kind):
        return SimpleNamespace(context=ctx, client_hello=SimpleNamespace(sni=sni, **kind), ignore_connection=False)

    for sni in ("ae-pic-a1.aliexpress-media.com", "encrypted-tbn0.gstatic.com", "www.google.com"):
        chrome, app = hello(sni, CHROME_HELLO), hello(sni, OKHTTP_HELLO)
        fp.tls_clienthello(chrome)
        fp.tls_clienthello(app)
        assert chrome.ignore_connection is False, sni          # כרום — נבדק
        assert app.ignore_connection is True, sni              # האפליקציה — ממשיכה לעבוד
    api = hello("api.bank.example", CHROME_HELLO)               # שרת API שנלמד — לא תוכן, עובר כרגיל
    fp.tls_clienthello(api)
    assert api.ignore_connection is True
    static = hello("g.whatsapp.net", CHROME_HELLO)               # הרשימה הקבועה — לא משתנה
    fp.tls_clienthello(static)
    assert static.ignore_connection is True


async def test_path_rules_block_google_images_tab_only(fp):
    pol = DevicePolicy.from_json(
        '{"level":2,"paths":[{"host":"google.*","re":"[?&](?:tbm=isch|udm=2)(?:&|$)"}]}', 1)
    fp.policies.set(IP, pol)
    tab = mkflow("https://www.google.co.il/search?q=x&udm=2", {"sec-fetch-dest": "document"})
    fp.requestheaders(tab)
    assert tab.response.status_code == 403
    web = mkflow("https://www.google.co.il/search?q=x", {"sec-fetch-dest": "document"})
    fp.requestheaders(web)
    assert web.response is None


# ------------------------------------------------------------------ תמונות

async def test_image_request_is_normalized(fp):
    f = mkflow("https://ae-pic-a1.aliexpress-media.com/kf/S1.jpg_480x480.jpg_.avif",
               {"sec-fetch-dest": "image", "accept": CHROME_IMG_ACCEPT, "if-none-match": '"abc"'})
    fp.requestheaders(f)
    assert f.response is None
    assert "avif" not in f.request.headers["accept"] and "image/webp" in f.request.headers["accept"]
    assert f.request.path.endswith(".jpg_.webp")
    assert "if-none-match" not in f.request.headers


async def test_image_is_classified_and_replaced(fp):
    f = mkflow("https://cdn.site.com/p.webp?x=1", {"sec-fetch-dest": "image"}, "image/webp", jpeg())
    fp.responseheaders(f)
    assert f.metadata["fp_kind"] == "image" and not f.response.stream
    await fp.response(f)
    assert f.response.content == b"\x89PNGsized"
    assert f.response.headers["x-filterphone"] == "block"
    assert fp.engine.calls[0]["level"] == 2
    await drain(fp)
    (_, ev), = await fp.redis.xrange(A.EVENTS_KEY)
    assert ev["kind"] == "image" and ev["path"] == "/p.webp" and ev["reason"] == "female 0.9"


async def test_image_without_content_type_is_still_checked(fp):
    # שרת תמונות שלא מסר סוג תוכן, בכתובת בלי סיומת — הדפדפן ביקש תמונה, אז היא נבדקת
    f = mkflow("https://ae01.alicdn.com/kf/S1a2b3c4d", {"sec-fetch-dest": "image"}, "application/octet-stream", jpeg())
    fp.responseheaders(f)
    assert f.metadata.get("fp_kind") == "image"
    other = mkflow("https://x.com/download", {}, "application/octet-stream", b"\x00" * 2000)
    fp.responseheaders(other)
    assert other.response.stream is True                     # הורדה רגילה — לא נוגעים


async def test_oversize_image_is_not_passed_unchecked_on_strict_levels(fp):
    big = mkflow("https://x.com/huge.jpg", {"sec-fetch-dest": "image"}, "image/jpeg", b"")
    big.response.headers["content-length"] = str(fp.cfg.max_image_bytes + 1)
    fp.responseheaders(big)
    assert big.error is not None                             # רמה 2 — לא עוברת בלי בדיקה
    fp.policies.set(IP, DevicePolicy(level=3))
    big3 = mkflow("https://x.com/huge.jpg", {"sec-fetch-dest": "image"}, "image/jpeg", b"")
    big3.response.headers["content-length"] = str(fp.cfg.max_image_bytes + 1)
    fp.responseheaders(big3)
    assert big3.error is None and big3.response.stream is True


async def test_all_images_off_mode_skips_engine(fp):
    fp.policies.set(IP, DevicePolicy(level=0, images="block"))
    req = mkflow("https://x.com/a.jpg", {"sec-fetch-dest": "image"})
    fp.requestheaders(req)
    assert req.response.headers["x-filterphone"] == "images-off"
    xhr = mkflow("https://x.com/b.jpg", {}, "image/jpeg", jpeg())       # לא זוהה כבקשת תמונה מראש
    fp.responseheaders(xhr)
    await fp.response(xhr)
    assert xhr.response.headers["x-filterphone"] == "block" and fp.engine.calls == []


async def test_level0_device_streams_everything(fp):
    fp.policies.set(IP, DevicePolicy(level=0))
    f = mkflow("https://x.com/a.jpg", {"sec-fetch-dest": "image"}, "image/jpeg", jpeg())
    fp.responseheaders(f)
    assert f.response.stream is True


# ------------------------------------------------------------------ תמונות מוטמעות (גוגל)

async def test_inline_images_in_google_html_are_filtered(fp):
    b64 = base64.b64encode(jpeg())
    html = b'<html><body><g-img><img id="dimg_1" src="data:image/jpeg;base64,' + b64 + b'"></g-img></body></html>'
    f = mkflow("https://www.google.com/search?q=x", {"sec-fetch-dest": "document"}, "text/html; charset=UTF-8", html)
    fp.responseheaders(f)
    assert f.metadata["fp_kind"] == "serp"              # דף תוצאות: סינון תוצאות + תמונות מוטמעות
    await fp.response(f)
    assert b64 not in f.response.content
    assert base64.b64encode(b"\x89PNGsized") in f.response.content
    assert f.response.headers["x-filterphone"] == "inline-1"
    await drain(fp)
    (_, ev), = await fp.redis.xrange(A.EVENTS_KEY)
    assert ev["kind"] == "inline" and ev["host"] == "www.google.com"


async def test_json_from_other_sites_streams(fp):
    f = mkflow("https://api.example.com/feed", {}, "application/json", b"{}")
    fp.responseheaders(f)
    assert f.response.stream is True


# ------------------------------------------------------------------ וידאו

async def test_video_is_killed_when_blocked(fp):
    fp.policies.set(IP, DevicePolicy(level=2, video=True))
    f = mkflow("https://rr1.googlevideo.com/videoplayback", {}, "video/mp4", b"\x00" * 10)
    fp.responseheaders(f)
    assert f.error is not None and f.error.msg == "Connection killed."
    ok = mkflow("https://x.com/song.mp3", {}, "audio/mpeg", b"\x00")
    fp.responseheaders(ok)
    assert ok.error is None
