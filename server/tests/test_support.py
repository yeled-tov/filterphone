"""תמיכה: צ'אט באפליקציה ↔ ממשק הניהול ↔ הוואטסאפ של המנהל (WhatsApp Cloud API)."""
import hashlib
import hmac
import json

import pytest
from helpers import admin_headers, wg_key

from filterphone.api import support as sp
from filterphone.api.app import support_limiter

ADMIN_WA = "972521112233"
SECRET = "app-secret-123"
PHONE_ID = "1234567890"


@pytest.fixture(autouse=True)
def _limits():
    support_limiter._hits.clear()


class FakeWa:
    def __init__(self):
        self.sent: list[tuple] = []
        self.fail_text: sp.WaError | None = None
        self.n = 0

    def __call__(self, cfg):          # client_factory(cfg) → אותו אובייקט
        self.cfg = cfg
        return self

    def _id(self):
        self.n += 1
        return f"wamid.{self.n}"

    def text(self, to, body, reply_to=""):
        if self.fail_text:
            raise self.fail_text
        self.sent.append(("text", to, body))
        return self._id()

    def template(self, to, name, lang, params):
        self.sent.append(("template", to, name, lang, params))
        return self._id()

    def react(self, to, message_id, emoji="✅"):
        self.sent.append(("react", to, message_id))


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def device(svc, client, n=1):
    code = svc.create_code("לקוח", 2, 1, 1)["code"]
    j = client.post("/v1/enroll", json={"code": code, "public_key": wg_key(n), "name": "גלקסי"}).json()
    return j["device_id"], j["token"]


def configure(client, **extra):
    body = {"token": "EAAG-test-token-9999", "phone_number_id": PHONE_ID, "admin_number": "052-111-2233",
            "verify_token": "my-verify", "app_secret": SECRET} | extra
    r = client.put("/v1/admin/whatsapp", headers=admin_headers(client), json=body)
    assert r.status_code == 200, r.text
    return r.json()


def signed(payload: dict, secret: str = SECRET) -> tuple[bytes, dict]:
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return body, {"X-Hub-Signature-256": sig, "Content-Type": "application/json"}


def incoming(text=None, reply_to="", sender=ADMIN_WA, wamid="wamid.in1", kind="text"):
    m = {"from": sender, "id": wamid, "timestamp": "1", "type": kind}
    if kind == "text":
        m["text"] = {"body": text}
    if reply_to:
        m["context"] = {"from": "972500000000", "id": reply_to}
    return {"object": "whatsapp_business_account", "entry": [{"id": "1", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp", "metadata": {"phone_number_id": PHONE_ID}, "messages": [m]}}]}]}


# ---------------------------------------------------------------- עזרים טהורים

def test_pure_helpers():
    assert sp.wa_number("050-123-4567") == "972501234567"
    assert sp.wa_number("+1 202 555 0100") == "12025550100"
    assert sp.wa_number("abc") == ""
    assert sp.parse_target("#12 שלום רב") == (12, "שלום רב")
    assert sp.parse_target("#7: בסדר") == (7, "בסדר")
    assert sp.parse_target("סתם תשובה") == (None, "סתם תשובה")
    body = b'{"a":1}'
    good = "sha256=" + hmac.new(b"s", body, hashlib.sha256).hexdigest()
    assert sp.verify_signature("s", body, good)
    assert not sp.verify_signature("s", body + b" ", good)
    assert not sp.verify_signature("", body, good) and not sp.verify_signature("s", body, None)
    # רק השדות הטכניים נשמרים, בסוג הנכון
    d = sp.clean_diag({"vpn": "UP", "accessibility": True, "sdk": 34, "contacts": ["x"], "device_owner": "yes",
                       "app_version": "2.3.0"})
    assert d == {"vpn": "UP", "accessibility": True, "sdk": 34, "app_version": "2.3.0"}
    msgs = sp.parse_incoming(incoming("היי", reply_to="wamid.9"), PHONE_ID)
    assert msgs == [sp.Incoming(ADMIN_WA, "wamid.in1", "היי", "wamid.9")]
    assert sp.parse_incoming(incoming("היי"), "999") == []                    # מספר עסקי אחר
    assert sp.parse_incoming({"object": "page"}, PHONE_ID) == []
    assert sp.parse_incoming(incoming(kind="image"), PHONE_ID)[0].text is None


# ---------------------------------------------------------------- זרימה מלאה

def test_customer_message_reaches_admin_whatsapp_and_reply_comes_back(env):
    svc, client = env
    fake = FakeWa()
    client.app.state.support.client_factory = fake
    status = configure(client, public_number="0521112233")
    assert status["can_send"] and status["can_receive"]
    assert "token" not in status and status["token_hint"] == "…9999"      # הסוד לא חוזר לממשק
    did, token = device(svc, client)

    r = client.post("/v1/device/support", headers=auth(token),
                    json={"text": "ה-VPN לא מתחבר\nמה לעשות?", "diag": {"vpn": "DOWN", "accessibility": True}})
    assert r.status_code == 201, r.text
    assert r.json()["forwarded"] is True
    kind, to, text = fake.sent[0]
    assert (kind, to) == ("text", ADMIN_WA)
    assert "ה-VPN לא מתחבר" in text and f"#{did}" in text and "VPN: DOWN" in text

    # המנהל לוחץ "השב" על ההודעה בוואטסאפ
    body, headers = signed(incoming("תפעיל מחדש את הטלפון 🙂", reply_to="wamid.1"))
    assert client.post("/v1/whatsapp/webhook", content=body, headers=headers).status_code == 200
    assert fake.sent[-1] == ("react", ADMIN_WA, "wamid.in1")               # ✅ על התשובה = נמסרה

    # הטלפון: התראה בדיווח התקופתי, ואז הצ'אט
    hb = client.post("/v1/device/heartbeat", headers=auth(token), json={"policy_version": 1}).json()
    assert hb["support"]["unread"] == 1
    me = client.get("/v1/device", headers=auth(token)).json()
    assert me["support"]["unread"] == 1 and me["support"]["whatsapp"] == ADMIN_WA
    chat = client.get("/v1/device/support", headers=auth(token)).json()["messages"]
    assert [m["from"] for m in chat] == ["customer", "support"]
    assert chat[1]["text"] == "תפעיל מחדש את הטלפון 🙂" and chat[1]["via"] == "whatsapp"
    assert client.get("/v1/device", headers=auth(token)).json()["support"]["unread"] == 0   # נקרא

    # Meta שולחת שוב את אותה הודעה — לא נשמרת פעמיים
    client.post("/v1/whatsapp/webhook", content=body, headers=headers)
    assert len(client.get("/v1/device/support", headers=auth(token)).json()["messages"]) == 2


def test_webhook_security_and_routing(env):
    svc, client = env
    fake = FakeWa()
    client.app.state.support.client_factory = fake
    configure(client)
    did, token = device(svc, client)
    # חתימה שגויה / בלי חתימה
    body, headers = signed(incoming(f"#{did} שלום"), secret="wrong")
    assert client.post("/v1/whatsapp/webhook", content=body, headers=headers).status_code == 403
    assert client.post("/v1/whatsapp/webhook", content=body,
                       headers={"Content-Type": "application/json"}).status_code == 403
    # מספר זר — מתעלמים (לא מגיע ללקוח)
    body, headers = signed(incoming(f"#{did} פריצה", sender="972509999999"))
    assert client.post("/v1/whatsapp/webhook", content=body, headers=headers).status_code == 200
    assert client.get("/v1/device/support", headers=auth(token)).json()["messages"] == []
    # בלי "השב" ובלי #מספר — עזרה למנהל
    body, headers = signed(incoming("שלום", wamid="wamid.in2"))
    client.post("/v1/whatsapp/webhook", content=body, headers=headers)
    assert fake.sent[-1][0] == "text" and "השב" in fake.sent[-1][2]
    # "#<מכשיר> טקסט" — מגיע ללקוח
    body, headers = signed(incoming(f"#{did} הכול תקין עכשיו", wamid="wamid.in3"))
    client.post("/v1/whatsapp/webhook", content=body, headers=headers)
    msgs = client.get("/v1/device/support", headers=auth(token)).json()["messages"]
    assert msgs[-1]["text"] == "הכול תקין עכשיו"
    # אימות ה-webhook מול Meta
    ok = client.get("/v1/whatsapp/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "my-verify",
                                                    "hub.challenge": "42"})
    assert ok.status_code == 200 and ok.text == "42"
    bad = client.get("/v1/whatsapp/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "x",
                                                     "hub.challenge": "42"})
    assert bad.status_code == 403


def test_outside_24h_window_falls_back_to_template(env):
    svc, client = env
    fake = FakeWa()
    fake.fail_text = sp.WaError(sp.ERR_OUTSIDE_WINDOW, "Re-engagement message")
    client.app.state.support.client_factory = fake
    configure(client, template="support_message")
    did, token = device(svc, client)
    client.post("/v1/device/support", headers=auth(token), json={"text": "שורה 1\nשורה 2"})
    kind, to, name, lang, params = fake.sent[0]
    assert (kind, name, lang) == ("template", "support_message", "he")
    assert params[1] == "שורה 1 שורה 2"                                     # בלי ירידת שורה (דרישת Meta)
    # בלי תבנית: ההודעה נשמרת, והממשק מראה למה לא נשלחה
    configure(client, template="")
    client.post("/v1/device/support", headers=auth(token), json={"text": "עוד שאלה"})
    thread = client.get(f"/v1/admin/support/{did}", headers=admin_headers(client)).json()
    assert "24 שעות" in thread["messages"][-1]["wa_error"]
    assert "24 שעות" in client.get("/v1/admin/whatsapp", headers=admin_headers(client)).json()["last_error"]


def test_dashboard_inbox_and_reply_without_whatsapp(env):
    svc, client = env
    did, token = device(svc, client)
    r = client.post("/v1/device/support", headers=auth(token), json={"text": "איך מתקינים אפליקציה?"})
    assert r.json()["forwarded"] is False                                  # וואטסאפ לא מוגדר — רק בממשק
    h = admin_headers(client)
    assert client.get("/v1/admin/overview", headers=h).json()["support_unread"] == 1
    threads = client.get("/v1/admin/support", headers=h).json()
    assert threads["unread"] == 1 and threads["threads"][0]["device_id"] == did
    assert threads["threads"][0]["last_text"] == "איך מתקינים אפליקציה?"
    thread = client.get(f"/v1/admin/support/{did}", headers=h).json()
    assert thread["messages"][0]["from"] == "customer"
    assert client.get("/v1/admin/overview", headers=h).json()["support_unread"] == 0      # נפתח = נקרא
    assert client.post(f"/v1/admin/support/{did}", headers=h, json={"text": "בלשונית חנות 👍"}).status_code == 201
    chat = client.get("/v1/device/support", headers=auth(token)).json()["messages"]
    assert chat[-1] == {"id": chat[-1]["id"], "from": "support", "text": "בלשונית חנות 👍", "ts": chat[-1]["ts"],
                        "via": "dashboard"}
    assert "author" not in chat[-1]                                        # שם המנהל לא נחשף ללקוח
    # רק השיחה שלו: טלפון אחר לא רואה
    _, other = device(svc, client, n=2)
    assert client.get("/v1/device/support", headers=auth(other)).json()["messages"] == []
    assert client.post("/v1/admin/support/9999", headers=h, json={"text": "x"}).status_code == 404


def test_validation_and_limits(env):
    svc, client = env
    _, token = device(svc, client)
    assert client.post("/v1/device/support", headers=auth(token), json={"text": "   "}).status_code == 400
    assert client.post("/v1/device/support", json={"text": "שלום"}).status_code == 401
    for _ in range(20):
        client.post("/v1/device/support", headers=auth(token), json={"text": "שלום"})
    assert client.post("/v1/device/support", headers=auth(token), json={"text": "שלום"}).status_code == 429
    h = admin_headers(client)
    assert client.put("/v1/admin/whatsapp", headers=h, json={"admin_number": "12"}).status_code == 400
    assert client.put("/v1/admin/whatsapp", headers=h, json={"phone_number_id": "abc"}).status_code == 400
    # שדה סוד ריק = בלי שינוי; clear_secrets מוחק
    configure(client)
    assert client.put("/v1/admin/whatsapp", headers=h, json={"token": ""}).json()["token_set"] is True
    assert client.put("/v1/admin/whatsapp", headers=h, json={"clear_secrets": True}).json()["token_set"] is False
    assert client.post("/v1/admin/whatsapp/test", headers=h).status_code == 400


def test_old_messages_are_pruned(env):
    svc, client = env
    did, token = device(svc, client)
    client.post("/v1/device/support", headers=auth(token), json={"text": "ישן"})
    with svc.db() as c:
        c.execute("UPDATE support_messages SET ts = ts - 400 * 86400")
    assert client.app.state.support.prune() == 1


def test_wa_client_speaks_the_graph_api(monkeypatch):
    """הלקוח האמיתי (urllib) מול שרת מקומי: נתיב, הרשאה, גוף, ופענוח שגיאה של Meta."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    seen = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append((self.path, self.headers["Authorization"], body))
            if body.get("type") == "template":
                out, code = {"error": {"code": 132001, "message": "Template name does not exist"}}, 400
            else:
                out, code = {"messages": [{"id": "wamid.REAL"}]}, 200
            data = json.dumps(out).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        monkeypatch.setattr(sp, "GRAPH", f"http://127.0.0.1:{srv.server_port}")
        c = sp.WaClient(sp.WaConfig(token="T0K", phone_number_id=PHONE_ID, admin_number=ADMIN_WA))
        assert c.text(ADMIN_WA, "שלום", reply_to="wamid.1") == "wamid.REAL"
        path, authz, body = seen[0]
        assert path == f"/v23.0/{PHONE_ID}/messages" and authz == "Bearer T0K"
        assert body == {"messaging_product": "whatsapp", "to": ADMIN_WA, "type": "text",
                        "text": {"body": "שלום", "preview_url": False}, "context": {"message_id": "wamid.1"}}
        with pytest.raises(sp.WaError) as e:
            c.template(ADMIN_WA, "nope", "he", ["a"])
        assert e.value.code == 132001 and "Template" in e.value.message
    finally:
        srv.shutdown()



def _active_with_phone(client, phone, n, name="לקוח"):
    """רישום מכשיר עם טלפון + אישור המנהל → מכשיר פעיל עם לקוח (לבדיקת ניהול תמיכה)."""
    reg = {"name": name, "phone": phone, "email": "", "level": 1, "options": [], "note": "",
           "device_name": "מכשיר", "platform": "android", "app_version": "2.0.0"}
    r = client.post("/v1/register", json={**reg, "public_key": wg_key(n), "device_uid": str(n) * 32})
    did, token = r.json()["device_id"], r.json()["token"]
    h = admin_headers(client)
    req = next(x for x in client.get("/v1/admin/requests", headers=h).json() if x["device_id"] == did)
    client.post(f"/v1/admin/requests/{req['id']}/approve", headers=h, json={"options": []})
    return did, token


def test_admin_device_manages_support_from_the_app(env):
    """מכשיר המנהל (טלפון הלקוח שלו = מספר הוואטסאפ של המנהל) מנהל תמיכה מתוך האפליקציה:
    רואה את כל השיחות, קורא שיחה, ועונה — ללקוח רגיל אין גישה (403)."""
    svc, client = env
    configure(client, admin_number="052-111-2233")          # הוואטסאפ של המנהל
    admin_did, admin_tok = _active_with_phone(client, "0521112233", 40, name="המנהל")
    cust_did, cust_tok = _active_with_phone(client, "0509998877", 41)

    # הלקוח כותב לתמיכה
    client.post("/v1/device/support", headers=auth(cust_tok), json={"text": "אין אינטרנט"})

    # מכשיר המנהל: הדגל admin מופיע בסטטוס
    me = client.get("/v1/device", headers=auth(admin_tok)).json()
    assert me.get("admin", {}).get("support_unread") == 1

    # רשימת השיחות
    threads = client.get("/v1/device/admin/support", headers=auth(admin_tok)).json()
    assert threads["unread"] == 1
    assert any(t["device_id"] == cust_did and t["last_text"] == "אין אינטרנט" for t in threads["threads"])

    # קריאת השיחה + מענה שחוזר ללקוח
    thread = client.get(f"/v1/device/admin/support/{cust_did}", headers=auth(admin_tok)).json()
    assert [m["text"] for m in thread["messages"]] == ["אין אינטרנט"]
    r = client.post(f"/v1/device/admin/support/{cust_did}", headers=auth(admin_tok), json={"text": "נסה להתחבר מחדש"})
    assert r.status_code == 201
    chat = client.get("/v1/device/support", headers=auth(cust_tok)).json()["messages"]
    assert chat[-1]["text"] == "נסה להתחבר מחדש" and chat[-1]["from"] == "support"

    # לקוח רגיל — אין לו גישה לניהול
    assert client.get("/v1/device/admin/support", headers=auth(cust_tok)).status_code == 403
    assert "admin" not in client.get("/v1/device", headers=auth(cust_tok)).json()


def test_admin_device_by_explicit_meta(env):
    """גם בלי התאמת טלפון: מכשיר שנקבע במפורש בדשבורד (meta admin_device_id) הוא מכשיר המנהל."""
    svc, client = env
    did, token = device(svc, client, n=42)
    assert client.get("/v1/device/admin/support", headers=auth(token)).status_code == 403
    with svc.db() as c:
        c.execute("INSERT INTO meta (key, value) VALUES ('admin_device_id', ?)"
                  " ON CONFLICT(key) DO UPDATE SET value = excluded.value", (str(did),))
        c.commit()
    assert client.get("/v1/device/admin/support", headers=auth(token)).status_code == 200


def _register(client, phone, uid, n):
    reg = {"name": "מנהל", "phone": phone, "email": "", "level": 1, "options": [], "note": "",
           "device_name": "DUOQIN", "platform": "android", "app_version": "2.0.0"}
    r = client.post("/v1/register", json={**reg, "public_key": wg_key(n), "device_uid": uid})
    did, token = r.json()["device_id"], r.json()["token"]
    h = admin_headers(client)
    req = next(x for x in client.get("/v1/admin/requests", headers=h).json() if x["device_id"] == did)
    client.post(f"/v1/admin/requests/{req['id']}/approve", headers=h, json={"options": []})
    return did, token


def test_admin_device_by_android_id_and_phone(env):
    """מכשיר המנהל מזוהה לפי מזהה המכשיר (ANDROID_ID → hash) וגם מספר הטלפון — שניהם חייבים להתאים."""
    svc, client = env
    uid = sp.device_uid_for(sp.DEFAULT_ADMIN_ANDROID_ID)
    _, tok = _register(client, sp.DEFAULT_ADMIN_PHONE, uid, 50)
    assert client.get("/v1/device/admin/support", headers=auth(tok)).status_code == 200
    # אותו מזהה עם טלפון אחר — לא מנהל
    _, other = _register(client, "0501112222", uid, 51)
    assert client.get("/v1/device/admin/support", headers=auth(other)).status_code == 403


def test_account_window_opens_google_hosts_for_one_device(env):
    """אחרי צריבה: חלון של 10 דקות שבו מסך הוספת חשבון Google עובד — רק במכשיר הזה."""
    svc, client = env
    did, _ = device(svc, client, n=52)
    r = client.post(f"/v1/admin/devices/{did}/account-window", headers=admin_headers(client))
    assert r.status_code == 200 and r.json()["minutes"] == 10
    ip = svc.device_row(did)["vpn_ip"]
    hosts = {h["host"] for h in svc.learned_passthrough(ip)}
    assert "lh3.googleusercontent.com" in hosts and "ssl.gstatic.com" in hosts
    assert client.post(f"/v1/admin/devices/{did}/account-window").status_code in (401, 403)
