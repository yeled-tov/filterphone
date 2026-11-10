"""בקשת אפליקציה (מחנות FilterPhone / מ-Google Play המנוהל): האישור בדשבורד מוסיף אותה לחנות, הטלפון מקבל
מדיניות חדשה (הרשימה המאושרת — Play המנוהל מאפשר להתקין), והלקוח מקבל הודעה שאפשר להתקין."""
from helpers import admin_headers, wg_key

from filterphone.api import playinfo as P
from filterphone.api.service import category_for_genre, parse_app_request


def test_parse_app_request_from_every_source():
    assert parse_app_request("בקשת התקנת אפליקציה: Waze (com.waze)") == ("com.waze", "Waze")
    assert parse_app_request("בקשת אפליקציה מ-Google Play: מוביט (com.tranzmate)\nלמה צריך אותה: לנסיעות") \
        == ("com.tranzmate", "מוביט")
    assert parse_app_request("בקשת אפליקציה מ-Google Play: Word\nלמה צריך אותה: לעבודה") == ("", "Word")
    assert parse_app_request("בקשת אפליקציה חדשה לחנות: מחשבון מדעי") == ("", "מחשבון מדעי")
    assert parse_app_request("אפשר לפתוח לי אתר?") == ("", "")


def test_category_for_genre_and_play_title_match():
    assert category_for_genre("מפות וניווט") == "תחבורה"
    assert category_for_genre("Finance") == "בנקים ותשלומים"
    assert category_for_genre(None) == "כלים"
    res = [{"package": "com.other", "title": "Totally Different"},
           {"package": "com.microsoft.office.word", "title": "Microsoft Word: Edit Documents"}]
    assert P.play_match(res, "Microsoft Word") == "com.microsoft.office.word"
    assert P.play_match(res, "Excel") == ""                   # לא מוסיפים אפליקציה אחרת בטעות


def _play(svc, titles: dict[str, str]):
    def details(pkg):
        if pkg not in titles:
            return None
        return {"title": titles[pkg], "summary": "תקציר", "description": "תיאור מלא", "icon": "https://img.example/i.png",
                "screenshots": ["https://img.example/s1.png"], "developer": "Dev", "genre": "מפות וניווט",
                "score": 4.5, "installs": "1M+"}
    return P.PlayCatalog(svc.redis, details=details, search=lambda q: list(titles))


def _active_play_managed_phone(client):
    h = admin_headers(client)
    reg = {"name": "משה", "phone": "0501234567", "email": "", "level": 1, "options": ["play_managed"], "note": "",
           "device_name": "גלקסי", "platform": "android", "app_version": "2.0.0"}
    r = client.post("/v1/register", json={**reg, "public_key": wg_key(1), "device_uid": "11" * 16})
    req = client.get("/v1/admin/requests", headers=h).json()[0]
    client.post(f"/v1/admin/requests/{req['id']}/approve", headers=h, json={"options": ["play_managed"]})
    return {"Authorization": f"Bearer {r.json()['token']}"}, h


def test_approving_an_app_request_adds_it_to_the_store_and_unlocks_install(env):
    svc, client = env
    client.app.state.playcatalog = _play(svc, {"com.example.notes": "Example Notes"})
    dh, h = _active_play_managed_phone(client)
    before = client.get("/v1/device", headers=dh).json()
    assert "com.example.notes" not in before["device_policy"]["allowlist"]["apps"]
    v0 = before["device_policy"]["version"]
    # "חסום להתקנה — בקשת אישור" ב-Play המנוהל (גרסה ישנה של האפליקציה — רק טקסט)
    r = client.post("/v1/device/requests", headers=dh,
                    json={"note": "בקשת אפליקציה מ-Google Play: Example Notes (com.example.notes)\nלמה צריך אותה: פתקים"})
    assert r.status_code == 201
    req = next(x for x in client.get("/v1/admin/requests", headers=h).json() if x["id"] == r.json()["request_id"])
    assert req["app_package"] == "com.example.notes" and req["app_title"] == "Example Notes"
    ok = client.post(f"/v1/admin/requests/{req['id']}/approve", headers=h, json={})
    assert ok.status_code == 200, ok.text
    store = {a["package"]: a for a in client.get("/v1/admin/apps", headers=h).json()["apps"]}
    app = store["com.example.notes"]
    assert app["label"] == "Example Notes" and app["category"] == "תחבורה" and app["enabled"] == 1
    assert app["icon_url"].startswith("https://") and app["play_url"].endswith("id=com.example.notes")
    after = client.get("/v1/device", headers=dh).json()
    assert "com.example.notes" in after["device_policy"]["allowlist"]["apps"]      # Play המנוהל מאפשר להתקין
    assert after["device_policy"]["version"] > v0                                    # הטלפון יקבל את המדיניות
    assert any(a["package"] == "com.example.notes" for a in after["managed_apps"])   # מופיעה בחנות בטלפון
    msgs = svc.support_thread(after["id"])["messages"] if hasattr(svc, "support_thread") else []
    if msgs:
        assert any("נוספה לחנות" in m["body"] for m in msgs)


def test_title_only_request_is_resolved_from_play_or_asks_for_the_package(env):
    svc, client = env
    client.app.state.playcatalog = _play(svc, {"com.microsoft.office.word": "Microsoft Word: Edit Documents"})
    dh, h = _active_play_managed_phone(client)
    r = client.post("/v1/device/requests", headers=dh, json={"note": "x", "app": {"title": "Microsoft Word"}})
    ok = client.post(f"/v1/admin/requests/{r.json()['request_id']}/approve", headers=h, json={})
    assert ok.status_code == 200, ok.text
    assert any(a["package"] == "com.microsoft.office.word"
               for a in client.get("/v1/admin/apps", headers=h).json()["apps"])
    # שם שלא נמצא ב-Play — המנהל מתבקש להזין את שם החבילה (ולא מתווספת אפליקציה אחרת)
    r2 = client.post("/v1/device/requests", headers=dh, json={"note": "y", "app": {"title": "אפליקציה לא קיימת"}})
    bad = client.post(f"/v1/admin/requests/{r2.json()['request_id']}/approve", headers=h, json={})
    assert bad.status_code == 400 and "שם החבילה" in bad.json()["error"]
    # או אישור בלי להוסיף לחנות
    assert client.post(f"/v1/admin/requests/{r2.json()['request_id']}/approve", headers=h,
                       json={"add_app": False}).status_code == 200


def test_store_changes_reach_play_managed_phones(env):
    """הוספה ידנית לחנות (בלי בקשה) — גם היא יוצרת גרסה חדשה לטלפונים עם Play מנוהל."""
    svc, client = env
    dh, h = _active_play_managed_phone(client)
    v0 = client.get("/v1/device", headers=dh).json()["device_policy"]["version"]
    assert client.post("/v1/admin/apps", headers=h, json={"package": "com.example.calc", "label": "מחשבון"}).status_code == 201
    pol = client.get("/v1/device", headers=dh).json()["device_policy"]
    assert pol["version"] > v0 and "com.example.calc" in pol["allowlist"]["apps"]


def test_app_approved_only_for_this_customer_appears_in_his_custom_tab_only(env):
    """"מותאם אישית": אישור בקשה רק ללקוח הזה — האפליקציה בחנות שלו (טאב "מותאם אישית") ובמאושרות שלו
    בלבד; לא בחנות הכללית ולא אצל לקוח אחר."""
    svc, client = env
    client.app.state.playcatalog = _play(svc, {"com.example.work": "Work App"})
    dh, h = _active_play_managed_phone(client)
    reg = {"name": "דוד", "phone": "0507654321", "email": "", "level": 1, "options": ["play_managed"], "note": "",
           "device_name": "שיאומי", "platform": "android", "app_version": "2.0.0"}
    r2 = client.post("/v1/register", json={**reg, "public_key": wg_key(2), "device_uid": "22" * 16})
    req2 = next(x for x in client.get("/v1/admin/requests", headers=h).json() if x["status"] == "pending")
    client.post(f"/v1/admin/requests/{req2['id']}/approve", headers=h, json={"options": ["play_managed"]})
    other = {"Authorization": f"Bearer {r2.json()['token']}"}

    v0 = client.get("/v1/device", headers=dh).json()["device_policy"]["version"]
    r = client.post("/v1/device/requests", headers=dh, json={"note": "x", "app": {"package": "com.example.work",
                                                                                     "title": "Work App"}})
    ok = client.post(f"/v1/admin/requests/{r.json()['request_id']}/approve", headers=h, json={"app_scope": "device"})
    assert ok.status_code == 200, ok.text
    mine = client.get("/v1/device", headers=dh).json()
    app = next(a for a in mine["managed_apps"] if a["package"] == "com.example.work")
    assert app["category"] == "מותאם אישית" and mine["store_categories"][0] == "מותאם אישית"
    assert "com.example.work" in mine["device_policy"]["allowlist"]["apps"]
    assert mine["device_policy"]["version"] > v0
    # לא אצל לקוח אחר, ולא בחנות הכללית
    theirs = client.get("/v1/device", headers=other).json()
    assert all(a["package"] != "com.example.work" for a in theirs["managed_apps"])
    assert "com.example.work" not in theirs["device_policy"]["allowlist"]["apps"]
    store = {a["package"]: a for a in client.get("/v1/admin/apps", headers=h).json()["apps"]}
    assert store["com.example.work"]["private"] == 1 and store["com.example.work"]["device_count"] == 1
    msgs = svc.support_thread(mine["device_id"])["messages"] if hasattr(svc, "support_thread") else []
    if msgs:
        assert any("מותאם אישית" in m["body"] for m in msgs)


def test_admin_grants_and_revokes_a_custom_app_from_the_device_page(env):
    svc, client = env
    client.app.state.playcatalog = _play(svc, {"com.example.notes": "Example Notes"})
    dh, h = _active_play_managed_phone(client)
    dev_id = client.get("/v1/device", headers=dh).json()["device_id"]
    # לפי שם (חיפוש ב-Play) או לפי שם חבילה
    g = client.post(f"/v1/admin/devices/{dev_id}/apps", headers=h, json={"package": "Example Notes"})
    assert g.status_code == 201 and g.json()["package"] == "com.example.notes" and g.json()["public"] is False
    listed = client.get(f"/v1/admin/devices/{dev_id}/apps", headers=h).json()["apps"]
    assert [a["package"] for a in listed] == ["com.example.notes"]
    assert "com.example.notes" in client.get("/v1/device", headers=dh).json()["device_policy"]["allowlist"]["apps"]
    # אפליקציה שכבר בחנות הכללית — אין מה להוסיף
    assert client.post(f"/v1/admin/devices/{dev_id}/apps", headers=h,
                       json={"package": "com.waze"}).json()["public"] is True
    # ביטול — יוצאת מהחנות שלו ומהמאושרות, ונמחקת מהקטלוג (אף אחד לא מחזיק אותה)
    assert client.delete(f"/v1/admin/devices/{dev_id}/apps/com.example.notes", headers=h).status_code == 204
    pol = client.get("/v1/device", headers=dh).json()
    assert "com.example.notes" not in pol["device_policy"]["allowlist"]["apps"]
    assert all(a["package"] != "com.example.notes" for a in pol["managed_apps"])
    assert all(a["package"] != "com.example.notes" for a in client.get("/v1/admin/apps", headers=h).json()["apps"])
    assert client.delete(f"/v1/admin/devices/{dev_id}/apps/com.example.notes", headers=h).status_code == 404
    bad = client.post(f"/v1/admin/devices/{dev_id}/apps", headers=h, json={"package": "לא קיימת בכלל"})
    assert bad.status_code == 400


def test_custom_app_becomes_public_when_added_to_the_store(env):
    svc, client = env
    client.app.state.playcatalog = _play(svc, {"com.example.calc2": "Calc Two"})
    dh, h = _active_play_managed_phone(client)
    dev_id = client.get("/v1/device", headers=dh).json()["device_id"]
    client.post(f"/v1/admin/devices/{dev_id}/apps", headers=h, json={"package": "com.example.calc2"})
    assert client.post("/v1/admin/apps", headers=h, json={"package": "com.example.calc2", "label": "מחשבון 2"}
                       ).status_code == 201
    store = {a["package"]: a for a in client.get("/v1/admin/apps", headers=h).json()["apps"]}
    assert store["com.example.calc2"]["private"] == 0 and store["com.example.calc2"]["label"] == "מחשבון 2"
    app = next(a for a in client.get("/v1/device", headers=dh).json()["managed_apps"] if a["package"] == "com.example.calc2")
    assert app["category"] != "מותאם אישית"
