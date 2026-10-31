"""אפליקציית המנהל (CRM FilterPhone): התראות על אירועים חדשים, והתקנה שקטה — רק במכשיר המנהל."""
import hashlib
import json

import pytest
from helpers import admin_headers, wg_key

from filterphone.api import db
from filterphone.api.app import device_limiter, heartbeat_limiter, register_limiter, support_limiter
from filterphone.api.service import CRM_PACKAGE


@pytest.fixture(autouse=True)
def _limits():
    for lim in (support_limiter, device_limiter, register_limiter, heartbeat_limiter):
        lim._hits.clear()


def auth(t):
    return {"Authorization": f"Bearer {t}"}


def register(client, phone, uid, key):
    reg = {"name": "משה כהן", "phone": phone, "email": "", "level": 1, "options": [], "note": "",
           "device_name": "גלקסי", "platform": "android", "app_version": "2.0.0"}
    r = client.post("/v1/register", json={**reg, "public_key": wg_key(key), "device_uid": uid})
    assert r.status_code in (200, 201), r.text
    return r.json()["device_id"], r.json()["token"]


def approve_all(client, h):
    for req in client.get("/v1/admin/requests", headers=h).json():
        if req["status"] == "pending":
            client.post(f"/v1/admin/requests/{req['id']}/approve", headers=h, json={"options": []})


def notifications(client, h, after=""):
    r = client.get("/v1/admin/notifications", params={"after": after}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def test_notifications_need_admin_login(env):
    _, client = env
    assert client.get("/v1/admin/notifications").status_code == 401


def test_first_call_returns_cursor_only_then_new_events(env):
    svc, client = env
    h = admin_headers(client)
    first = notifications(client, h)
    assert first["events"] == [] and first["cursor"] == "r0.s0.w0"

    # בקשת הצטרפות חדשה
    did, token = register(client, "0501234567", "11" * 16, 1)
    got = notifications(client, h, first["cursor"])
    assert [e["kind"] for e in got["events"]] == ["join"]
    assert got["events"][0]["title"] == "בקשת הצטרפות חדשה"
    assert "משה כהן" in got["events"][0]["text"] and got["events"][0]["route"] == "#/requests"
    assert got["counts"]["requests"] == 1
    # אותו סמן שוב — בלי כפילויות
    assert notifications(client, h, got["cursor"])["events"] == []

    # שתי הודעות תמיכה מאותו לקוח — התראה אחת, עם הטקסט האחרון
    approve_all(client, h)
    cur = notifications(client, h, got["cursor"])["cursor"]
    for text in ("שלום", "אין לי אינטרנט בכלל"):
        assert client.post("/v1/device/support", json={"text": text}, headers=auth(token)).status_code == 201
    got = notifications(client, h, cur)
    support = [e for e in got["events"] if e["kind"] == "support"]
    assert len(support) == 1
    assert support[0]["text"].startswith("אין לי אינטרנט בכלל") and "(2 הודעות)" in support[0]["text"]
    assert support[0]["route"] == f"#/support/{did}" and support[0]["key"] == f"s{did}"
    assert got["counts"]["support"] == 1

    # תשובה של המנהל — לא התראה
    cur = got["cursor"]
    assert client.post(f"/v1/admin/support/{did}", json={"text": "בודק"}, headers=h).status_code in (200, 201)
    assert notifications(client, h, cur)["events"] == []

    # בקשה לבדיקת אתר
    with svc.db() as c:
        c.execute("INSERT INTO web_reviews (device_id, host, url, note, created_at) VALUES (?, ?, '', '', ?)",
                  (did, "example.org", db.now()))
    got = notifications(client, h, cur)
    assert [(e["kind"], e["route"]) for e in got["events"]] == [("web", "#/web")]
    assert got["events"][0]["text"].startswith("example.org")


def test_bad_or_future_cursor_is_safe(env):
    _, client = env
    h = admin_headers(client)
    assert notifications(client, h, "garbage")["events"] == []
    register(client, "0501234567", "11" * 16, 1)
    # סמן מעבר לקיים (שחזור גיבוי) — לא נופל, ולא מציף
    got = notifications(client, h, "r999.s999.w999")
    assert got["events"] == [] and got["cursor"].startswith("r1.")


def publish_crm(svc, tmp_path, body=b"PK\x03\x04crm-apk", code=7, sha=None):
    www = tmp_path / "www"
    www.mkdir(exist_ok=True)
    object.__setattr__(svc.cfg, "apk_dir", str(www))
    (www / "CRM-FilterPhone.apk").write_bytes(body)
    (www / "CRM.json").write_text(json.dumps({
        "name": "CRM-FilterPhone.apk", "version_code": code, "version_name": f"1.0.{code}",
        "sha256": sha or hashlib.sha256(body).hexdigest(), "size": len(body)}))


def test_crm_is_installed_only_on_the_admin_device(env, tmp_path):
    svc, client = env
    publish_crm(svc, tmp_path)
    h = admin_headers(client)
    admin_id, admin_token = register(client, "0501234567", "11" * 16, 1)
    other_id, other_token = register(client, "0529999999", "22" * 16, 2)
    approve_all(client, h)
    with svc.db() as c:
        c.execute("INSERT INTO meta (key, value) VALUES ('admin_device_id', ?)"
                  " ON CONFLICT(key) DO UPDATE SET value = excluded.value", (str(admin_id),))

    def crm_in(token):
        apps = client.get("/v1/device", headers=auth(token)).json().get("managed_apps", [])
        hb = client.post("/v1/device/heartbeat", json={}, headers=auth(token)).json().get("managed_apps", [])
        return [a for a in apps if a["package"] == CRM_PACKAGE], [a for a in hb if a["package"] == CRM_PACKAGE]

    dev, hb = crm_in(admin_token)
    assert len(dev) == 1 and len(hb) == 1
    crm = dev[0]
    assert crm["required"] is True and crm["version_code"] == 7
    assert crm["url"] == "https://api.filterphone.com/download/CRM-FilterPhone.apk"
    assert crm["label"] == "CRM FilterPhone"
    # לקוח רגיל — לא מקבל ולא רואה אותה בחנות
    assert crm_in(other_token) == ([], [])
    # ברשימת המאושרות (כדי שלא תוסתר במכשיר המנהל)
    policy = client.get("/v1/device", headers=auth(admin_token)).json()["device_policy"]
    assert CRM_PACKAGE in policy["allowlist"]["apps"]


def test_crm_with_a_mismatched_file_is_not_offered(env, tmp_path):
    svc, _ = env
    publish_crm(svc, tmp_path, sha="0" * 64)
    assert svc.crm_release() is None
    publish_crm(svc, tmp_path, code=8)
    assert svc.crm_release()["version_code"] == 8
