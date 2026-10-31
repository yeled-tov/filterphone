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


# ---------------------------------------------------------------- צוות

def login(client, username, password, remember=False):
    return client.post("/v1/admin/login", json={"username": username, "password": password, "remember": remember})


def test_owner_adds_staff_who_can_work_but_not_manage_the_team(env):
    _, client = env
    h = admin_headers(client)
    team = client.get("/v1/admin/team", headers=h).json()
    assert team["me"]["role"] == "owner" and [m["username"] for m in team["members"]] == ["admin"]

    r = client.post("/v1/admin/team", headers=h, json={"username": "yossi", "password": "a-strong-pass-1"})
    assert r.status_code == 201 and r.json()["role"] == "staff"
    # אותו שם — לא
    assert client.post("/v1/admin/team", headers=h,
                       json={"username": "Yossi", "password": "a-strong-pass-1"}).status_code == 409
    # סיסמה קצרה / שם עם רווח — לא
    assert client.post("/v1/admin/team", headers=h, json={"username": "dan", "password": "short"}).status_code == 400
    assert client.post("/v1/admin/team", headers=h,
                       json={"username": "a b", "password": "a-strong-pass-1"}).status_code == 400

    # איש הצוות נכנס (גם לאפליקציה — 30 יום) ועובד בדשבורד
    lr = login(client, "yossi", "a-strong-pass-1", remember=True)
    assert lr.status_code == 200 and lr.json()["role"] == "staff"
    hs = {"Authorization": f"Bearer {lr.json()['token']}"}
    assert client.get("/v1/admin/devices", headers=hs).status_code == 200
    assert client.get("/v1/admin/notifications", headers=hs).status_code == 200
    assert client.get("/v1/admin/overview", headers=hs).json()["admin_role"] == "staff"
    # ...אבל לא מנהל את הצוות
    assert client.get("/v1/admin/team", headers=hs).json()["members"] == []
    assert client.post("/v1/admin/team", headers=hs,
                       json={"username": "eve", "password": "a-strong-pass-1"}).status_code == 403
    admin_id = team["members"][0]["id"]
    assert client.delete(f"/v1/admin/team/{admin_id}", headers=hs).status_code == 403

    # הבעלים מחליף לו סיסמה — החיבור שלו נסגר
    yid = r.json()["id"]
    assert client.patch(f"/v1/admin/team/{yid}", headers=h, json={"password": "another-pass-22"}).status_code == 200
    assert client.get("/v1/admin/devices", headers=hs).status_code == 401
    assert login(client, "yossi", "another-pass-22").status_code == 200

    # הסרה — אי אפשר להיכנס יותר
    assert client.delete(f"/v1/admin/team/{yid}", headers=h).status_code == 204
    assert login(client, "yossi", "another-pass-22").status_code == 401


def test_the_last_owner_cannot_be_removed_or_demoted(env):
    _, client = env
    h = admin_headers(client)
    me = client.get("/v1/admin/team", headers=h).json()["me"]
    assert client.delete(f"/v1/admin/team/{me['id']}", headers=h).status_code == 409          # לא את עצמך
    assert client.patch(f"/v1/admin/team/{me['id']}", headers=h, json={"role": "staff"}).status_code == 409
    # בעלים נוסף — עכשיו אפשר
    other = client.post("/v1/admin/team", headers=h,
                        json={"username": "partner", "password": "a-strong-pass-1", "role": "owner"}).json()
    assert other["role"] == "owner"
    assert client.patch(f"/v1/admin/team/{other['id']}", headers=h, json={"role": "staff"}).json()["role"] == "staff"


def test_each_member_changes_their_own_password(env):
    _, client = env
    h = admin_headers(client)
    other_session = admin_headers(client)       # עוד חיבור (למשל באפליקציה)
    bad = client.post("/v1/admin/me/password", headers=h, json={"current": "wrong", "new": "brand-new-pass-1"})
    assert bad.status_code == 400
    ok = client.post("/v1/admin/me/password", headers=h,
                     json={"current": "correct horse battery", "new": "brand-new-pass-1"})
    assert ok.status_code == 204
    assert client.get("/v1/admin/devices", headers=h).status_code == 200              # החיבור הנוכחי נשאר
    assert client.get("/v1/admin/devices", headers=other_session).status_code == 401  # השאר נסגרו
    assert login(client, "admin", "brand-new-pass-1").status_code == 200


def test_owner_gives_the_crm_app_to_a_staff_members_filtered_phone(env, tmp_path):
    svc, client = env
    publish_crm(svc, tmp_path)
    h = admin_headers(client)
    did, token = register(client, "0529999999", "22" * 16, 2)
    approve_all(client, h)

    def has_crm():
        apps = client.get("/v1/device", headers=auth(token)).json().get("managed_apps", [])
        return any(a["package"] == CRM_PACKAGE for a in apps)

    assert not has_crm()
    assert client.get(f"/v1/admin/devices/{did}", headers=h).json()["crm_app"] is False
    assert client.put(f"/v1/admin/devices/{did}/crm", headers=h, json={"enabled": True}).json() == {"crm_app": True}
    assert has_crm()
    assert client.get(f"/v1/admin/devices/{did}", headers=h).json()["crm_app"] is True
    # איש צוות (לא בעלים) — לא יכול
    client.post("/v1/admin/team", headers=h, json={"username": "yossi", "password": "a-strong-pass-1"})
    hs = {"Authorization": f"Bearer {login(client, 'yossi', 'a-strong-pass-1').json()['token']}"}
    assert client.put(f"/v1/admin/devices/{did}/crm", headers=hs, json={"enabled": False}).status_code == 403
    assert client.put(f"/v1/admin/devices/{did}/crm", headers=h, json={"enabled": False}).status_code == 200
    assert not has_crm()
