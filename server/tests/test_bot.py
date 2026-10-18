"""API של בוט הוואטסאפ הלא-רשמי: CRM, העברת פניות, תשובות, ופעולות מדורגות (בטוח מול קריטי)."""
import pytest
from helpers import admin_headers, wg_key

from filterphone.api.app import device_limiter, register_limiter, support_limiter

BOT = "bot-secret-token-123456"


@pytest.fixture(autouse=True)
def _limits():
    support_limiter._hits.clear()
    device_limiter._hits.clear()
    register_limiter._hits.clear()


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
    assert out[0]["topic"] == "" and out[0]["diag"] == {}
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


def test_suggestion_reaches_the_bot_with_its_topic(env):
    """הצעת ייעול מההגדרות באפליקציה — הבוט מזהה לפי topic ומעביר ישר למנהל (בלי מענה AI)."""
    svc, client = env
    set_bot(client)
    _, token = active_customer(svc, client)
    client.post("/v1/device/support", headers=auth(token),
                json={"text": "💡 הצעת ייעול: מצב לילה", "diag": {"topic": "suggestion", "vpn": "UP"}})
    out = client.get("/v1/bot/outbox", headers=auth(BOT)).json()["messages"]
    assert [(m["topic"], m["text"]) for m in out] == [("suggestion", "💡 הצעת ייעול: מצב לילה")]


def _policy(client, token):
    return client.get("/v1/device", headers=auth(token)).json()["device_policy"]


def test_bot_opens_play_for_minutes_and_it_closes_by_itself(env):
    svc, client = env
    set_bot(client)
    did, token = active_customer(svc, client)
    before = _policy(client, token)["version"]
    actions = client.get("/v1/bot/customer?phone=0501234567", headers=auth(BOT)).json()["actions"]
    assert {"id": "play_store", "title": "כניסה לחנות Google Play"} in actions["open"]
    r = client.post("/v1/bot/open", headers=auth(BOT), json={"device_id": did, "item": "play_store"})
    assert r.status_code == 200, r.text
    until = r.json()["until"]
    assert r.json()["minutes"] == 10
    pol = _policy(client, token)
    assert pol["version"] > before
    assert "com.android.vending" in pol["blocked_apps"]           # הטלפון חוסם שוב לבד בשעה שנקבעה
    assert pol["temp_allow"] == [{"package": "com.android.vending", "until": until}]
    # כבר פתוח — לא מוצע שוב; פריט שהבוט לא פותח לבד → 403 (רק באישור מנהל)
    assert client.get("/v1/bot/customer?phone=0501234567", headers=auth(BOT)).json()["actions"]["opened"] == ["play_store"]
    r = client.post("/v1/bot/open", headers=auth(BOT), json={"device_id": did, "item": "browsers_other"})
    assert r.status_code == 403
    assert client.post("/v1/bot/open", headers=auth(BOT),
                       json={"device_id": did, "item": "play_store", "minutes": 61}).status_code == 400
    # התוקף פג → נחסם שוב (בלי temp_allow), ונרשם ביומן המכשיר
    assert svc.expire_temp_opens(until - 1) == 0
    assert svc.expire_temp_opens(until + 1) == 1
    pol = _policy(client, token)
    assert "temp_allow" not in pol
    log = client.get(f"/v1/admin/devices/{did}", headers=admin_headers(client)).json()["device_log"]
    assert [e["kind"] for e in log][:2] == ["temp_closed", "temp_open"]


def test_temp_open_unblocks_the_network_until_it_expires(env):
    svc, client = env
    did, token = active_customer(svc, client)
    h = admin_headers(client)
    client.patch(f"/v1/admin/devices/{did}", headers=h, json={"options": ["play_store", "youtube"]})
    row = svc.device_row(did)
    yt = set(svc.compiled(row).hosts)
    assert "youtube.com" in yt
    r = client.post(f"/v1/admin/devices/{did}/temp-open", headers=h, json={"item": "youtube", "minutes": 5})
    assert r.status_code == 200, r.text
    assert "youtube.com" not in svc.compiled(svc.device_row(did)).hosts
    assert client.get(f"/v1/admin/devices/{did}", headers=h).json()["temp_opens"][0]["item"] == "youtube"
    # המנהל סוגר לפני הזמן
    assert client.delete(f"/v1/admin/devices/{did}/temp-open/youtube", headers=h).json()["closed"] is True
    assert "youtube.com" in svc.compiled(svc.device_row(did)).hosts


def test_bot_blocks_hotspot_permanently_and_identifies_by_device_and_android_id(env):
    svc, client = env
    set_bot(client)
    from filterphone.api.support import device_uid_for
    aid = "7d1ac306b6d02fe3"
    did, token = active_customer(svc, client, uid=device_uid_for(aid))
    client.patch(f"/v1/admin/devices/{did}", headers=admin_headers(client), json={"dpc": {"disallow_tethering": False}})
    assert _policy(client, token)["dpc"]["disallow_tethering"] is False
    r = client.post("/v1/bot/block", headers=auth(BOT), json={"device_id": did, "options": ["hotspot"]})
    assert r.status_code == 200, r.text
    assert r.json()["titles"] == ["נקודה חמה (שיתוף אינטרנט)"]
    assert _policy(client, token)["dpc"]["disallow_tethering"] is True
    # מספר וואטסאפ אחר (לא רשום): מזוהה רק עם מספר המכשיר + מזהה האנדרואיד הנכון
    q = f"/v1/bot/customer?phone=0539999999&device={did}"
    assert client.get(q, headers=auth(BOT)).status_code == 404
    assert client.get(q + "&aid=0000000000000000", headers=auth(BOT)).status_code == 404
    j = client.get(q + f"&aid={aid}", headers=auth(BOT)).json()
    assert j["selected"] == did and j["customer"]["name"] == "משה"
    assert all(b["id"] != "dpc:disallow_tethering" for b in j["actions"]["block"])   # כבר חסום
