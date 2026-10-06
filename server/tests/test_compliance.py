"""גרסאות מדיניות, דיווח תקופתי (heartbeat), מצב תאימות, חסימת אפליקציות במצבים, מנהל מכשיר ו-QR."""
import base64
import hashlib
import json

import pytest
from helpers import admin_headers, wg_key

from filterphone.api import compliance as C
from filterphone.api import db
from filterphone.api.app import device_limiter, enroll_limiter, heartbeat_limiter
from filterphone.api.catalog import DPC_DEFAULTS, PROTECTIONS, DeviceSettings, compile_settings


@pytest.fixture(autouse=True)
def _limits():
    enroll_limiter._hits.clear()
    device_limiter._hits.clear()
    heartbeat_limiter._hits.clear()


def enroll(svc, client, n=1, level=2):
    code = svc.create_code("t", level, 1, 1)["code"]
    r = client.post("/v1/enroll", json={"code": code, "public_key": wg_key(n), "name": f"טלפון {n}",
                                        "platform": "android", "app_version": "2.0.0"})
    assert r.status_code == 201, r.text
    j = r.json()
    return j["device_id"], {"Authorization": f"Bearer {j['token']}"}, j["vpn"]["address"].split("/")[0]


def row(svc, device_id):
    with svc.db() as c:
        return c.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()


# ------------------------------------------------------------------ גרסאות מדיניות


def test_policy_version_bumps_only_when_phone_policy_changes(env):
    svc, client = env
    h = admin_headers(client)
    did, dh, _ = enroll(svc, client)
    r0 = row(svc, did)
    assert r0["policy_version"] == 1 and r0["policy_checksum"] and r0["policy_updated_at"]
    pol = client.get("/v1/device", headers=dh).json()["device_policy"]
    assert pol["version"] == 1 and pol["checksum"] == r0["policy_checksum"]

    client.patch(f"/v1/admin/devices/{did}", headers=h, json={"name": "שם חדש"})
    assert row(svc, did)["policy_version"] == 1                 # שם — לא מדיניות
    client.patch(f"/v1/admin/devices/{did}", headers=h, json={"options": ["whatsapp_status"]})
    r1 = row(svc, did)
    assert r1["policy_version"] == 2 and r1["policy_revision"] > r0["policy_revision"]
    client.patch(f"/v1/admin/devices/{did}", headers=h, json={"options": ["whatsapp_status"]})
    assert row(svc, did)["policy_version"] == 2                 # אותו דבר שוב — בלי גרסה חדשה
    client.patch(f"/v1/admin/devices/{did}", headers=h, json={"level": 1})
    assert row(svc, did)["policy_version"] == 3
    pol = client.get("/v1/device", headers=dh).json()["device_policy"]
    assert pol["version"] == 3 and pol["blocked_screens"]


def test_backfill_gives_old_devices_a_checksum_without_a_new_version(env):
    svc, client = env
    did, _, _ = enroll(svc, client)
    with svc.db() as c:
        c.execute("UPDATE devices SET policy_checksum = '', policy_version = 1 WHERE id = ?", (did,))
    assert svc.backfill_policy_checksums() == 1
    r = row(svc, did)
    assert r["policy_checksum"] and r["policy_version"] == 1


# ------------------------------------------------------------------ heartbeat


def test_heartbeat_acks_policy_and_returns_new_policy_when_behind(env):
    svc, client = env
    h = admin_headers(client)
    did, dh, _ = enroll(svc, client)
    r = client.post("/v1/device/heartbeat", headers=dh, json={"policy_version": 1}).json()
    assert r["policy_version"] == 1 and "policy" not in r and r["interval_s"] == 900
    client.patch(f"/v1/admin/devices/{did}", headers=h, json={"custom_apps": ["com.example.game"]})
    r = client.post("/v1/device/heartbeat", headers=dh, json={"policy_version": 1}).json()
    assert r["policy_version"] == 2 and r["policy"]["version"] == 2
    assert r["policy"]["blocked_apps"] == ["com.example.game"]
    d = client.get(f"/v1/admin/devices/{did}", headers=h).json()
    assert d["policy_version"] == 2 and d["device_policy_version"] == 1
    assert d["compliance"]["policy_sync"] == "syncing"          # עוד בזמן החסד
    client.post("/v1/device/heartbeat", headers=dh, json={"policy_version": 2})
    d = client.get(f"/v1/admin/devices/{did}", headers=h).json()
    assert d["device_policy_version"] == 2 and d["compliance"]["policy_sync"] == "synced"
    assert d["policy_ack_at"]


def test_heartbeat_keeps_only_known_fields_and_types(env):
    svc, client = env
    did, dh, _ = enroll(svc, client)
    client.post("/v1/device/heartbeat", headers=dh, json={
        "policy_version": 1, "app_version": "2.1.0", "sdk": 34, "device_owner": "yes", "ca_installed": True,
        "vpn": {"state": "UP", "always_on": True, "lockdown": True, "secret": "x", "reconnects": "many"},
        "accessibility": {"enabled": True, "running": True}, "messages": ["private text"],
        "tamper": {"adb": False}, "private_dns": "off" * 100})
    hb = json.loads(row(svc, did)["heartbeat"])
    assert "messages" not in hb and "device_owner" not in hb and "secret" not in hb["vpn"]
    assert "reconnects" not in hb["vpn"] and hb["vpn"]["state"] == "UP" and hb["sdk"] == 34
    assert len(hb["private_dns"]) <= 80 and hb["via_vpn"] is False   # TestClient = לא דרך המנהרה
    assert row(svc, did)["app_version"] == "2.1.0"


def test_heartbeat_rate_limit(env):
    svc, client = env
    _, dh, _ = enroll(svc, client)
    codes = [client.post("/v1/device/heartbeat", headers=dh, json={}).status_code for _ in range(41)]
    assert codes[:40] == [200] * 40 and codes[40] == 429


def test_heartbeat_requires_device_token(env):
    _, client = env
    assert client.post("/v1/device/heartbeat", json={}).status_code == 401
    assert client.post("/v1/device/heartbeat", json={}, headers={"Authorization": "Bearer nope"}).status_code == 401


def test_transitions_go_to_device_log_and_compliance(env, monkeypatch):
    svc, client = env
    h = admin_headers(client)
    did, dh, ip = enroll(svc, client)
    client.patch(f"/v1/admin/devices/{did}", headers=h, json={"options": ["whatsapp_status"]})
    good = {"policy_version": 2, "device_owner": True, "ca_installed": True, "battery_unrestricted": True,
            "vpn": {"state": "UP", "always_on": True, "lockdown": True},
            "accessibility": {"enabled": True, "running": True}}
    monkeypatch.setattr("filterphone.api.app.client_ip", lambda request: ip)     # הגיע דרך המנהרה
    client.post("/v1/device/heartbeat", headers=dh, json=good)
    d = client.get(f"/v1/admin/devices/{did}", headers=h).json()
    assert d["compliance"]["state"] == "green", d["compliance"]
    assert d["compliance"]["management"] == "device_owner" and d["managed_at"]

    bad = {**good, "device_owner": False, "accessibility": {"enabled": False}}
    client.post("/v1/device/heartbeat", headers=dh, json=bad)
    d = client.get(f"/v1/admin/devices/{did}", headers=h).json()
    codes = {i["code"] for i in d["compliance"]["issues"]}
    assert d["compliance"]["state"] == "red" and {"management_removed", "accessibility_off"} <= codes
    kinds = [e["kind"] for e in d["device_log"]]
    assert "management_removed" in kinds and "accessibility_off" in kinds
    red = client.get("/v1/admin/devices?state=red", headers=h).json()
    assert [x["id"] for x in red] == [did]
    assert client.get("/v1/admin/devices?state=green", headers=h).json() == []
    assert [x["id"] for x in client.get("/v1/admin/devices?state=accessibility_off", headers=h).json()] == [did]
    assert [x["id"] for x in client.get("/v1/admin/devices?state=unmanaged", headers=h).json()] == [did]


def test_heartbeat_from_outside_the_tunnel_is_red(env, monkeypatch):
    svc, client = env
    h = admin_headers(client)
    did, dh, ip = enroll(svc, client)
    monkeypatch.setattr("filterphone.api.app.client_ip", lambda request: "203.0.113.9")
    client.post("/v1/device/heartbeat", headers=dh, json={"policy_version": 1, "vpn": {"state": "DOWN"}})
    d = client.get(f"/v1/admin/devices/{did}", headers=h).json()
    assert d["compliance"]["vpn"] == "down" and d["compliance"]["state"] == "red"
    assert "outside_vpn" in [e["kind"] for e in d["device_log"]] and d["heartbeat"]["via_vpn"] is False
    assert [x["id"] for x in client.get("/v1/admin/devices?state=vpn_inactive", headers=h).json()] == [did]


# ------------------------------------------------------------------ חישוב התאימות (יחידה)


def fake_row(**kw):
    base = {"status": "active", "heartbeat_at": None, "last_seen_at": None, "managed_at": None,
            "policy_version": 1, "device_policy_version": 1, "policy_updated_at": 0}
    return {**base, **kw}


def ev(row_, hb=None, guard=None, hs=0, dev=False, acc=False, now=1_000_000):
    return C.evaluate(row_, hb or {}, guard or {}, hs, dev, acc, now, 1800, 172800, 1500)


def test_compliance_states():
    now = 1_000_000
    hb = {"via_vpn": True, "vpn": {"state": "UP", "always_on": True, "lockdown": True}, "ca_installed": True,
          "battery_unrestricted": True, "accessibility": {"enabled": True}}
    assert ev(fake_row(heartbeat_at=now - 60), hb, hs=now - 30).state == C.GREEN
    # הטלפון נרדם (אין handshake) אבל הדיווח האחרון הגיע דרך המנהרה — עדיין "פעיל"
    assert ev(fake_row(heartbeat_at=now - 600), hb, hs=now - 900).vpn == "up"
    # דיווח שלא הגיע דרך המנהרה — כבוי, אדום
    r = ev(fake_row(heartbeat_at=now - 60), {**hb, "via_vpn": False})
    assert r.vpn == "down" and r.state == C.RED
    # בלי שום קשר שעה — צהוב (כבוי); יומיים — אדום
    r = ev(fake_row(heartbeat_at=now - 3600), hb)
    assert r.state == C.YELLOW and not r.online and [i["code"] for i in r.issues] == ["offline"]
    assert ev(fake_row(heartbeat_at=now - 200_000), hb).state == C.RED
    # גרסה ישנה בלי heartbeat — צהוב, לא "ירוק"
    r = ev(fake_row(last_seen_at=now - 10), {}, {"accessibility": True}, hs=now - 10)
    assert r.state == C.YELLOW and "no_heartbeat" in [i["code"] for i in r.issues]
    # מגן כבוי: אדום רק אם נדרש
    hb_off = {**hb, "accessibility": {"enabled": False}}
    assert ev(fake_row(heartbeat_at=now - 60), hb_off, hs=now - 30).state == C.GREEN
    assert ev(fake_row(heartbeat_at=now - 60), hb_off, hs=now - 30, dev=True).state == C.RED
    # עם מנהל מכשיר — חסימת אפליקציות בהסתרה, הנגישות לא נדרשת; למסכים כן
    hb_do = {**hb_off, "device_owner": True}
    assert ev(fake_row(heartbeat_at=now - 60), hb_do, hs=now - 30, dev=True).state == C.GREEN
    assert ev(fake_row(heartbeat_at=now - 60), hb_do, hs=now - 30, acc=True).state == C.RED
    # סוללה מוגבלת / VPN בלי lockdown — צהוב
    r = ev(fake_row(heartbeat_at=now - 60), {**hb, "battery_unrestricted": False,
                                             "vpn": {"state": "UP", "always_on": True, "lockdown": False}}, hs=now - 30)
    assert r.state == C.YELLOW and {i["code"] for i in r.issues} == {"battery_restricted", "lockdown_off"}
    # מדיניות שלא הגיעה אחרי זמן החסד
    r = ev(fake_row(heartbeat_at=now - 60, policy_version=5, device_policy_version=4, policy_updated_at=now - 4000),
           hb, hs=now - 30)
    assert r.policy_sync == "pending" and r.state == C.YELLOW
    # מכשיר לא פעיל
    assert ev(fake_row(status="disabled"), hb).state == C.INACTIVE


# ------------------------------------------------------------------ חסימת אפליקציות במצבים


def test_app_rules_network_accessibility_both(env):
    svc, client = env
    h = admin_headers(client)
    did, dh, ip = enroll(svc, client)
    rules = [{"package": "com.zhiliaoapp.musically", "mode": "network"},            # טיקטוק — שרתים ידועים
             {"package": "com.google.android.googlequicksearchbox", "mode": "accessibility"},
             {"package": "com.example.chat", "mode": "both", "hosts": ["chat.example.com"]},
             {"package": "com.unknown.app", "mode": "network"}]
    r = client.patch(f"/v1/admin/devices/{did}", headers=h, json={"app_rules": rules})
    assert r.status_code == 200, r.text
    pol = client.get("/v1/device", headers=dh).json()["device_policy"]
    # בטלפון: רק accessibility / both. רשימת מחרוזות — גם גרסה ישנה של האפליקציה מבינה
    assert pol["blocked_apps"] == ["com.example.chat", "com.google.android.googlequicksearchbox"]
    assert {r_["package"]: r_["mode"] for r_ in pol["app_rules"]}["com.zhiliaoapp.musically"] == "network"
    # ברשת: השרתים של טיקטוק ושל האפליקציה עם הכתובת הידנית — בפרוקסי וב-AdGuard
    proxy = json.loads(svc.redis.hget("fp:policy", ip))
    assert "tiktok.com" in proxy["hosts"] and "chat.example.com" in proxy["hosts"]
    assert any("tiktok.com" in rule and ip in rule for rule in svc.adguard_rules())
    d = client.get(f"/v1/admin/devices/{did}", headers=h).json()
    assert d["network_only_unknown"] == ["com.unknown.app"]      # אין שרתים ידועים — הממשק מזהיר
    assert d["settings"]["custom_apps"] == ["com.example.chat", "com.google.android.googlequicksearchbox"]
    # מצב לא חוקי
    bad = client.patch(f"/v1/admin/devices/{did}", headers=h, json={"app_rules": [{"package": "x.y", "mode": "dns"}]})
    assert bad.status_code == 400


def test_legacy_custom_apps_still_block_on_device():
    s = DeviceSettings.load(json.dumps({"custom_apps": ["com.old.app"]}))
    assert s.app_rules == {"com.old.app": {"mode": "accessibility"}}
    assert compile_settings(2, s).apps == ("com.old.app",)
    s.update({"custom_apps": ["com.old.app", "com.new.app"]})
    assert compile_settings(2, s).apps == ("com.new.app", "com.old.app")
    s.update({"custom_apps": []})
    assert compile_settings(2, s).apps == ()
    dumped = json.loads(s.dump())
    assert dumped["custom_apps"] == [] and "app_rules" in dumped


def test_whatsapp_profile_option_sends_detector_and_needs_accessibility():
    s = DeviceSettings(options=["whatsapp_profile"])
    comp = compile_settings(2, s)
    assert "pps.whatsapp.net" in comp.hosts
    assert comp.device_json()["protections"]["wa_profile_photo"] == PROTECTIONS["wa_profile_photo"]
    assert comp.needs_accessibility


# ------------------------------------------------------------------ מנהל מכשיר


def test_dpc_settings_validation_and_effective_policy(env):
    svc, client = env
    h = admin_headers(client)
    did, dh, _ = enroll(svc, client)
    assert client.get("/v1/device", headers=dh).json()["device_policy"]["dpc"] == DPC_DEFAULTS
    r = client.patch(f"/v1/admin/devices/{did}", headers=h, json={"dpc": {"disallow_debugging": True,
                                                                          "frp_accounts": ["123456789012"]}})
    assert r.status_code == 200
    dpc = client.get("/v1/device", headers=dh).json()["device_policy"]["dpc"]
    assert dpc["disallow_debugging"] is True and dpc["frp_accounts"] == ["123456789012"]
    assert client.patch(f"/v1/admin/devices/{did}", headers=h, json={"dpc": {"root": True}}).status_code == 400
    r = client.patch(f"/v1/admin/devices/{did}", headers=h, json={"dpc": {"frp_accounts": ["a@gmail.com"]}})
    assert r.status_code == 400 and "מספר" in r.json()["error"]


def test_provisioning_qr(env, tmp_path):
    svc, client = env
    h = admin_headers(client)
    object.__setattr__(svc.cfg, "apk_dir", str(tmp_path / "www"))
    assert client.post("/v1/admin/provisioning", headers=h, json={"level": 1}).status_code == 409
    (tmp_path / "www").mkdir()
    apk = tmp_path / "www" / "FilterPhone-universal.apk"
    apk.write_bytes(b"PK\x03\x04 fake apk")
    r = client.post("/v1/admin/provisioning", headers=h, json={"level": 2, "label": "משפחת לוי",
                                                                "wifi_ssid": "Home", "wifi_password": "pw123456"})
    assert r.status_code == 200, r.text
    j = r.json()
    p = j["payload"]
    want = base64.urlsafe_b64encode(hashlib.sha256(apk.read_bytes()).digest()).decode().rstrip("=")
    assert p["android.app.extra.PROVISIONING_DEVICE_ADMIN_PACKAGE_CHECKSUM"] == want
    assert p["android.app.extra.PROVISIONING_DEVICE_ADMIN_COMPONENT_NAME"] == "com.filterphone.app/.DeviceAdmin"
    assert p["android.app.extra.PROVISIONING_DEVICE_ADMIN_PACKAGE_DOWNLOAD_LOCATION"].endswith(
        "/download/FilterPhone-universal.apk")
    extras = p["android.app.extra.PROVISIONING_ADMIN_EXTRAS_BUNDLE"]
    assert extras["fp_code"] == j["code"]["code"] and j["code"]["uses_left"] == 1
    assert p["android.app.extra.PROVISIONING_WIFI_SSID"] == "Home"
    assert j["qr"].startswith("data:image/svg+xml")
    # הקוד מה-QR מפעיל מכשיר (פעם אחת)
    r = client.post("/v1/enroll", json={"code": extras["fp_code"], "public_key": wg_key(50), "name": "x"})
    assert r.status_code == 201 and r.json()["level"] == 2


def test_fleet_summary_and_metrics(env):
    svc, client = env
    h = admin_headers(client)
    enroll(svc, client, 1)
    f = client.get("/v1/admin/fleet", headers=h).json()
    assert f["total"] == 1 and sum(f["states"].values()) == 1
    text = client.get("/metrics").text
    assert "fp_devices_compliance{" in text
    o = client.get("/v1/admin/overview", headers=h).json()
    assert sum(o["compliance"].values()) == 1


def test_device_log_is_removed_with_device(env):
    svc, client = env
    h = admin_headers(client)
    did, dh, _ = enroll(svc, client)
    with svc.db() as c:
        svc.log_device(c, did, "vpn_down")
    assert client.delete(f"/v1/admin/devices/{did}", headers=h).status_code == 204
    with svc.db() as c:
        assert c.execute("SELECT count(*) FROM device_log").fetchone()[0] == 0
    assert db.VERSION == 2
