"""הרשמה מהאפליקציה, בקשות, אישור המנהל, מדיניות לכל מכשיר, AdGuard ובדיקת תמונה."""
import json

import pytest
from helpers import admin_headers, wg_key

from filterphone import protocol
from filterphone.api import adguard, fetch
from filterphone.api.app import device_limiter, register_limiter
from filterphone.api.catalog import CATALOG, OPTIONS, DeviceSettings, compile_settings, public_catalog

REG = {"name": "משה כהן", "phone": "050-123-4567", "email": "Moshe@Example.com", "level": 2,
       "options": ["whatsapp_profile", "play_store", "nonexistent"], "note": "לחסום גם את אתר החדשות X",
       "device_name": "גלקסי A54", "platform": "android", "app_version": "1.2.0"}


def register(client, n=1, **over):
    body = {**REG, "public_key": wg_key(n), **over}
    r = client.post("/v1/register", json=body)
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture(autouse=True)
def _limits():
    register_limiter._hits.clear()
    device_limiter._hits.clear()


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_catalog_is_public_and_consistent(env):
    _, client = env
    cat = client.get("/v1/catalog").json()
    assert [lv["level"] for lv in cat["levels"]] == [1, 2, 3]
    ids = {o["id"] for o in cat["options"]}
    assert {"whatsapp_profile", "whatsapp_status", "play_store", "instagram", "browsers_other"} <= ids
    assert any(o["popular"] for o in cat["options"])
    assert "hosts" not in cat["options"][0]                 # פרטי האכיפה — רק למנהל
    cats = {c["id"] for c in cat["categories"]}
    assert all(o["category"] in cats for o in cat["options"])
    assert public_catalog() == cat


def test_every_option_enforces_something():
    for o in OPTIONS:
        assert o.hosts or o.apps or o.screens or o.paths or o.images or o.video, o.id
        assert o.title and o.category


def test_registration_waits_for_approval_then_activates(env):
    svc, client = env
    reg = register(client)
    assert reg["status"] == "pending"
    me = client.get("/v1/device", headers=auth(reg["token"])).json()
    assert me["status"] == "pending" and "מחכה לאישור" in me["message"]
    assert me["request"]["kind"] == "join"
    assert client.get("/v1/device/vpn", headers=auth(reg["token"])).status_code == 403
    assert wg_key(1) not in svc.wg.peers_                   # עוד לא מחובר ל-VPN

    h = admin_headers(client)
    reqs = client.get("/v1/admin/requests", headers=h).json()
    assert len(reqs) == 1
    r = reqs[0]
    assert r["customer_name"] == "משה כהן" and r["phone"] == "0501234567" and r["email"] == "moshe@example.com"
    assert [o["id"] for o in r["options"]] == ["play_store", "whatsapp_profile"]      # לא מוכר — סונן
    assert r["note"] == "לחסום גם את אתר החדשות X" and r["level"] == 2

    # המנהל קובע את הרמה הסופית ומוסיף חסימה משלו
    ok = client.post(f"/v1/admin/requests/{r['id']}/approve", headers=h, json={
        "level": 1, "options": ["whatsapp_profile", "play_store", "instagram"],
        "custom_hosts": ["https://www.news-x.co.il/path"], "payment": "paid", "paid_until": 1900000000})
    assert ok.status_code == 200, ok.text
    assert ok.json()["vpn_ip"] == "10.8.0.10"
    assert wg_key(1) in svc.wg.peers_
    assert svc.redis.hget("fp:levels", "10.8.0.10") == "1"
    pol = json.loads(svc.redis.hget("fp:policy", "10.8.0.10"))
    assert pol["level"] == 1 and pol["app_images"] == "block"
    assert {"pps.whatsapp.net", "play.google.com", "instagram.com", "news-x.co.il"} <= set(pol["hosts"])

    me = client.get("/v1/device", headers=auth(reg["token"])).json()
    assert me["status"] == "active" and me["level_name"] == "מחמיר"
    assert {b["id"] for b in me["blocks"]} >= {"whatsapp_profile", "play_store", "instagram", "custom_hosts"}
    assert "com.android.vending" in me["device_policy"]["blocked_apps"]
    vpn = client.get("/v1/device/vpn", headers=auth(reg["token"])).json()
    assert vpn["address"] == "10.8.0.10/32" and "BEGIN CERTIFICATE" in vpn["ca"]["pem"]

    cust = client.get("/v1/admin/customers", headers=h).json()[0]
    assert cust["payment"] == "paid" and cust["paid_until"] == 1900000000 and cust["active"] == 1
    assert client.post(f"/v1/admin/requests/{r['id']}/approve", headers=h, json={}).status_code == 409
    log = client.get("/v1/admin/audit", headers=h).json()
    assert {e["action"] for e in log} >= {"register", "approve"}


def test_reject_and_cancel(env):
    _, client = env
    h = admin_headers(client)
    a = register(client, 2)
    rid = client.get("/v1/admin/requests", headers=h).json()[0]["id"]
    assert client.post(f"/v1/admin/requests/{rid}/reject", headers=h,
                       json={"admin_note": "לא שולם"}).status_code == 204
    me = client.get("/v1/device", headers=auth(a["token"])).json()
    assert me["status"] == "rejected" and "לא שולם" in me["message"]

    b = register(client, 3, phone="052-7654321")
    assert client.delete("/v1/device", headers=auth(b["token"])).status_code == 204
    assert client.get("/v1/device", headers=auth(b["token"])).status_code == 401
    assert client.get("/v1/admin/requests", headers=h).json() == []


def test_registration_validation_and_rate_limit(env):
    _, client = env
    bad = client.post("/v1/register", json={**REG, "public_key": wg_key(4), "phone": "12"})
    assert bad.status_code == 400 and "טלפון" in bad.json()["error"]
    bad = client.post("/v1/register", json={**REG, "public_key": wg_key(4), "email": "not-an-email"})
    assert bad.status_code == 400
    bad = client.post("/v1/register", json={**REG, "public_key": wg_key(4), "level": 0})
    assert bad.status_code == 400 and "רמת סינון" in bad.json()["error"]
    register(client, 5, email="")                          # מייל — לא חובה
    assert client.post("/v1/register", json={**REG, "public_key": wg_key(5)}).status_code == 409
    codes = [client.post("/v1/register", json={**REG, "public_key": wg_key(20 + i)}).status_code for i in range(6)]
    assert 429 in codes


def test_same_phone_links_to_same_customer(env):
    _, client = env
    h = admin_headers(client)
    register(client, 6)
    register(client, 7, phone="+972-50-1234567", device_name="הטלפון של הבן")
    custs = client.get("/v1/admin/customers", headers=h).json()
    assert len(custs) == 1 and custs[0]["devices"] == 2
    reqs = client.get("/v1/admin/requests", headers=h).json()
    assert all(r["customer_devices"] == 2 for r in reqs)


def test_change_request_from_active_device(env):
    svc, client = env
    h = admin_headers(client)
    reg = register(client, 8)
    rid = client.get("/v1/admin/requests", headers=h).json()[0]["id"]
    client.post(f"/v1/admin/requests/{rid}/approve", headers=h, json={})       # מה שהלקוח ביקש
    ch = client.post("/v1/device/requests", headers=auth(reg["token"]),
                     json={"level": 3, "options": ["youtube"], "note": "בבקשה לפתוח יוטיוב? לא — לחסום"})
    assert ch.status_code == 201
    # בקשה שנייה מעדכנת את הפתוחה במקום לפתוח עוד אחת
    client.post("/v1/device/requests", headers=auth(reg["token"]), json={"options": ["youtube", "tiktok"]})
    pend = client.get("/v1/admin/requests", headers=h).json()
    assert len(pend) == 1 and pend[0]["kind"] == "change"
    assert set(pend[0]["current"]["options"]) == {"play_store", "whatsapp_profile"}
    client.post(f"/v1/admin/requests/{pend[0]['id']}/approve", headers=h,
                json={"options": ["play_store", "whatsapp_profile", "youtube", "tiktok"]})
    dev = client.get("/v1/admin/devices", headers=h).json()[0]
    assert dev["level"] == 2                                # הלקוח לא משנה רמה לבד
    pol = json.loads(svc.redis.hget("fp:policy", dev["vpn_ip"]))
    assert "googlevideo.com" in pol["hosts"] and "tiktok.com" in pol["hosts"]


def test_admin_edits_policy_and_device_reports_apps(env):
    svc, client = env
    h = admin_headers(client)
    code = svc.create_code("משפחת לוי", level=2, uses=1, expires_days=None)["code"]
    tok = client.post("/v1/enroll", json={"code": code, "public_key": wg_key(9)}).json()["token"]
    dev = client.get("/v1/admin/devices", headers=h).json()[0]
    r = client.patch(f"/v1/admin/devices/{dev['id']}", headers=h, json={
        "options": ["browsers_other", "video_off", "images_off"], "custom_apps": ["com.some.game", "bad pkg"]})
    assert r.status_code == 400 and "שם חבילה" in r.json()["error"]
    r = client.patch(f"/v1/admin/devices/{dev['id']}", headers=h, json={
        "options": ["browsers_other", "video_off", "images_off", "google_images"],
        "custom_apps": ["com.some.game"], "allow_hosts": ["instagram.com"], "app_images": "allow"})
    assert r.status_code == 200, r.text
    detail = r.json()
    assert detail["compiled"]["images"] == "block" and detail["compiled"]["video"] is True
    assert detail["compiled"]["needs_guard"] is True
    pol = json.loads(svc.redis.hget("fp:policy", dev["vpn_ip"]))
    assert pol["images"] == "block" and pol["video"] is True and pol["app_images"] == "allow"
    assert pol["paths"] and pol["paths"][0]["host"] == "google.*"

    rep = client.post("/v1/device/report", headers=auth(tok), json={
        "apps": [{"package": "org.mozilla.firefox", "label": "Firefox"},
                 {"package": "com.some.game", "label": "משחק"},
                 {"package": "com.waze", "label": "Waze"}, {"package": "evil; rm", "label": "x"}],
        "guard": {"accessibility": True, "device_owner": False, "evil": "x"}})
    assert rep.status_code == 200
    detail = client.get(f"/v1/admin/devices/{dev['id']}", headers=h).json()
    apps = {a["package"]: a["blocked"] for a in detail["apps"]}
    assert apps == {"org.mozilla.firefox": True, "com.some.game": True, "com.waze": False}
    assert detail["guard"] == {"accessibility": True, "device_owner": False}
    me = client.get("/v1/device", headers=auth(tok)).json()
    assert "org.mozilla.firefox" in me["device_policy"]["blocked_apps"] and not me["apps_report_needed"]


def test_compile_respects_allow_list_and_levels():
    s = DeviceSettings(options=["facebook", "whatsapp_status"], allow_hosts=["fbcdn.net"])
    c = compile_settings(2, s)
    assert "facebook.com" in c.hosts and "fbcdn.net" not in c.hosts
    assert c.app_images == "block" and compile_settings(3, s).app_images == "allow"
    assert {sc["package"] for sc in c.screens} == {"com.whatsapp", "com.whatsapp.w4b"}
    assert set(CATALOG["facebook"].apps) <= set(c.apps)


def test_adguard_rules_and_merge():
    rules = adguard.build_rules([("10.8.0.10", ["instagram.com", "*.tiktok.com"]),
                                 ("10.8.0.11", ["instagram.com"])])
    assert rules == ["||instagram.com^$client=10.8.0.10|10.8.0.11", "||tiktok.com^$client=10.8.0.10"]
    current = ["||ads.example^", adguard.MARK_BEGIN, "||old^$client=10.8.0.9", adguard.MARK_END, "@@||ok.com^"]
    merged = adguard.merge(current, rules)
    assert merged[:2] == ["||ads.example^", "@@||ok.com^"]
    assert merged[2] == adguard.MARK_BEGIN and merged[-1] == adguard.MARK_END and "||old" not in str(merged)
    assert adguard.merge(merged, []) == ["||ads.example^", "@@||ok.com^"]


def test_adguard_sync_writes_only_on_change(env, monkeypatch):
    svc, client = env
    calls = []
    state = {"user_rules": ["||manual.example^"]}

    def fake_request(method, path, body=None):
        calls.append((method, path))
        if method == "POST":
            state["user_rules"] = body["rules"]
            return None
        return dict(state)

    svc.adguard.user, svc.adguard.password = "u", "p"
    monkeypatch.setattr(svc.adguard, "_request", fake_request)
    h = admin_headers(client)
    reg = register(client, 10)
    rid = client.get("/v1/admin/requests", headers=h).json()[0]["id"]
    client.post(f"/v1/admin/requests/{rid}/approve", headers=h, json={"options": ["tiktok"]})
    svc.sync_adguard()          # ייתכן שהלולאה ברקע כבר סנכרנה (האישור מסמן "dirty") — בודקים את התוצאה
    assert state["user_rules"][0] == "||manual.example^"
    assert "||tiktok.com^$client=10.8.0.10" in state["user_rules"]
    n = len(calls)
    assert svc.sync_adguard() is False and len(calls) == n          # בלי שינוי — בלי פנייה בכלל
    assert client.get("/v1/admin/overview", headers=h).json()["adguard"]["rules"] > 0
    assert reg["status"] == "pending"


def test_events_filters(env):
    svc, client = env
    h = admin_headers(client)
    code = svc.create_code("", level=1, uses=1, expires_days=None)["code"]
    client.post("/v1/enroll", json={"code": code, "public_key": wg_key(11)})
    dev = client.get("/v1/admin/devices", headers=h).json()[0]
    for host, action, kind in (("a.com", "block", "image"), ("www.google.com", "block", "inline"),
                               ("instagram.com", "site", "host")):
        svc.redis.xadd("fp:events", {"ip": dev["vpn_ip"], "host": host, "action": action, "kind": kind,
                                     "reason": "r", "path": "/p", "ts": 1})
    svc.redis.xadd("fp:events", {"ip": "10.8.0.99", "host": "b.com", "action": "block", "reason": "r", "ts": 1})
    assert len(client.get("/v1/admin/events", headers=h).json()) == 4
    mine = client.get(f"/v1/admin/events?device={dev['id']}", headers=h).json()
    assert len(mine) == 3 and mine[0]["device"] == dev["name"]
    assert [e["host"] for e in client.get("/v1/admin/events?kind=inline", headers=h).json()] == ["www.google.com"]
    assert [e["host"] for e in client.get("/v1/admin/events?q=insta", headers=h).json()] == ["instagram.com"]


def test_test_image_tool(env, monkeypatch):
    svc, client = env
    h = admin_headers(client)

    def fake_call(path, header, body, timeout=30):
        assert header == {"op": "explain", "level": 2} and body == b"IMG"
        return {"ok": True, "action": "replace", "ct": "image/jpeg",
                "info": {"decisions": {"1": {"action": "block"}}}}, b"PAINTED"

    monkeypatch.setattr(protocol, "call_sync", fake_call)
    r = client.post("/v1/admin/test-image", headers=h, json={"data": "data:image/jpeg;base64,SU1H", "level": 2})
    assert r.status_code == 200, r.text
    assert r.json()["result_image"] == "data:image/jpeg;base64,UEFJTlRFRA=="
    assert client.post("/v1/admin/test-image", headers=h, json={"level": 2}).status_code == 400
    r = client.post("/v1/admin/test-image", headers=h, json={"url": "http://127.0.0.1:8090/admin", "level": 2})
    assert r.status_code == 400 and "פנימית" in r.json()["error"]


def test_ssrf_guard_rejects_private_targets():
    for url in ("http://127.0.0.1/", "http://10.8.0.1/", "http://169.254.169.254/latest", "ftp://x.com/a",
                "http://[::1]/", "http://localhost/"):
        with pytest.raises(fetch.FetchError):
            fetch._check_url(url)
