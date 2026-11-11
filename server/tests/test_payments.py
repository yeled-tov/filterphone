"""תשלומים: הגדרות (מפתחות לא חוזרים לדפדפן), מחירון, קישור תשלום ללקוח, אישור מהספק (webhook חתום,
אידמפוטנטי), הארכת "בתוקף עד" והודעה ללקוח — במצב בדיקה, בלי כסף אמיתי."""
import json
import time

from test_app_requests import _active_play_managed_phone

from filterphone.api.payments import Payments, add_months, shekels, sign


def _setup(client):
    dh, h = _active_play_managed_phone(client)                  # לקוח "משה" 0501234567 + מכשיר פעיל
    me = client.get("/v1/device", headers=dh).json()
    dev = client.get(f"/v1/admin/devices/{me['device_id']}", headers=h).json()
    return dh, h, dev["customer"]["id"], me["device_id"]


def test_helpers():
    t = 1706659200                                              # 31.1.2024
    assert time.gmtime(add_months(t, 1))[:3] == (2024, 2, 29)   # שנה מעוברת
    assert time.gmtime(add_months(t, 12))[:3] == (2025, 1, 31)
    assert shekels(4000) == "40" and shekels(1590) == "15.90"


def test_payments_need_a_connected_provider_and_settings_are_owner_only(env):
    svc, client = env
    _, h, cust, _ = _setup(client)
    r = client.post("/v1/admin/payments", headers=h, json={"customer_id": cust, "amount": 4000, "description": "x"})
    assert r.status_code == 409 and "לא מחוברת" in r.json()["error"]
    view = client.put("/v1/admin/payment-settings", headers=h,
                      json={"enabled": True, "provider": "test", "credentials": {"api_key": "SECRET123"}}).json()
    assert view["ready"] and view["webhook_url"].endswith("/v1/payments/webhook/test")
    assert "SECRET123" not in json.dumps(view)                  # מפתח לא חוזר לדפדפן
    assert Payments(svc).settings()["credentials"]["api_key"] == "SECRET123"
    assert client.put("/v1/admin/payment-settings", headers=h, json={"provider": "nope"}).status_code == 400


def test_monthly_plan_link_then_provider_confirms_and_paid_until_moves_a_month(env):
    svc, client = env
    dh, h, cust, dev = _setup(client)
    client.put("/v1/admin/payment-settings", headers=h, json={"enabled": True, "provider": "test"})
    plan = client.post("/v1/admin/plans", headers=h,
                       json={"code": "monthly", "title": "מנוי חודשי", "amount": 4000, "interval": "month"}).json()
    assert plan["months"] == 1
    assert client.post("/v1/admin/plans", headers=h, json={"code": "monthly", "title": "x", "amount": 100}
                       ).status_code == 409
    p = client.post("/v1/admin/payments", headers=h, json={"phone": "050-1234567", "plan_code": "monthly"})
    assert p.status_code == 201, p.text
    p = p.json()
    assert p["status"] == "pending" and p["amount"] == 4000 and p["customer_id"] == cust and p["device_id"] == dev
    assert p["link"].startswith("https://") and "/v1/pay/" in p["link"] and "40 ₪" in p["wa_text"]
    token = p["token"]
    # הקישור ללקוח מעביר לדף התשלום (במצב בדיקה — דף הבדיקה שלנו)
    r = client.get(f"/v1/pay/{token}", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].endswith(f"/v1/pay/{token}/test")
    assert "תשלום הצליח" in client.get(f"/v1/pay/{token}/test").text
    before = int(time.time())
    assert "התקבל" in client.post(f"/v1/pay/{token}/test", content="ok=1").text
    row = next(x for x in client.get("/v1/admin/payments", headers=h).json()["payments"] if x["token"] == token)
    assert row["status"] == "paid" and row["receipt_number"] == f"TEST-{row['id']}"
    c = client.get(f"/v1/admin/devices/{dev}", headers=h).json()["customer"]
    assert c["payment"] == "paid" and c["paid_until"] >= add_months(before, 1) - 5
    # הודעה ללקוח באפליקציה
    with svc.db() as conn:
        body = conn.execute("SELECT body FROM support_messages WHERE device_id = ? ORDER BY id DESC LIMIT 1",
                            (dev,)).fetchone()["body"]
    assert "התשלום התקבל" in body and "בתוקף עד" in body
    # תשלום נוסף — מאריך מהתאריך הקיים (לא מהיום)
    p2 = client.post("/v1/admin/payments", headers=h, json={"customer_id": cust, "plan_code": "monthly"}).json()
    client.post(f"/v1/pay/{p2['token']}/test", content="ok=1")
    c2 = client.get(f"/v1/admin/devices/{dev}", headers=h).json()["customer"]
    assert c2["paid_until"] == add_months(c["paid_until"], 1)
    # קישור ששולם — מסביר ולא מעביר שוב
    assert "כבר התקבל" in client.get(f"/v1/pay/{token}", follow_redirects=False).text


def test_webhook_is_verified_and_idempotent(env):
    svc, client = env
    _, h, cust, _ = _setup(client)
    client.put("/v1/admin/payment-settings", headers=h, json={"enabled": True, "provider": "test"})
    p = client.post("/v1/admin/payments", headers=h,
                    json={"customer_id": cust, "amount": 7000, "description": "התקנה"}).json()
    secret = Payments(svc).settings()["webhook_secret"]
    body = json.dumps({"event_id": "e1", "status": "paid", "token": p["token"], "amount": 7000}).encode()
    bad = client.post("/v1/payments/webhook/test", content=body, headers={"x-fp-signature": "nope"})
    assert bad.status_code == 401
    wrong_amount = json.dumps({"event_id": "e0", "status": "paid", "token": p["token"], "amount": 100}).encode()
    assert client.post("/v1/payments/webhook/test", content=wrong_amount,
                       headers={"x-fp-signature": sign(secret, wrong_amount)}).status_code == 400
    ok = client.post("/v1/payments/webhook/test", content=body, headers={"x-fp-signature": sign(secret, body)})
    assert ok.status_code == 200 and ok.json() == {"ok": True}
    again = client.post("/v1/payments/webhook/test", content=body, headers={"x-fp-signature": sign(secret, body)})
    assert again.json()["duplicate"] is True
    row = next(x for x in client.get("/v1/admin/payments", headers=h).json()["payments"] if x["id"] == p["id"])
    assert row["status"] == "paid"
    # תשלום חד-פעמי (בלי מסלול) — "שולם", בלי לשנות את "בתוקף עד"
    assert client.get(f"/v1/admin/devices/{row['device_id']}", headers=h).json()["customer"]["paid_until"] is None


def test_failed_cancel_and_manual_payment(env):
    svc, client = env
    _, h, cust, dev = _setup(client)
    client.put("/v1/admin/payment-settings", headers=h, json={"enabled": True, "provider": "test"})
    client.post("/v1/admin/plans", headers=h, json={"code": "yearly", "title": "שנתי", "amount": 15000,
                                                    "interval": "year"})
    p = client.post("/v1/admin/payments", headers=h, json={"customer_id": cust, "plan_code": "yearly"}).json()
    client.post(f"/v1/pay/{p['token']}/test", content="ok=0")
    assert client.get("/v1/admin/payments?status=failed", headers=h).json()["payments"][0]["id"] == p["id"]
    # שולם במזומן — סימון ידני מאריך שנה
    m = client.post(f"/v1/admin/payments/{p['id']}/mark-paid", headers=h, json={"note": "מזומן"}).json()
    assert m["status"] == "paid" and m["receipt_number"] == "מזומן"
    until = client.get(f"/v1/admin/devices/{dev}", headers=h).json()["customer"]["paid_until"]
    assert until >= add_months(int(time.time()), 12) - 5
    p3 = client.post("/v1/admin/payments", headers=h, json={"customer_id": cust, "plan_code": "yearly"}).json()
    assert client.post(f"/v1/admin/payments/{p3['id']}/cancel", headers=h).json()["status"] == "cancelled"
    assert "לא בתוקף" in client.get(f"/v1/pay/{p3['token']}", follow_redirects=False).text
    assert client.post(f"/v1/admin/payments/{p3['id']}/cancel", headers=h).status_code == 409


def test_staff_with_payments_area_cannot_touch_provider_keys():
    """איש צוות עם "תשלומים": מחירון וקישורים — כן; הגדרות הסליקה והמפתחות — רק לבעלים."""
    from filterphone.api import perms
    staff = {"payments": "edit"}
    assert perms.allowed("staff", staff, "POST", "/v1/admin/payments")
    assert perms.allowed("staff", staff, "PUT", "/v1/admin/plans/3")
    assert not perms.allowed("staff", staff, "GET", "/v1/admin/payment-settings")
    assert not perms.allowed("staff", {"payments": "view"}, "POST", "/v1/admin/payments")
    assert perms.allowed("owner", {}, "PUT", "/v1/admin/payment-settings")
