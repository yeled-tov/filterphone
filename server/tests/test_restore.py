"""שחזור לפי מספר טלפון + מזהה מכשיר, והחלפת מכשיר / מכשיר נוסף באישור המנהל."""


import pytest
from helpers import admin_headers, wg_key

from filterphone.api.app import device_limiter, register_limiter, restore_limiter

UID_A = "a1" * 16
UID_B = "b2" * 16
REG = {"name": "משה כהן", "phone": "050-123-4567", "email": "", "level": 1, "options": ["play_store"],
       "note": "", "device_name": "גלקסי", "platform": "android", "app_version": "2.0.0"}


@pytest.fixture(autouse=True)
def _limits():
    register_limiter._hits.clear()
    device_limiter._hits.clear()
    restore_limiter._hits.clear()


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def active_customer(svc, client, uid=UID_A):
    """לקוח עם טלפון אחד פעיל (נרשם ואושר), מחזיר (device_id, token)."""
    r = client.post("/v1/register", json={**REG, "public_key": wg_key(1), "device_uid": uid})
    assert r.status_code == 201, r.text
    reg = r.json()
    h = admin_headers(client)
    req = client.get("/v1/admin/requests", headers=h).json()[0]
    assert client.post(f"/v1/admin/requests/{req['id']}/approve", headers=h,
                       json={"options": ["play_store", "tiktok"]}).status_code == 200
    return reg["device_id"], reg["token"]


def restore(client, uid, n, phone="0501234567"):
    return client.post("/v1/restore", json={"phone": phone, "device_uid": uid, "public_key": wg_key(n),
                                            "name": "אותו טלפון", "platform": "android", "app_version": "2.1.0"})


def test_same_phone_reinstall_restores_protection_by_itself(env):
    svc, client = env
    did, old_token = active_customer(svc, client)
    old = client.get("/v1/admin/devices", headers=admin_headers(client)).json()[0]
    r = restore(client, UID_A, 2, phone="+972 50-123-4567")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["status"] == "active" and j["device_id"] == did and j["level"] == 1
    assert j["vpn"]["address"] == f"{old['vpn_ip']}/32"           # אותה כתובת — אותה מדיניות בפרוקסי
    # האסימון הישן לא עובד יותר; החדש כן — עם אותה מדיניות
    assert client.get("/v1/device", headers=auth(old_token)).status_code == 401
    me = client.get("/v1/device", headers=auth(j["token"])).json()
    assert me["status"] == "active" and "tiktok" in {b["id"] for b in me["blocks"]}
    assert wg_key(2) in svc.wg.peers_ and wg_key(1) not in svc.wg.peers_


def test_restore_after_uninstall_during_pause_but_not_after_admin_disable(env):
    svc, client = env
    did, token = active_customer(svc, client)
    h = admin_headers(client)
    client.post(f"/v1/admin/devices/{did}/pause", headers=h, json={"minutes": 30})
    assert client.post("/v1/device/uninstalled", headers=auth(token)).status_code == 200
    assert restore(client, UID_A, 2).json()["status"] == "active"           # הסיר בעצמו → חוזר
    client.patch(f"/v1/admin/devices/{did}", headers=h, json={"status": "disabled"})
    r = restore(client, UID_A, 3)
    assert r.status_code == 403 and "המנהל" in r.json()["error"]            # המנהל השבית → לא חוזר לבד


def test_other_phone_gets_a_choice_and_swap_needs_admin_approval(env):
    svc, client = env
    did, old_token = active_customer(svc, client)
    r = restore(client, UID_B, 5)
    assert r.status_code == 200
    j = r.json()
    assert j["status"] == "needs_choice" and [d["id"] for d in j["devices"]] == [did]
    # הלקוח בוחר "להעביר את ההגנה לטלפון הזה"
    rq = client.post("/v1/restore/request", json={"phone": "0501234567", "device_uid": UID_B, "public_key": wg_key(5),
                                                  "name": "טלפון חדש", "mode": "swap", "old_device_id": did})
    assert rq.status_code == 201, rq.text
    new = rq.json()
    assert new["status"] == "pending"
    assert client.get("/v1/device", headers=auth(new["token"])).json()["status"] == "pending"
    # בקשה כפולה מאותו טלפון — לא נוצרת שנייה
    dup = client.post("/v1/restore/request", json={"phone": "0501234567", "device_uid": UID_B,
                                                   "public_key": wg_key(6), "mode": "swap", "old_device_id": did})
    assert dup.status_code == 409
    h = admin_headers(client)
    swaps = client.get("/v1/admin/swaps", headers=h).json()["swaps"]
    assert len(swaps) == 1 and swaps[0]["mode"] == "swap" and swaps[0]["old_device_id"] == did
    ok = client.post(f"/v1/admin/swaps/{swaps[0]['id']}", headers=h, json={"approve": True})
    assert ok.status_code == 200 and ok.json()["mode"] == "swap"
    # החדש פעיל עם המדיניות של הישן; הישן מנותק
    me = client.get("/v1/device", headers=auth(new["token"])).json()
    assert me["status"] == "active" and me["level"] == 1 and "tiktok" in {b["id"] for b in me["blocks"]}
    assert client.get("/v1/device", headers=auth(old_token)).json()["status"] == "disabled"
    assert wg_key(5) in svc.wg.peers_ and wg_key(1) not in svc.wg.peers_
    assert client.post(f"/v1/admin/swaps/{swaps[0]['id']}", headers=h, json={"approve": True}).status_code == 409


def test_additional_device_keeps_the_old_one(env):
    svc, client = env
    did, old_token = active_customer(svc, client)
    rq = client.post("/v1/restore/request", json={"phone": "0501234567", "device_uid": UID_B, "public_key": wg_key(7),
                                                  "name": "טאבלט", "mode": "additional"}).json()
    h = admin_headers(client)
    sw = client.get("/v1/admin/swaps", headers=h).json()["swaps"][0]
    assert sw["mode"] == "additional"
    client.post(f"/v1/admin/swaps/{sw['id']}", headers=h, json={"approve": True})
    assert client.get("/v1/device", headers=auth(rq["token"])).json()["status"] == "active"
    assert client.get("/v1/device", headers=auth(old_token)).json()["status"] == "active"


def test_reject_and_unknown_phone(env):
    svc, client = env
    did, _ = active_customer(svc, client)
    rq = client.post("/v1/restore/request", json={"phone": "0501234567", "device_uid": UID_B, "public_key": wg_key(8),
                                                  "mode": "swap", "old_device_id": did}).json()
    h = admin_headers(client)
    sw = client.get("/v1/admin/swaps", headers=h).json()["swaps"][0]
    client.post(f"/v1/admin/swaps/{sw['id']}", headers=h, json={"approve": False})
    assert client.get("/v1/device", headers=auth(rq["token"])).json()["status"] == "rejected"
    r = restore(client, UID_B, 9, phone="0529999999")
    assert r.status_code == 404 and "לא נמצא" in r.json()["error"]
    assert restore(client, "short", 9).status_code == 400                     # מזהה לא תקין


def test_heartbeat_records_the_device_id_of_existing_phones(env):
    svc, client = env
    code = svc.create_code("ותיק", 2, 1, 1)["code"]
    j = client.post("/v1/enroll", json={"code": code, "public_key": wg_key(20), "name": "ישן"}).json()
    client.post("/v1/device/heartbeat", headers=auth(j["token"]), json={"policy_version": 1, "device_uid": UID_A})
    with svc.db() as c:
        assert c.execute("SELECT device_uid FROM devices WHERE id = ?", (j["device_id"],)).fetchone()[0] == UID_A

