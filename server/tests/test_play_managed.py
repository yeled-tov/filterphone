"""Google Play פתוח ומנוהל: גלישה חופשית בחנות, התקנה רק של המאושרות (בטלפון), תמונות החנות חסומות."""
from filterphone.api.catalog import DeviceSettings, compile_settings, effective_options


def test_managed_play_replaces_the_locked_store_but_not_the_full_block():
    assert "play_store" in effective_options([])                       # ברירת המחדל לכולם: כניסה חסומה
    managed = effective_options(["play_managed"])
    assert "play_managed" in managed and "play_store" not in managed    # פתוח ומנוהל — בלי המסך הנעול
    full = effective_options(["play_managed", "play_store_full"])
    assert "play_store_full" in full and "play_managed" not in full     # החסימה המלאה גוברת


def test_managed_play_policy_for_the_phone():
    comp = compile_settings(1, DeviceSettings(options=["play_managed"]))
    dj = comp.device_json()
    assert "com.android.vending" not in dj["blocked_apps"]               # אפשר להיכנס לחנות
    assert "play_managed" in dj.get("protections", {})                    # האכיפה בטלפון
    assert "play-lh.googleusercontent.com" in comp.hosts                 # תמונות החנות חסומות ב-DNS


def test_the_phone_gets_the_titles_of_approved_apps(env):
    svc, client = env
    from helpers import admin_headers, wg_key
    h = admin_headers(client)
    reg = {"name": "משה", "phone": "0501234567", "email": "", "level": 1, "options": ["play_managed"], "note": "",
           "device_name": "גלקסי", "platform": "android", "app_version": "2.0.0"}
    r = client.post("/v1/register", json={**reg, "public_key": wg_key(1), "device_uid": "11" * 16})
    req = client.get("/v1/admin/requests", headers=h).json()[0]
    client.post(f"/v1/admin/requests/{req['id']}/approve", headers=h, json={"options": ["play_managed"]})
    policy = client.get("/v1/device", headers={"Authorization": f"Bearer {r.json()['token']}"}).json()["device_policy"]
    assert "com.waze" in policy["allowlist"]["apps"]
    assert any("waze" in t.lower() for t in policy["allowlist"]["titles"])     # מהקטלוג (וויז מאושרת כברירת מחדל)


def test_device_list_has_a_short_line_per_device(env):
    """רשימת המכשירים בשורה אחת: דגם ומצבים דחופים (בהשהיה) — בלי לפתוח את המכשיר."""
    from helpers import admin_headers

    from filterphone.api import db
    svc, client = env
    h = admin_headers(client)
    code = client.post("/v1/admin/codes", json={"label": "בדיקה", "level": 2}, headers=h).json()["code"]
    token = client.post("/v1/enroll", json={"code": code, "public_key": "A" * 43 + "="}).json()["token"]
    dh = {"Authorization": f"Bearer {token}"}
    client.post("/v1/device/heartbeat", headers=dh, json={
        "capabilities": {"sdk": 34, "manufacturer": "samsung", "model": "SM-S911B"},
        "pkg_log": [{"t": 1, "pkg": "com.einnovation.temu", "a": "replaced", "by": "com.android.vending",
                     "sys": True, "gate": False}, {"pkg": "bad pkg!"}, "junk"]})
    d = client.get("/v1/admin/devices", headers=h).json()[0]
    assert d["model"] == "Samsung SM-S911B"
    assert d["paused"] is False and d["unmanaging"] is False
    with svc.db() as c:
        c.execute("UPDATE devices SET pause_until = ? WHERE id = ?", (db.now() + 600, d["id"]))
    assert client.get("/v1/admin/devices", headers=h).json()[0]["paused"] is True
    hb = client.get(f"/v1/admin/devices/{d['id']}", headers=h).json()["heartbeat"]
    assert hb["pkg_log"] == [{"t": 1, "pkg": "com.einnovation.temu", "a": "replaced", "by": "com.android.vending",
                              "sys": True, "gate": False}]


def test_admin_answers_carry_the_dashboard_build(env):
    """הדשבורד (גם בתוך CRM FilterPhone) מתרענן לבד כשהגרסה משתנה — הגרסה בכל תשובה של /v1/admin."""
    from helpers import admin_headers
    _, client = env
    h = admin_headers(client)
    b = client.get("/v1/admin/devices", headers=h).headers.get("X-FP-Build")
    assert b and len(b) == 16
    assert client.get("/v1/admin/catalog", headers=h).headers.get("X-FP-Build") == b
    assert "X-FP-Build" not in client.get("/v1/catalog").headers
