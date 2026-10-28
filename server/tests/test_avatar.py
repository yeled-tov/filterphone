"""תמונת פרופיל מהאפליקציה: מוקטנת ל-JPEG נקי, עוברת את מנוע הסינון, ומוצגת בתמיכה בדשבורד."""
import base64
import io

import pytest
from helpers import admin_headers
from PIL import Image
from test_bot import active_customer, auth

from filterphone.api import service as S
from filterphone.api.app import device_limiter


@pytest.fixture(autouse=True)
def _limits():
    device_limiter._hits.clear()


def _photo(size=(900, 600), fmt="JPEG", exif=True) -> bytes:
    im = Image.new("RGB", size, (40, 120, 200))
    out = io.BytesIO()
    if exif and fmt == "JPEG":
        ex = Image.Exif()
        ex[0x010F] = "PhoneMaker"            # Make — מטא-דאטה שלא אמורה להישמר
        im.save(out, fmt, exif=ex.tobytes())
    else:
        im.save(out, fmt)
    return out.getvalue()


def _engine(monkeypatch, action="pass"):
    seen = []

    def classify(sock, header, data, timeout):
        seen.append((header, data))
        return {"action": action}, b""
    monkeypatch.setattr(S.protocol, "call_sync", classify)
    return seen


def test_avatar_is_resized_clean_filtered_and_shown_in_support(env, monkeypatch):
    svc, client = env
    h = admin_headers(client)
    did, token = active_customer(svc, client)
    seen = _engine(monkeypatch)
    assert client.get("/v1/device", headers=auth(token)).json()["avatar_at"] is None
    r = client.put("/v1/device/avatar", headers=auth(token), json={"image": base64.b64encode(_photo()).decode()})
    assert r.status_code == 200 and r.json()["ok"] and r.json()["avatar_at"]
    header, sent = seen[0]
    assert header["op"] == "classify" and header["level"] == 1      # מחמיר — כמו תמונות החנות
    stored = client.get("/v1/device/avatar", headers=auth(token)).content
    assert stored == sent
    im = Image.open(io.BytesIO(stored))
    assert im.format == "JPEG" and im.size == (256, 256) and not im.getexif()
    assert client.get("/v1/device", headers=auth(token)).json()["avatar_at"] == r.json()["avatar_at"]
    # בדשבורד: ברשימת הפניות, בשיחה ובדף המכשיר
    client.post("/v1/device/support", headers=auth(token), json={"text": "שלום"})
    th = client.get("/v1/admin/support", headers=h).json()["threads"]
    assert next(t for t in th if t["device_id"] == did)["avatar_at"] == r.json()["avatar_at"]
    assert client.get(f"/v1/admin/support/{did}", headers=h).json()["device"]["avatar_at"] == r.json()["avatar_at"]
    assert client.get(f"/v1/admin/devices/{did}", headers=h).json()["avatar_at"] == r.json()["avatar_at"]
    img = client.get(f"/v1/admin/devices/{did}/avatar", headers=h)
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg" and img.content == stored
    assert client.get(f"/v1/admin/devices/{did}/avatar").status_code == 401   # בלי הרשאה — לא
    # הסרה
    assert client.delete("/v1/device/avatar", headers=auth(token)).status_code == 204
    assert client.get("/v1/device/avatar", headers=auth(token)).status_code == 404
    assert client.get("/v1/device", headers=auth(token)).json()["avatar_at"] is None


def test_avatar_rejected_by_the_filter_or_engine_down_is_not_stored(env, monkeypatch):
    svc, client = env
    _, token = active_customer(svc, client)
    _engine(monkeypatch, action="block")
    r = client.put("/v1/device/avatar", headers=auth(token), json={"image": base64.b64encode(_photo()).decode()})
    assert r.status_code == 422 and "לא אושרה" in r.json()["error"]
    assert client.get("/v1/device/avatar", headers=auth(token)).status_code == 404

    def down(*a, **k):
        raise ConnectionRefusedError()
    monkeypatch.setattr(S.protocol, "call_sync", down)
    r = client.put("/v1/device/avatar", headers=auth(token), json={"image": base64.b64encode(_photo()).decode()})
    assert r.status_code == 503
    assert client.get("/v1/device/avatar", headers=auth(token)).status_code == 404


def test_avatar_rejects_what_is_not_an_image(env, monkeypatch):
    svc, client = env
    _, token = active_customer(svc, client)
    _engine(monkeypatch)
    bad = client.put("/v1/device/avatar", headers=auth(token), json={"image": base64.b64encode(b"x" * 400).decode()})
    assert bad.status_code == 400
    assert client.put("/v1/device/avatar", headers=auth(token), json={"image": "!!not-base64!!" * 4}).status_code == 400
    png = client.put("/v1/device/avatar", headers=auth(token),
                     json={"image": base64.b64encode(_photo((300, 900), "PNG")).decode()})
    assert png.status_code == 200


def test_admin_device_sees_customer_avatars_in_the_app_console(env, monkeypatch):
    from test_support import _active_with_phone, configure
    svc, client = env
    _engine(monkeypatch)
    configure(client, admin_number="052-111-2233")
    _, admin_tok = _active_with_phone(client, "0521112233", 50, name="המנהל")
    cust_did, cust_tok = _active_with_phone(client, "0509998877", 51)
    at = client.put("/v1/device/avatar", headers=auth(cust_tok),
                    json={"image": base64.b64encode(_photo()).decode()}).json()["avatar_at"]
    client.post("/v1/device/support", headers=auth(cust_tok), json={"text": "שלום"})
    threads = client.get("/v1/device/admin/support", headers=auth(admin_tok)).json()["threads"]
    assert next(t for t in threads if t["device_id"] == cust_did)["avatar_at"] == at
    img = client.get(f"/v1/device/admin/avatar/{cust_did}", headers=auth(admin_tok))
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"
    assert client.get(f"/v1/device/admin/avatar/{cust_did}", headers=auth(cust_tok)).status_code == 403
