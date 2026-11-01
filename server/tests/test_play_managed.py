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
