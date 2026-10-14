"""גרסאות מדיניות, דיווח תקופתי (heartbeat), מצב תאימות, חסימת אפליקציות במצבים, מנהל מכשיר ו-QR."""
import base64
import hashlib
import json

import pytest
from helpers import admin_headers, wg_key

from filterphone.api import compliance as C
from filterphone.api import db
from filterphone.api.app import device_limiter, enroll_limiter, heartbeat_limiter, rekey_limiter
from filterphone.api.catalog import DPC_DEFAULTS, PROTECTIONS, DeviceSettings, compile_settings


@pytest.fixture(autouse=True)
def _limits():
    enroll_limiter._hits.clear()
    device_limiter._hits.clear()
    heartbeat_limiter._hits.clear()
    rekey_limiter._hits.clear()


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
    # אבל במצב "רענון יומי" (חיסכון סוללה) — ייושם בפתיחה הבאה, לא תקלה (נשאר ירוק)
    r = ev(fake_row(heartbeat_at=now - 60, policy_version=5, device_policy_version=4, policy_updated_at=now - 4000),
           {**hb, "sync_mode": "daily"}, hs=now - 30)
    assert r.policy_sync == "deferred" and r.state == C.GREEN
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
    # בטלפון: רק accessibility / both. רשימת מחרוזות — גם גרסה ישנה של האפליקציה מבינה.
    # אפליקציית גוגל — רק המסכים שלה (ג'מיני ו-Circle to Search רצים מתוכה וממשיכים לעבוד)
    assert pol["blocked_apps"] == ["com.example.chat"]
    assert "com.google.android.googlequicksearchbox" in {s["package"] for s in pol["blocked_screens"]}
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
    assert db.VERSION == 9


def test_rekey_keeps_address_and_policy_and_swaps_wireguard_peer(env):
    svc, client = env
    did, dh, ip = enroll(svc, client, 3)
    old = wg_key(3)
    assert old in svc.wg.peers_
    r = client.post("/v1/device/rekey", headers=dh, json={"public_key": wg_key(33)})
    assert r.status_code == 200, r.text
    assert r.json()["address"] == f"{ip}/32"
    assert wg_key(33) in svc.wg.peers_ and old not in svc.wg.peers_
    assert row(svc, did)["public_key"] == wg_key(33)
    # מפתח של מכשיר אחר — אסור
    enroll(svc, client, 4)
    assert client.post("/v1/device/rekey", headers=dh, json={"public_key": wg_key(4)}).status_code == 409
    assert client.post("/v1/device/rekey", headers=dh, json={"public_key": "x" * 44}).status_code == 400
    codes = [client.post("/v1/device/rekey", headers=dh, json={"public_key": wg_key(60 + i)}).status_code
             for i in range(3)]
    assert codes[-1] == 429


def test_vpn_config_has_ip_fallback_when_configured(env):
    svc, client = env
    object.__setattr__(svc.cfg, "public_ip", "151.145.88.42")
    _, dh, _ = enroll(svc, client, 5)
    vpn = client.get("/v1/device/vpn", headers=dh).json()
    assert vpn["endpoint"] == "vpn.filterphone.com:51820" and vpn["endpoint_fallback"] == "151.145.88.42:51820"


# ------------------------------------------------------------------ עדכון האפליקציה


def _publish(www, files, version_name="1.0.7"):
    www.mkdir(exist_ok=True)
    entries = []
    for name, (data, code, abis) in files.items():
        (www / name).write_bytes(data)
        entries.append({"name": name, "version_code": code, "abis": abis, "size": len(data),
                        "sha256": hashlib.sha256(data).hexdigest()})
    (www / "FilterPhone.json").write_text(json.dumps({"version_name": version_name, "files": entries}))


def test_heartbeat_offers_published_release_verified_against_the_files(env, tmp_path):
    svc, client = env
    www = tmp_path / "www"
    object.__setattr__(svc.cfg, "apk_dir", str(www))
    did, dh, _ = enroll(svc, client)
    assert "app_update" not in client.post("/v1/device/heartbeat", headers=dh, json={}).json()   # לא פורסם

    _publish(www, {"FilterPhone.apk": (b"PK arm64 build", 2007, ["arm64-v8a"]),
                   "FilterPhone-universal.apk": (b"PK universal build", 7, ["arm64-v8a", "armeabi-v7a", "x86_64"])})
    up = client.post("/v1/device/heartbeat", headers=dh, json={}).json()["app_update"]
    assert up["version_name"] == "1.0.7"
    by_name = {f["url"].rsplit("/", 1)[1]: f for f in up["files"]}
    assert by_name["FilterPhone.apk"]["version_code"] == 2007
    assert by_name["FilterPhone.apk"]["url"] == "https://api.filterphone.com/download/FilterPhone.apk"
    assert by_name["FilterPhone.apk"]["sha256"] == hashlib.sha256(b"PK arm64 build").hexdigest()
    assert by_name["FilterPhone-universal.apk"]["abis"] == ["arm64-v8a", "armeabi-v7a", "x86_64"]

    # הקובץ הוחלף בלי שהרשימה עודכנה — לא מציעים אותו (רק את מה שעדיין תואם)
    (www / "FilterPhone.apk").write_bytes(b"PK arm64 BUILD")          # אותו גודל, תוכן אחר
    up = client.post("/v1/device/heartbeat", headers=dh, json={}).json()["app_update"]
    assert [f["url"].rsplit("/", 1)[1] for f in up["files"]] == ["FilterPhone-universal.apk"]

    # רשימה פגומה / שם קובץ לא חוקי — לא מציעים כלום, ולא נופלים
    (www / "FilterPhone.json").write_text("{not json")
    assert "app_update" not in client.post("/v1/device/heartbeat", headers=dh, json={}).json()
    (www / "FilterPhone.json").write_text(json.dumps({"files": [{"name": "../../etc/passwd", "version_code": 9,
                                                                 "sha256": "0" * 64, "size": 1}]}))
    assert "app_update" not in client.post("/v1/device/heartbeat", headers=dh, json={}).json()
    assert svc.device_detail(did)["latest_app_version"] is None


def test_heartbeat_logs_app_update_and_failure(env, tmp_path):
    svc, client = env
    h = admin_headers(client)
    object.__setattr__(svc.cfg, "apk_dir", str(tmp_path / "www"))
    _publish(tmp_path / "www", {"FilterPhone.apk": (b"PK new", 2008, ["arm64-v8a"])}, "1.0.8")
    did, dh, _ = enroll(svc, client)
    client.post("/v1/device/heartbeat", headers=dh, json={"app_version": "1.0.7", "device_owner": True})
    client.post("/v1/device/heartbeat", headers=dh, json={
        "app_version": "1.0.7", "device_owner": True,
        "update": {"state": "failed", "version": "1.0.8", "error": "sha256", "junk": "x"}})
    client.post("/v1/device/heartbeat", headers=dh, json={"app_version": "1.0.8", "device_owner": True})
    d = client.get(f"/v1/admin/devices/{did}", headers=h).json()
    kinds = [e["kind"] for e in d["device_log"]]
    assert "app_update_failed" in kinds and "app_updated" in kinds
    assert next(e for e in d["device_log"] if e["kind"] == "app_updated")["detail"] == "1.0.7 → 1.0.8"
    assert d["latest_app_version"] == "1.0.8"
    assert row(svc, did)["app_version"] == "1.0.8"


# ------------------------------------------------------------------ חסימת מילות חיפוש


def test_keyword_block_is_on_by_default_in_device_policy(env):
    svc, client = env
    did, dh, _ = enroll(svc, client)
    kw = client.get("/v1/device", headers=dh).json()["device_policy"]["keywords"]
    assert kw["enabled"] is True and kw["action"] == "back"
    assert "sex" in kw["terms"] and "porn" in kw["terms"] and "בקיני" in kw["terms"]
    # דורש נגישות — ולכן מופיע כחסימה פעילה בסיכום
    assert svc.compiled(row(svc, did)).needs_accessibility
    assert any(b["id"] == "keyword_block" for b in client.get(f"/v1/admin/devices/{did}",
                                                               headers=admin_headers(client)).json()["blocks"])


def test_admin_can_disable_add_and_remove_keywords(env):
    svc, client = env
    h = admin_headers(client)
    did, dh, _ = enroll(svc, client)
    base = len(client.get("/v1/device", headers=dh).json()["device_policy"]["keywords"]["terms"])

    r = client.patch(f"/v1/admin/devices/{did}", headers=h,
                     json={"keywords": {"extra": ["MyBadWord", "ביטוי רע"], "removed": ["bikini"]}})
    assert r.status_code == 200, r.text
    kw = client.get("/v1/device", headers=dh).json()["device_policy"]["keywords"]
    assert "mybadword" in kw["terms"] and "ביטוי רע" in kw["terms"]   # מנורמל לאותיות קטנות
    assert "bikini" not in kw["terms"]                                 # הוסר מברירת המחדל
    assert len(kw["terms"]) == base + 2 - 1
    # גרסת המדיניות עלתה — הטלפון יקבל עדכון
    assert row(svc, did)["policy_version"] >= 2

    client.patch(f"/v1/admin/devices/{did}", headers=h, json={"keywords": {"enabled": False}})
    kw = client.get("/v1/device", headers=dh).json()["device_policy"]["keywords"]
    assert kw["enabled"] is False and kw["terms"]           # הרשימה נשמרת, רק כבויה
    d = client.get(f"/v1/admin/devices/{did}", headers=h).json()
    assert d["settings"]["keywords_effective"]["enabled"] is False
    assert sorted(d["settings"]["keywords"]["extra"]) == sorted(["ביטוי רע", "mybadword"])


def test_keyword_changes_survive_dump_load_roundtrip(env):
    svc, client = env
    h = admin_headers(client)
    did, dh, _ = enroll(svc, client)
    client.patch(f"/v1/admin/devices/{did}", headers=h, json={"keywords": {"enabled": False, "extra": ["zzz"]}})
    from filterphone.api.catalog import DeviceSettings
    s = DeviceSettings.load(row(svc, did)["policy"])
    assert s.keywords == {"enabled": False, "extra": ["zzz"]}


def test_heartbeat_stores_capabilities_and_preflight(env):
    svc, client = env
    h = admin_headers(client)
    did, dh, _ = enroll(svc, client)
    client.post("/v1/device/heartbeat", headers=dh, json={
        "preflight": "REQUIRES_FACTORY_RESET",
        "capabilities": {"sdk": 34, "manufacturer": "Samsung", "model": "SM-A546",
                         "device_owner": False, "provisioning_allowed": False, "root": False,
                         "always_on": True, "lockdown": True, "accessibility": True, "junk": 1}})
    d = client.get(f"/v1/admin/devices/{did}", headers=h).json()
    hb = d["heartbeat"]
    assert hb["preflight"] == "REQUIRES_FACTORY_RESET"
    assert hb["capabilities"]["model"] == "SM-A546" and hb["capabilities"]["always_on"] is True
    assert "junk" not in hb["capabilities"]


# ------------------------------------------------------------------ השהיית הגנה / הסרת ניהול


def test_pause_sets_passthrough_and_delivers_admin_command(env):
    svc, client = env
    h = admin_headers(client)
    did, dh, ip = enroll(svc, client, level=1)
    import json as _json
    # לפני: רמה 1 עם סינון תמונות
    assert _json.loads(svc.redis.hget("fp:policy", ip))["level"] == 1

    r = client.post(f"/v1/admin/devices/{did}/pause", headers=h, json={"minutes": 30, "reason": "תחזוקה"})
    assert r.status_code == 200, r.text
    assert r.json()["pause"]["active"] is True and r.json()["pause"]["reason"] == "תחזוקה"
    # הפרוקסי עבר ל-passthrough (level 0)
    assert int(svc.redis.hget("fp:levels", ip)) == 0
    assert _json.loads(svc.redis.hget("fp:policy", ip))["level"] == 0
    # הטלפון מקבל פקודת השהיה ב-heartbeat (עם nonce ותוקף)
    hb = client.post("/v1/device/heartbeat", headers=dh, json={}).json()
    assert hb["admin"]["action"] == "pause" and hb["admin"]["nonce"] and hb["admin"]["until"] > hb["server_time"]
    assert any(e["kind"] == "paused" for e in client.get(f"/v1/admin/devices/{did}", headers=h).json()["device_log"])
    assert any(a["action"] == "pause" for a in svc.list_audit())


def test_resume_restores_full_filtering(env):
    svc, client = env
    h = admin_headers(client)
    did, dh, ip = enroll(svc, client, level=1)
    client.post(f"/v1/admin/devices/{did}/pause", headers=h, json={"minutes": 30})
    import json as _json
    assert _json.loads(svc.redis.hget("fp:policy", ip))["level"] == 0
    r = client.post(f"/v1/admin/devices/{did}/resume", headers=h)
    assert r.status_code == 200 and r.json()["pause"] is None
    assert int(svc.redis.hget("fp:levels", ip)) == 1
    assert "admin" not in client.post("/v1/device/heartbeat", headers=dh, json={}).json()


def test_expired_pause_auto_restores_on_next_heartbeat(env):
    svc, client = env
    h = admin_headers(client)
    did, dh, ip = enroll(svc, client, level=1)
    client.post(f"/v1/admin/devices/{did}/pause", headers=h, json={"minutes": 30})
    client.post("/v1/device/heartbeat", headers=dh, json={})            # נרשם "_was_paused"
    with svc.db() as c, db.transaction(c):                               # מזייפים פקיעת תוקף
        c.execute("UPDATE devices SET pause_until = ? WHERE id = ?", (db.now() - 5, did))
    hb = client.post("/v1/device/heartbeat", headers=dh, json={}).json()
    assert "admin" not in hb                                            # כבר לא מושהה
    assert int(svc.redis.hget("fp:levels", ip)) == 1                    # הסינון חזר לבד
    assert svc.device_detail(did)["pause"] is None


def test_unmanage_request_then_device_confirms(env):
    svc, client = env
    h = admin_headers(client)
    did, dh, _ = enroll(svc, client)
    client.post("/v1/device/heartbeat", headers=dh, json={"device_owner": True})
    assert client.post(f"/v1/admin/devices/{did}/unmanage", headers=h).json()["unmanage_requested"] is True
    hb = client.post("/v1/device/heartbeat", headers=dh, json={"device_owner": True}).json()
    assert hb["admin"]["action"] == "unmanage"
    # הטלפון אישר שהניהול הוסר
    client.post("/v1/device/heartbeat", headers=dh, json={"device_owner": False})
    d = client.get(f"/v1/admin/devices/{did}", headers=h).json()
    assert d["unmanage_requested"] is False
    assert any(e["kind"] == "management_cleared" for e in d["device_log"])
    assert any(a["action"] == "unmanage" for a in svc.list_audit())


def test_cannot_pause_inactive_device(env):
    svc, client = env
    h = admin_headers(client)
    did, _, _ = enroll(svc, client)
    client.patch(f"/v1/admin/devices/{did}", headers=h, json={"status": "disabled"})
    # מכשיר שאינו פעיל — לא ניתן להשהות
    assert client.post(f"/v1/admin/devices/{did}/pause", headers=h, json={"minutes": 10}).status_code == 409


# ------------------------------------------------------------------ החלפת מכשיר


def test_device_transfer_inherits_policy_and_disables_old(env):
    svc, client = env
    h = admin_headers(client)
    did, dh, old_ip = enroll(svc, client, level=1)
    # נותנים למכשיר הישן מדיניות ייחודית שתעבור לחדש
    client.patch(f"/v1/admin/devices/{did}", headers=h, json={"level": 2, "options": ["whatsapp_status"]})

    r = client.post(f"/v1/admin/devices/{did}/transfer", headers=h)
    assert r.status_code == 200, r.text
    token = r.json()["token"]

    # הטלפון החדש נרשם עם טוקן ההחלפה
    new = client.post("/v1/transfer", json={"token": token, "public_key": wg_key(80), "name": "טלפון חדש",
                                            "platform": "android"})
    assert new.status_code == 201, new.text
    nj = new.json()
    assert nj["level"] == 2 and nj["vpn"]["address"].split("/")[0] != old_ip
    new_id = nj["device_id"]
    nh = {"Authorization": f"Bearer {nj['token']}"}
    # יורש את המדיניות (רמה 2 + הסטטוסים)
    pol = client.get("/v1/device", headers=nh).json()
    assert pol["level"] == 2 and any(b["id"] == "whatsapp_status" for b in pol["blocks"])
    # הישן הושבת ואין לו גישה
    assert row(svc, did)["status"] == "disabled"
    assert client.get("/v1/device", headers=dh).json()["status"] == "disabled"
    # הטוקן חד-פעמי — לא ניתן לשימוש חוזר
    assert client.post("/v1/transfer", json={"token": token, "public_key": wg_key(81),
                                             "platform": "android"}).status_code == 403
    assert any(a["action"] == "transfer_done" for a in svc.list_audit())
    d = client.get(f"/v1/admin/devices/{new_id}", headers=h).json()
    assert any(e["kind"] == "transfer_in" for e in d["device_log"])


def test_expired_transfer_token_rejected(env):
    svc, client = env
    h = admin_headers(client)
    did, _, _ = enroll(svc, client)
    token = client.post(f"/v1/admin/devices/{did}/transfer", headers=h).json()["token"]
    with svc.db() as c, db.transaction(c):
        c.execute("UPDATE devices SET transfer_expires = ? WHERE id = ?", (db.now() - 5, did))
    assert client.post("/v1/transfer", json={"token": token, "public_key": wg_key(82),
                                             "platform": "android"}).status_code == 403


# ------------------------------------------------------------------ פרופילים


def test_builtin_profiles_seeded_and_applied(env):
    svc, client = env
    h = admin_headers(client)
    profs = client.get("/v1/admin/profiles", headers=h).json()["profiles"]
    assert len(profs) >= 5 and all(p["builtin"] for p in profs)
    basic = next(p for p in profs if p["level"] == 3)   # "בסיסי — רק עירום"
    did, dh, _ = enroll(svc, client, level=1)

    r = client.post(f"/v1/admin/devices/{did}/apply-profile", headers=h, json={"profile_id": basic["id"]})
    assert r.status_code == 200, r.text
    assert row(svc, did)["level"] == 3 and row(svc, did)["policy_version"] >= 2
    pol = client.get("/v1/device", headers=dh).json()
    assert pol["level"] == 3
    assert any(e["kind"] == "profile_applied" for e in client.get(f"/v1/admin/devices/{did}", headers=h).json()["device_log"])
    assert any(a["action"] == "profile_apply" for a in svc.list_audit())


def test_profile_crud_and_builtin_protection(env):
    svc, client = env
    h = admin_headers(client)
    # יצירה
    r = client.post("/v1/admin/profiles", headers=h,
                    json={"name": "מותאם אישית", "level": 2, "settings": {"options": ["tiktok", "instagram"]}})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    # שם כפול — נדחה
    assert client.post("/v1/admin/profiles", headers=h,
                       json={"name": "מותאם אישית", "level": 1, "settings": {}}).status_code == 409
    # עריכה
    assert client.patch(f"/v1/admin/profiles/{pid}", headers=h,
                        json={"name": "מותאם אישית", "level": 1, "settings": {"options": ["tiktok"]}}).status_code == 200
    prof = next(p for p in client.get("/v1/admin/profiles", headers=h).json()["profiles"] if p["id"] == pid)
    assert prof["level"] == 1 and prof["settings"]["options"] == ["tiktok"]
    # מחיקה של מותאם — מותר; של מובנה — אסור
    assert client.delete(f"/v1/admin/profiles/{pid}", headers=h).status_code == 204
    builtin = next(p for p in client.get("/v1/admin/profiles", headers=h).json()["profiles"] if p["builtin"])
    assert client.delete(f"/v1/admin/profiles/{builtin['id']}", headers=h).status_code == 409


def test_profile_rejects_bad_settings(env):
    svc, client = env
    h = admin_headers(client)
    assert client.post("/v1/admin/profiles", headers=h,
                       json={"name": "רע", "level": 2, "settings": {"options": ["no_such_option"]}}).status_code == 400
    assert client.post("/v1/admin/profiles", headers=h,
                       json={"name": "רע", "level": 9, "settings": {}}).status_code == 400


# ------------------------------------------------------------------ קטלוג אפליקציות מאושרות


def test_managed_app_catalog_crud_and_delivery(env):
    svc, client = env
    h = admin_headers(client)
    did, dh, _ = enroll(svc, client)
    # מסירים את FilterTube שנזרע כברירת מחדל כדי לבדוק CRUD נקי
    for a in client.get("/v1/admin/apps", headers=h).json()["apps"]:
        client.delete(f"/v1/admin/apps/{a['id']}", headers=h)
    assert client.get("/v1/admin/apps", headers=h).json()["apps"] == []

    # אפליקציה מחנות (בלי APK) — להתקנה ידנית
    r1 = client.post("/v1/admin/apps", headers=h, json={"package": "com.whatsapp", "label": "וואטסאפ",
                                                        "category": "תקשורת"})
    assert r1.status_code == 201, r1.text
    # אפליקציה עם APK שמתארח אצלנו — להתקנה אוטומטית במנהל מכשיר
    sha = "a" * 64
    r2 = client.post("/v1/admin/apps", headers=h, json={
        "package": "com.example.kosher", "label": "אפליקציה מאושרת", "required": True,
        "apk_url": "https://api.filterphone.com/apps/kosher.apk", "apk_sha256": sha.upper(), "version_code": 5})
    assert r2.status_code == 201, r2.text

    # הטלפון מקבל את הקטלוג המאושר
    apps = client.get("/v1/device", headers=dh).json()["managed_apps"]
    by_pkg = {a["package"]: a for a in apps}
    assert "url" not in by_pkg["com.whatsapp"]                      # התקנה ידנית — בלי שדות התקנה
    assert by_pkg["com.example.kosher"]["required"] is True
    assert by_pkg["com.example.kosher"]["sha256"] == sha and by_pkg["com.example.kosher"]["version_code"] == 5
    assert by_pkg["com.example.kosher"]["url"].startswith("https://")

    # כפילות נדחית; http (לא https) נדחה; sha חסר לאפליקציה עם url נדחה
    assert client.post("/v1/admin/apps", headers=h, json={"package": "com.whatsapp"}).status_code == 409
    assert client.post("/v1/admin/apps", headers=h, json={"package": "com.x.y",
                                                          "apk_url": "http://x/a.apk", "apk_sha256": sha,
                                                          "version_code": 1}).status_code == 400
    assert client.post("/v1/admin/apps", headers=h, json={"package": "com.x.z",
                                                          "apk_url": "https://x/a.apk"}).status_code == 400

    # כיבוי אפליקציה מסיר אותה מהקטלוג של הטלפון
    app_id = r1.json()["id"]
    client.patch(f"/v1/admin/apps/{app_id}", headers=h, json={"package": "com.whatsapp", "enabled": False})
    assert "com.whatsapp" not in {a["package"] for a in client.get("/v1/device", headers=dh).json()["managed_apps"]}
    client.delete(f"/v1/admin/apps/{r2.json()['id']}", headers=h)
    assert client.get("/v1/admin/apps", headers=h).json()["apps"][0]["package"] == "com.whatsapp"
    assert any(a["action"] == "app_add" for a in svc.list_audit())


# ------------------------------------------------------------------ חנות: העלאת APK


def test_admin_uploads_apk_and_phones_get_a_verified_install(env):
    from pathlib import Path
    svc, client = env
    h = admin_headers(client)
    data = (Path(__file__).parent / "fixtures" / "mini.apk").read_bytes()
    r = client.put("/v1/admin/apps/upload", headers={**h, "Content-Type": "application/vnd.android.package-archive"},
                   content=data)
    assert r.status_code == 201, r.text
    j = r.json()
    assert (j["package"], j["version_code"], j["version_name"], j["label"]) == ("org.example.kosher.notes", 4207,
                                                                                   "3.1.4", "Notes")
    assert j["sha256"] == hashlib.sha256(data).hexdigest()
    assert j["url"].endswith("/v1/apps/org.example.kosher.notes-4207.apk")
    # הקובץ מוגש, ושם קובץ לא חוקי — לא
    got = client.get("/v1/apps/org.example.kosher.notes-4207.apk")
    assert got.status_code == 200 and got.content == data
    assert client.get("/v1/apps/..%2Fetc%2Fpasswd").status_code == 404
    # הטלפון מקבל את פרטי ההתקנה בקטלוג
    did, dh, _ = enroll(svc, client)
    store = client.get("/v1/device", headers=dh).json()["managed_apps"]
    app = next(a for a in store if a["package"] == "org.example.kosher.notes")
    assert app["sha256"] == j["sha256"] and app["version_code"] == 4207 and app["url"] == j["url"]
    # קובץ שאינו APK — נדחה, בלי להשאיר שאריות
    bad = client.put("/v1/admin/apps/upload", headers=h, content=b"not an apk")
    assert bad.status_code == 400
    assert [p.name for p in svc.apps_dir().iterdir()] == ["org.example.kosher.notes-4207.apk"]
    # בלי הרשאת מנהל — אסור
    assert client.put("/v1/admin/apps/upload", content=data).status_code in (401, 403)


def test_default_options_apply_to_code_enrolled_devices(env):
    """קוד שנוצר מהדשבורד בלי אפשרויות → ברירת המחדל ללקוח חדש: מחמיר, רשתות, בלי דפדפן, חנויות חסומות."""
    from filterphone.api.catalog import DEFAULT_OPTIONS
    svc, client = env
    h = admin_headers(client)
    # יצירת קוד דרך ה-API בלי options → ברירת מחדל
    r = client.post("/v1/admin/codes", headers=h, json={"label": "לקוח חדש", "level": 1, "uses": 1})
    assert r.status_code == 201, r.text
    code = r.json()["code"]
    assert set(r.json()["options"]) == set(DEFAULT_OPTIONS)
    # הפעלה בקוד — המכשיר מקבל את החסימות
    e = client.post("/v1/enroll", json={"code": code, "public_key": wg_key(60), "name": "גלקסי",
                                        "platform": "android", "app_version": "2.0.0"}).json()
    me = client.get("/v1/device", headers={"Authorization": f"Bearer {e['token']}"}).json()
    blocks = {b["id"] for b in me["blocks"]}
    assert {"instagram", "tiktok", "browsers_all", "telegram", "youtube", "play_store", "galaxy_store"} <= blocks
    assert me["level"] == 1
    # קוד עם רשימה ריקה מפורשת → בלי חסימות
    r2 = client.post("/v1/admin/codes", headers=h, json={"label": "ללא", "level": 2, "options": []})
    assert r2.json()["options"] == []


def test_galaxy_store_is_in_the_catalog():
    from filterphone.api.catalog import CATALOG
    assert "com.sec.android.app.samsungapps" in CATALOG["galaxy_store"].apps


def test_filtertube_is_seeded_into_the_store_with_description(env):
    svc, client = env
    h = admin_headers(client)
    apps = {a["package"]: a for a in client.get("/v1/admin/apps", headers=h).json()["apps"]}
    assert "com.filtertube.app" in apps and "com.filtertube.filtertube" in apps
    store = apps["com.filtertube.filtertube"]
    assert "שלוש רמות" in store["description"] and store["category"] == "וידאו"
    assert store["play_url"].startswith("https://play.google.com/")
    # הזריעה חד-פעמית: מחיקה לא גוררת זריעה מחדש
    client.delete(f"/v1/admin/apps/{apps['com.filtertube.app']['id']}", headers=h)
    svc.seed_store()
    assert "com.filtertube.app" not in {a["package"] for a in client.get("/v1/admin/apps", headers=h).json()["apps"]}


def test_store_app_description_and_screenshots_round_trip(env):
    svc, client = env
    h = admin_headers(client)
    r = client.post("/v1/admin/apps", headers=h, json={
        "package": "com.example.notes", "label": "פתקים", "description": "אפליקציית פתקים כשרה",
        "screenshots": ["https://cdn.test/a.png", "http://bad/x.png", "https://cdn.test/b.png"],
        "icon_url": "https://cdn.test/icon.png",
        "play_url": "https://play.google.com/store/apps/details?id=com.example.notes"})
    assert r.status_code == 201, r.text
    a = next(x for x in client.get("/v1/admin/apps", headers=h).json()["apps"] if x["package"] == "com.example.notes")
    assert a["description"] == "אפליקציית פתקים כשרה"
    assert json.loads(a["screenshots"]) == ["https://cdn.test/a.png", "https://cdn.test/b.png"]   # http סונן
    # קישור Play לא תקין נדחה
    assert client.post("/v1/admin/apps", headers=h, json={"package": "com.x.bad",
                                                          "play_url": "https://evil.com/x"}).status_code == 400


def test_org_name_reaches_the_phone_and_is_editable(env):
    svc, client = env
    h = admin_headers(client)
    _, dh, _ = enroll(svc, client)
    r = client.post("/v1/device/heartbeat", headers=dh, json={"policy_version": 1}).json()
    assert r["org_name"] == "פילטר פון"                       # ברירת המחדל
    assert client.get("/v1/admin/branding", headers=h).json() == {"org_name": "פילטר פון"}
    assert client.put("/v1/admin/branding", headers=h, json={"org_name": "  פילטר   טיוב "}).json() == {
        "org_name": "פילטר טיוב"}
    r = client.post("/v1/device/heartbeat", headers=dh, json={"policy_version": 1}).json()
    assert r["org_name"] == "פילטר טיוב"
    assert client.put("/v1/admin/branding", headers=h, json={"org_name": ""}).status_code == 400
    assert client.put("/v1/admin/branding", json={"org_name": "x"}).status_code == 401
