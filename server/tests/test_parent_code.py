"""קוד הורים: בלי הקוד אי אפשר לשנות את החסימות של הלקוח — לא בדשבורד, לא בבוט ולא באישור בקשה."""
import pytest
from helpers import admin_headers
from test_bot import BOT, active_customer, auth, set_bot

from filterphone.api.app import device_limiter, register_limiter, support_limiter
from filterphone.api.service import PARENT_CODE_MAX_FAILS


@pytest.fixture(autouse=True)
def _limits():
    support_limiter._hits.clear()
    device_limiter._hits.clear()
    register_limiter._hits.clear()


def setup(env, code="2468"):
    svc, client = env
    h = admin_headers(client)
    did, token = active_customer(svc, client)
    cid = client.get(f"/v1/admin/devices/{did}", headers=h).json()["customer"]["id"]
    r = client.put(f"/v1/admin/customers/{cid}/parent-code", headers=h, json={"code": code})
    assert r.status_code == 200 and r.json()["parent_code"] is True
    return svc, client, h, did, cid, token


def test_without_a_code_nothing_changes_in_the_dashboard(env):
    svc, client, h, did, cid, _ = setup(env)
    d = client.get(f"/v1/admin/devices/{did}", headers=h).json()
    # ה-hash לא יוצא החוצה — רק "יש קוד"
    assert d["customer"]["parent_code"] is True and all(not k.startswith("parent_code_") for k in d["customer"])
    assert "$argon2" not in str(client.get("/v1/admin/customers", headers=h).json())
    before = d["settings"]["options"]
    for method, path, body in [
        ("PATCH", f"/v1/admin/devices/{did}", {"options": []}),
        ("PATCH", f"/v1/admin/devices/{did}", {"level": 3}),
        ("PATCH", f"/v1/admin/devices/{did}", {"track": "no_whatsapp"}),
        ("POST", f"/v1/admin/devices/{did}/pause", {"minutes": 30}),
        ("POST", f"/v1/admin/devices/{did}/temp-open", {"item": "play_store", "minutes": 10}),
        ("POST", f"/v1/admin/devices/{did}/unmanage", None),
        ("DELETE", f"/v1/admin/devices/{did}", None),
    ]:
        r = client.request(method, path, headers=h, json=body)
        assert r.status_code == 403 and r.json()["need"] == "parent_code", (path, r.text)
    assert client.get(f"/v1/admin/devices/{did}", headers=h).json()["settings"]["options"] == before
    # שם המכשיר — לא חסימה, לא צריך קוד
    assert client.patch(f"/v1/admin/devices/{did}", headers=h, json={"name": "חדש"}).status_code == 200
    # עם הקוד — עובד
    r = client.patch(f"/v1/admin/devices/{did}", headers={**h, "X-Parent-Code": "2468"}, json={"options": []})
    assert r.status_code == 200 and r.json()["settings"]["options"] == []


def test_wrong_codes_lock_and_an_audited_reset_unlocks(env):
    svc, client, h, did, cid, _ = setup(env)
    bad = {**h, "X-Parent-Code": "0000"}
    for _ in range(PARENT_CODE_MAX_FAILS):
        r = client.patch(f"/v1/admin/devices/{did}", headers=bad, json={"level": 3})
        assert r.status_code == 403 and r.json()["wrong"] is True
    assert r.json()["locked"] is True
    # נעול — גם הקוד הנכון לא עובד עכשיו
    r = client.patch(f"/v1/admin/devices/{did}", headers={**h, "X-Parent-Code": "2468"}, json={"level": 3})
    assert r.status_code == 429 and r.json()["need"] == "parent_code"
    # החלפת קוד דורשת את הישן; איפוס (ההורים שכחו) — מבטל, ונרשם ביומן
    assert client.put(f"/v1/admin/customers/{cid}/parent-code", headers=h, json={"code": "1111"}).status_code in (403, 429)
    assert client.delete(f"/v1/admin/customers/{cid}/parent-code", headers=h).json()["parent_code"] is False
    actions = [a["action"] for a in client.get("/v1/admin/audit", headers=h).json()]
    assert "parent_code_reset" in actions and "parent_code_wrong" in actions
    assert client.patch(f"/v1/admin/devices/{did}", headers=h, json={"level": 3}).status_code == 200


def test_changing_the_code_needs_the_old_one(env):
    svc, client, h, did, cid, _ = setup(env)
    r = client.put(f"/v1/admin/customers/{cid}/parent-code", headers=h, json={"code": "1357", "old_code": "2468"})
    assert r.status_code == 200
    assert client.patch(f"/v1/admin/devices/{did}", headers={**h, "X-Parent-Code": "2468"},
                        json={"level": 3}).status_code == 403
    assert client.patch(f"/v1/admin/devices/{did}", headers={**h, "X-Parent-Code": "1357"},
                        json={"level": 3}).status_code == 200
    assert client.put(f"/v1/admin/customers/{cid}/parent-code", headers=h, json={"code": "12ab"}).status_code == 400


def test_the_bot_cannot_bypass_the_code(env):
    svc, client, h, did, cid, _ = setup(env)
    set_bot(client)
    crm = client.get("/v1/bot/customer?phone=0501234567", headers=auth(BOT)).json()
    assert crm["customer"]["parent_code"] is True
    r = client.post("/v1/bot/open", headers=auth(BOT), json={"device_id": did, "item": "play_store"})
    assert r.status_code == 403 and r.json()["need"] == "parent_code"
    r = client.post("/v1/bot/block", headers=auth(BOT), json={"device_id": did, "options": ["tiktok"]})
    assert r.status_code == 403
    r = client.post("/v1/bot/open", headers=auth(BOT), json={"device_id": did, "item": "play_store", "parent_code": "9999"})
    assert r.status_code == 403 and r.json()["wrong"] is True
    r = client.post("/v1/bot/open", headers=auth(BOT),
                    json={"device_id": did, "item": "play_store", "minutes": 5, "parent_code": "2468"})
    assert r.status_code == 200 and r.json()["until"]
    # סגירה מוקדמת (חוסם שוב) — מותרת תמיד
    assert client.post("/v1/bot/close", headers=auth(BOT), json={"device_id": did, "item": "play_store"}).status_code == 200


def test_approving_a_change_request_needs_the_code(env):
    svc, client, h, did, cid, token = setup(env)
    r = client.post("/v1/device/requests", headers=auth(token), json={"level": 3, "options": [], "note": "לפתוח"})
    assert r.status_code == 201
    req = [q for q in client.get("/v1/admin/requests", headers=h).json() if q["kind"] == "change"][0]
    assert client.post(f"/v1/admin/requests/{req['id']}/approve", headers=h, json={}).status_code == 403
    assert client.post(f"/v1/admin/requests/{req['id']}/approve", headers={**h, "X-Parent-Code": "2468"},
                       json={}).status_code == 200
