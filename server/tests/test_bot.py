"""API של בוט הוואטסאפ הלא-רשמי: CRM, העברת פניות, תשובות, ופעולות מדורגות (בטוח מול קריטי)."""
import pytest
from helpers import admin_headers, wg_key

from filterphone.api.app import device_limiter, support_limiter

BOT = "bot-secret-token-123456"


@pytest.fixture(autouse=True)
def _limits():
    support_limiter._hits.clear()
    device_limiter._hits.clear()


def auth(t):
    return {"Authorization": f"Bearer {t}"}


def active_customer(svc, client, phone="0501234567", uid="11" * 16):
    reg = {"name": "משה", "phone": phone, "email": "", "level": 1, "options": ["play_store"], "note": "",
           "device_name": "גלקסי", "platform": "android", "app_version": "2.0.0"}
    r = client.post("/v1/register", json={**reg, "public_key": wg_key(1), "device_uid": uid})
    did = r.json()["device_id"]
    h = admin_headers(client)
    req = client.get("/v1/admin/requests", headers=h).json()[0]
    client.post(f"/v1/admin/requests/{req['id']}/approve", headers=h, json={"options": ["play_store"]})
    return did, r.json()["token"]


def set_bot(client, token=BOT):
    h = admin_headers(client)
    assert client.put("/v1/admin/whatsapp", headers=h, json={"bot_token": token}).status_code == 200


def test_bot_requires_a_valid_token(env):
    svc, client = env
    assert client.get("/v1/bot/outbox").status_code == 401            # בלי טוקן
    assert client.get("/v1/bot/outbox", headers=auth("x")).status_code == 401   # בלי הגדרה
    set_bot(client)
    assert client.get("/v1/bot/outbox", headers=auth("wrong")).status_code == 401
    assert client.get("/v1/bot/outbox", headers=auth(BOT)).status_code == 200


def test_outbox_reply_and_crm(env):
    svc, client = env
    set_bot(client)
    did, token = active_customer(svc, client)
    # הלקוח כותב באפליקציה → מופיע ב-outbox של הבוט (ולא נשלח ב-Cloud API כי יש בוט)
    client.post("/v1/device/support", headers=auth(token), json={"text": "האינטרנט לא עובד"})
    out = client.get("/v1/bot/outbox", headers=auth(BOT)).json()["messages"]
    assert len(out) == 1 and out[0]["text"] == "האינטרנט לא עובד" and out[0]["phone"] == "0501234567"
    mid = out[0]["id"]
    # סימון כ"הועבר" — לא חוזר שוב
    assert client.post("/v1/bot/forwarded", headers=auth(BOT), json={"ids": [mid]}).json()["marked"] == 1
    assert client.get("/v1/bot/outbox", headers=auth(BOT)).json()["messages"] == []
    # המנהל ענה מהוואטסאפ → מגיע ללקוח באפליקציה
    assert client.post("/v1/bot/reply", headers=auth(BOT),
                       json={"device_id": did, "text": "נסה להתחבר מחדש 🙂"}).json()["ok"] is True
    chat = client.get("/v1/device/support", headers=auth(token)).json()["messages"]
    assert chat[-1]["text"] == "נסה להתחבר מחדש 🙂" and chat[-1]["via"] == "whatsapp"
    # CRM: זיהוי לפי טלפון
    crm = client.get("/v1/bot/customer", headers=auth(BOT), params={"phone": "+972 50-123-4567"}).json()
    assert crm["customer"]["name"] == "משה"
    assert crm["devices"][0]["device_id"] == did and "play_store" in crm["devices"][0]["blocks"]


def test_safe_block_is_applied_but_critical_needs_admin_approval(env):
    svc, client = env
    set_bot(client)
    did, _ = active_customer(svc, client)
    # פעולה בטוחה (חסימה) — מוחלת מיד
    r = client.post("/v1/bot/block", headers=auth(BOT), json={"device_id": did, "options": ["tiktok", "instagram"]})
    assert r.status_code == 200 and set(r.json()["added"]) == {"tiktok", "instagram"}
    dev = client.get(f"/v1/admin/devices/{did}", headers=admin_headers(client)).json()
    assert {"tiktok", "instagram", "play_store"} <= {b["id"] for b in dev["blocks"]}
    # אפשרות לא מהקטלוג נדחית
    assert client.post("/v1/bot/block", headers=auth(BOT), json={"device_id": did, "options": ["junk"]}).status_code == 400
    # פעולה קריטית (השהיה / פתיחה) — רק נרשמת כבקשה לאישור המנהל, לא מבוצעת
    e = client.post("/v1/bot/escalate", headers=auth(BOT),
                    json={"device_id": did, "note": "הלקוח ביקש להשהות ליום", "options": []})
    assert e.status_code == 201 and e.json()["status"] == "pending"
    reqs = client.get("/v1/admin/requests", headers=admin_headers(client)).json()
    assert any("בוט וואטסאפ" in (rq.get("note") or "") for rq in reqs)
    # המכשיר עדיין פעיל — הבוט לא השהה לבד
    assert client.get(f"/v1/admin/devices/{did}", headers=admin_headers(client)).json()["status"] == "active"
