"""בוט הוואטסאפ מהדשבורד: הגדרות, מפתח AI (לא חוזר לדפדפן), מצב ו-QR מהבוט, ומה הבוט מקבל."""
import time

from helpers import admin_headers
from test_bot import BOT, auth, set_bot

from filterphone.api import wabot as W


def test_defaults_carry_the_business_info_from_the_old_bot(env):
    _, client = env
    v = client.get("/v1/admin/wabot", headers=admin_headers(client)).json()
    assert v["enabled"] is False and v["ai_provider"] == "gemini" and v["key_set"] is False
    assert "כשר פליי" in v["business_info"] and "אשדוד" in v["business_info"]
    assert v["hours"] == "13:00-15:00, 20:00-22:30" and v["status"]["state"] == "not_running"
    assert "ai_key" not in v


def test_save_validates_and_never_returns_the_key(env):
    _, client = env
    h = admin_headers(client)
    r = client.put("/v1/admin/wabot", headers=h, json={"enabled": True, "ai_key": "AIzaSyTESTKEY1234567890",
                                                        "hours": "09:00-13:00, 16:00-19:30", "handoff_minutes": 30})
    assert r.status_code == 200
    v = r.json()
    assert v["enabled"] and v["key_set"] and v["key_hint"] == "…7890" and "ai_key" not in v
    assert "AIzaSy" not in r.text
    # שדה מפתח ריק = בלי שינוי
    assert client.put("/v1/admin/wabot", headers=h, json={"ai_key": ""}).json()["key_set"] is True
    for bad in ({"hours": "כל היום"}, {"ai_provider": "skynet"}, {"handoff_minutes": 0}, {"ai_key": "has space"},
                {"ai_provider": "gemini", "ai_key": "gsk_" + "x" * 40}, {"ai_provider": "groq", "ai_key": "AIzaSyX" * 4}):
        assert client.put("/v1/admin/wabot", headers=h, json=bad).status_code == 400, bad
    assert client.put("/v1/admin/wabot", headers=h, json={"clear_key": True}).json()["key_set"] is False
    assert client.put("/v1/admin/wabot", json={"enabled": True}).status_code == 401


def test_bot_gets_config_with_key_and_reports_status_with_qr(env):
    _, client = env
    h = admin_headers(client)
    assert client.get("/v1/bot/config", headers=auth(BOT)).status_code == 401
    set_bot(client)
    client.put("/v1/admin/whatsapp", headers=h, json={"admin_number": "0527186881"})
    client.put("/v1/admin/wabot", headers=h, json={"enabled": True, "ai_key": "AIzaSyTESTKEY1234567890"})
    cfg = client.get("/v1/bot/config", headers=auth(BOT)).json()
    assert cfg["enabled"] and cfg["ai_key"] == "AIzaSyTESTKEY1234567890" and cfg["admin_number"] == "972527186881"
    # ממתין לסריקה → הדשבורד מקבל QR מוכן לתצוגה
    assert client.post("/v1/bot/status", headers=auth(BOT), json={"state": "qr", "qr": "2@abc,def"}).status_code == 200
    v = client.get("/v1/admin/wabot", headers=h).json()
    assert v["status"]["state"] == "qr" and v["qr_image"].startswith("data:image/svg+xml")
    # מחובר
    client.post("/v1/bot/status", headers=auth(BOT), json={"state": "ready", "me": "972527186881",
                                                           "ai": "gemini-2.5-flash", "stats": {"replies": 3}})
    v = client.get("/v1/admin/wabot", headers=h).json()
    assert v["status"]["state"] == "ready" and v["status"]["me"] == "972527186881" and v["qr_image"] == ""
    assert v["status"]["stats"] == {"replies": 3}
    assert client.post("/v1/bot/status", headers=auth(BOT), json={"state": "hacked"}).status_code == 400


def test_status_goes_stale_when_the_bot_stops_reporting(env, monkeypatch):
    svc, _ = env
    W.WaBot(svc).report({"state": "ready", "me": "972527186881"})
    assert W.WaBot(svc).status()["state"] == "ready"
    real = time.time
    monkeypatch.setattr(W.time, "time", lambda: real() + W.STATUS_STALE_S + 5)
    assert W.WaBot(svc).status()["state"] == "not_running"


def test_pick_gemini_prefers_the_newest_stable_flash():
    names = ["gemini-1.5-flash", "gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.5-flash-preview-05", "gemini-2.0-flash",
             "text-embedding-004"]
    assert W.pick_gemini(names) == "gemini-2.5-flash"
    assert W.pick_gemini(["gemini-3.0-flash", "gemini-2.5-flash"]) == "gemini-3.0-flash"
    assert W.pick_gemini(["gemini-2.5-flash-lite"]) == "gemini-2.5-flash-lite"
    assert W.pick_gemini([]) == ""


def test_cloud_api_is_skipped_only_while_the_bot_is_enabled(env):
    """בוט מופעל = הוא מעביר את הפניות; Cloud API לא שולח שוב (בלי כפילויות). כבוי — Cloud API שולח."""
    from test_support import FakeWa, configure, device
    from test_support import auth as dauth
    svc, client = env
    fake = FakeWa()
    client.app.state.support.client_factory = fake
    configure(client)
    set_bot(client)
    did, token = device(svc, client)
    h = admin_headers(client)
    client.put("/v1/admin/wabot", headers=h, json={"enabled": True})
    client.post("/v1/device/support", headers=dauth(token), json={"text": "שאלה ראשונה", "diag": {}})
    assert fake.sent == []
    client.put("/v1/admin/wabot", headers=h, json={"enabled": False})
    client.post("/v1/device/support", headers=dauth(token), json={"text": "שאלה שנייה", "diag": {}})
    assert [x[0] for x in fake.sent] == ["text"] and "שאלה שנייה" in fake.sent[0][2]


def test_whatsapp_list_settings_and_status(env):
    """ניהול מתוך הוואטסאפ: הרשימה "בוט" (שם, לקוחות חדשים אוטומטית) והמצב שלה בדשבורד."""
    _, client = env
    h = admin_headers(client)
    v = client.get("/v1/admin/wabot", headers=h).json()
    assert v["label_name"] == "בוט" and v["auto_new"] is True and v["include_contacts"] is False
    v = client.put("/v1/admin/wabot", headers=h, json={"label_name": " בוט שירות ", "include_contacts": True}).json()
    assert v["label_name"] == "בוט שירות" and v["include_contacts"] is True and v["auto_new"] is True
    assert client.put("/v1/admin/wabot", headers=h, json={"label_name": "  "}).status_code == 400
    set_bot(client)
    assert client.get("/v1/bot/config", headers=auth(BOT)).json()["label_name"] == "בוט שירות"
    client.post("/v1/bot/status", headers=auth(BOT), json={"state": "ready", "labels": "missing", "label": "בוט שירות"})
    st = client.get("/v1/admin/wabot", headers=h).json()["status"]
    assert st["labels"] == "missing" and st["label"] == "בוט שירות"
    client.post("/v1/bot/status", headers=auth(BOT), json={"state": "ready", "labels": "whatever"})
    assert client.get("/v1/admin/wabot", headers=h).json()["status"]["labels"] == ""


def test_legacy_import_from_install(env, monkeypatch):
    """install.sh: מפתח ה-Groq של הבוט הישן עובר לדשבורד, והבוט מופעל רק אם החיבור לוואטסאפ הועבר."""
    svc, _ = env
    W.import_legacy(svc, groq="gsk_" + "x" * 40, enable=True)
    s = W.WaBot(svc).settings()
    assert s["enabled"] and s["ai_provider"] == "groq" and s["ai_key"] == "gsk_" + "x" * 40
    # לא דורס הגדרות קיימות מהדשבורד
    W.WaBot(svc).save("admin", {"ai_provider": "gemini", "ai_key": "AIzaSyTESTKEY1234567890"})
    W.import_legacy(svc, groq="gsk_" + "y" * 40, enable=False)
    s = W.WaBot(svc).settings()
    assert s["ai_provider"] == "gemini" and s["ai_key"] == "AIzaSyTESTKEY1234567890" and s["enabled"]


def test_ai_marker_defaults_and_saves(env):
    """סימון AI: מופעל כברירת מחדל עם טקסט, והבוט מקבל אותו."""
    _, client = env
    h = admin_headers(client)
    v = client.get("/v1/admin/wabot", headers=h).json()
    assert v["mark_ai"] is True and v["ai_tag"]
    v = client.put("/v1/admin/wabot", headers=h, json={"mark_ai": False, "ai_tag": "✦ בוט"}).json()
    assert v["mark_ai"] is False and v["ai_tag"] == "✦ בוט"
    set_bot(client)
    cfg = client.get("/v1/bot/config", headers=auth(BOT)).json()
    assert cfg["mark_ai"] is False and cfg["ai_tag"] == "✦ בוט"


def test_learning_settings_default_on_and_reach_the_bot(env):
    _, client = env
    h = admin_headers(client)
    v = client.get("/v1/admin/wabot", headers=h).json()
    assert v["learn_from_replies"] is True and v["ask_when_unsure"] is True and "shadow" not in v
    set_bot(client)
    cfg = client.get("/v1/bot/config", headers=auth(BOT)).json()
    assert cfg["learn_from_replies"] is True and cfg["ask_when_unsure"] is True
    v = client.put("/v1/admin/wabot", headers=h, json={"learn_from_replies": False}).json()
    assert v["learn_from_replies"] is False


def test_bot_learns_and_the_admin_manages_knowledge(env):
    """הבוט שומר שאלה + תשובה שלך / עובדה; המנהל רואה, עורך ומוחק; כפילות לא נשמרת פעמיים."""
    _, client = env
    h = admin_headers(client)
    set_bot(client)
    r = client.post("/v1/bot/knowledge", headers=auth(BOT), json={
        "question": "יש לכם  סינון לאייפון?", "answer": "כן — חסימת אתרים ורשתות, בלי יוטיוב כשר.",
        "phone": "050-111-2222", "source": "reply"})
    assert r.status_code == 201
    qa = r.json()
    assert qa["kind"] == "qa" and qa["question"] == "יש לכם סינון לאייפון?" and qa["phone"] == "0501112222"
    client.post("/v1/bot/knowledge", headers=auth(BOT), json={
        "question": "יש לכם  סינון לאייפון?", "answer": "כן — חסימת אתרים ורשתות, בלי יוטיוב כשר."})
    f = client.post("/v1/bot/knowledge", headers=auth(BOT), json={"answer": "משלוח לכל הארץ תוך 3 ימי עסקים",
                                                                  "source": "teach"}).json()
    assert f["kind"] == "fact" and f["source"] == "teach"
    items = client.get("/v1/bot/knowledge", headers=auth(BOT)).json()["items"]
    assert len(items) == 2
    client.post("/v1/bot/knowledge/used", headers=auth(BOT), json={"ids": [qa["id"]]})
    admin_items = client.get("/v1/admin/wabot/knowledge", headers=h).json()["items"]
    assert next(i for i in admin_items if i["id"] == qa["id"])["uses"] == 1
    e = client.put(f"/v1/admin/wabot/knowledge/{qa['id']}", headers=h, json={"answer": "כן, לאייפון יש סינון."})
    assert e.json()["answer"] == "כן, לאייפון יש סינון."
    assert client.post("/v1/admin/wabot/knowledge", headers=h, json={"kind": "qa", "answer": "בלי שאלה"}).status_code == 400
    assert client.post("/v1/admin/wabot/knowledge", headers=h,
                       json={"question": "שעות?", "answer": "13:00-15:00"}).json()["source"] == "dashboard"
    assert client.delete(f"/v1/bot/knowledge/{f['id']}", headers=auth(BOT)).json() == {"deleted": f["id"]}
    assert client.delete(f"/v1/admin/wabot/knowledge/{f['id']}", headers=h).status_code == 404
    assert client.get("/v1/bot/knowledge").status_code == 401
    assert client.get("/v1/admin/wabot/knowledge").status_code == 401


def test_status_carries_progress_and_log(env):
    _, client = env
    h = admin_headers(client)
    set_bot(client)
    client.post("/v1/bot/status", headers=auth(BOT), json={"state": "starting", "progress": 45,
                                                           "log": ["08:00 מפעיל", "08:01 authenticated"]})
    st = client.get("/v1/admin/wabot", headers=h).json()["status"]
    assert st["progress"] == 45 and st["log"][-1] == "08:01 authenticated"


def test_wait_for_owner_in_business_hours_is_configurable(env):
    """בשעות הפעילות הבוט מחכה שתענה (ברירת מחדל 2 דקות); מחוץ לשעות — מיד (בבוט עצמו)."""
    _, client = env
    set_bot(client)
    h = admin_headers(client)
    assert client.get("/v1/admin/wabot", headers=h).json()["wait_seconds"] == 120
    assert client.put("/v1/admin/wabot", headers=h, json={"wait_seconds": 60}).json()["wait_seconds"] == 60
    assert client.get("/v1/bot/config", headers=auth(BOT)).json()["wait_seconds"] == 60
    for bad in (-1, 99999):
        assert client.put("/v1/admin/wabot", headers=h, json={"wait_seconds": bad}).status_code == 400


def test_unlisted_answers_and_product_guide_reach_the_bot(env):
    """מענה למי שלא ברשימה (ברירת מחדל: פעיל, 45 דקות) + מדריך המוצר וההתקנה המובנה, עם קישורים."""
    _, client = env
    set_bot(client)
    h = admin_headers(client)
    view = client.get("/v1/admin/wabot", headers=h).json()
    assert view["answer_unlisted"] is True and view["unlisted_minutes"] == 45 and view["product_guide"] is True
    cfg = client.get("/v1/bot/config", headers=auth(BOT)).json()
    assert "אנדרואיד" in cfg["guide"] and "אייפון" in cfg["guide"]
    assert cfg["links"]["download"] in cfg["guide"] and cfg["links"]["site"] in cfg["guide"]
    # FilterTube — קישור משלה (לא של FilterPhone) כשמבקשים את היוטיוב
    assert cfg["links"]["filtertube_download"] in cfg["guide"] and "FilterTube" in cfg["guide"]
    assert cfg["links"]["filtertube_download"] != cfg["links"]["download"]
    for must in ("Play Protect", "חסימה אוטומטית", "שיאומי", "חשבון", "צילום מסך", "3 ניסיונות"):
        assert must in cfg["install_guide"], must
    assert client.put("/v1/admin/wabot", headers=h,
                      json={"answer_unlisted": False, "unlisted_minutes": 30, "product_guide": False}).status_code == 200
    cfg = client.get("/v1/bot/config", headers=auth(BOT)).json()
    assert cfg["answer_unlisted"] is False and cfg["unlisted_minutes"] == 30
    assert cfg["guide"] == "" and cfg["install_guide"] == ""
    for bad in (0, 4, 2000, "x"):
        assert client.put("/v1/admin/wabot", headers=h, json={"unlisted_minutes": bad}).status_code in (400, 422)
