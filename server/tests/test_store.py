"""החנות: קטלוג אפליקציות בסיסיות מאומתות, מעבר בין גרסאות זריעה, ותמונות דרך מסנן התמונות."""
import json

import pytest
from helpers import admin_headers

from filterphone.api import service as S
from filterphone.api.service import ApiError, store_seed


def test_seed_catalog_is_complete_and_consistent():
    seed = store_seed()
    pkgs = [a["package"] for a in seed["apps"]]
    assert len(pkgs) == len(set(pkgs)) >= 100
    assert {a["category"] for a in seed["apps"]} <= set(seed["categories"])
    for a in seed["apps"]:
        assert a["play_url"] == f"https://play.google.com/store/apps/details?id={a['package']}"
        assert a["icon_url"].startswith("https://play-lh.googleusercontent.com/")
        assert all(u.startswith("https://play-lh.googleusercontent.com/") for u in a["screenshots"])
        assert a["label"] and a["description"] and len(a["screenshots"]) <= 4
    for must in ("com.whatsapp", "com.waze", "com.bnhp.payments.paymentsapp", "clalit.android",
                 "com.google.android.apps.nbu.files", "com.driivz.mobile.android.afcon.driver", "co.bhol.app"):
        assert must in pkgs


def test_store_is_seeded_once_and_respects_deletions(env):
    svc, client = env
    h = admin_headers(client)
    apps = {a["package"]: a for a in client.get("/v1/admin/apps", headers=h).json()["apps"]}
    assert len(apps) == len(store_seed()["apps"]) + 1            # + FilterTube (כרטיס אחד, התקנה ישירה)
    ft = apps["com.filtertube.app"]
    assert ft["play_url"] == "" and ft["category"] == "מדיה"    # מתארחת אצלנו, לא מ-Google Play
    assert "com.filtertube.filtertube" not in apps              # הכפילות של v2 הוסרה
    client.delete(f"/v1/admin/apps/{apps['com.waze']['id']}", headers=h)
    svc.seed_store()
    assert "com.waze" not in {a["package"] for a in client.get("/v1/admin/apps", headers=h).json()["apps"]}


def test_upgrade_to_v3_makes_filtertube_direct_install_not_play(env):
    svc, client = env
    h = admin_headers(client)
    with svc.db() as c:                     # מצב של שרת שהזריע רק את גרסה 1
        c.execute("DELETE FROM managed_apps")
        c.execute("DELETE FROM meta WHERE key = 'store_seed_version'")
        c.execute("INSERT INTO meta (key, value) VALUES ('store_seeded', '1') ON CONFLICT(key) DO NOTHING")
        S.Services._seed_store_v1(c)
    svc.seed_store()
    apps = {a["package"]: a for a in client.get("/v1/admin/apps", headers=h).json()["apps"]}
    # FilterTube חזרה להיות התקנה ישירה (מתארחת אצלנו) — לא מ-Google Play
    ft = apps["com.filtertube.app"]
    assert ft["play_url"] == "" and ft["icon_url"] == "" and ft["category"] == "מדיה"
    assert "com.filtertube.filtertube" not in apps              # הכפילות הוסרה
    assert "com.whatsapp" in apps


def test_upgrade_to_v3_keeps_admin_edited_filtertube(env):
    svc, client = env
    h = admin_headers(client)
    with svc.db() as c:                     # שרת ב-v1, והמנהל ערך את הכרטיס השני
        c.execute("DELETE FROM managed_apps")
        c.execute("DELETE FROM meta WHERE key = 'store_seed_version'")
        c.execute("INSERT INTO meta (key, value) VALUES ('store_seeded', '1') ON CONFLICT(key) DO NOTHING")
        S.Services._seed_store_v1(c)
        c.execute("UPDATE managed_apps SET label = 'יוטיוב שלנו' WHERE package = 'com.filtertube.filtertube'")
    svc.seed_store()
    apps = {a["package"]: a for a in client.get("/v1/admin/apps", headers=h).json()["apps"]}
    assert apps["com.filtertube.filtertube"]["label"] == "יוטיוב שלנו"        # עריכה ידנית נשמרת (לא נמחק)
    assert apps["com.filtertube.app"]["play_url"] == ""                        # עדיין עובר להתקנה ישירה


def test_device_gets_filtered_media_urls_and_tab_order(env):
    svc, client = env
    row = next(a for a in svc.managed_apps() if a["package"] == "com.waze")
    entry = next(a for a in svc.device_managed_apps() if a["package"] == "com.waze")
    base = f"https://{svc.cfg.public_host}/v1/store/media/{row['id']}"
    assert entry["icon_url"] == f"{base}/icon?v={row['updated_at']}"
    assert entry["screenshots"][0] == f"{base}/s0?v={row['updated_at']}"
    assert "play-lh" not in json.dumps(entry)                  # אף תמונה לא נטענת ישירות מהמקור
    # השם ב-Play (עברית + אנגלית) — כדי שהמגן יזהה את העמוד שלה בחלון ההתקנה
    assert entry["play_titles"] == ["Waze: ניווט ועדכוני תנועה", "Waze Navigation & Live Traffic"]


def test_store_media_goes_through_the_strict_filter_and_is_cached(env, monkeypatch):
    svc, client = env
    app_id = next(a["id"] for a in svc.managed_apps() if a["package"] == "com.waze")
    fetched, engine = [], []
    monkeypatch.setattr(S, "fetch_image", lambda url, n: (fetched.append(url), (b"ORIGINAL", "image/jpeg"))[1])

    def classify(sock, header, data, timeout):
        engine.append(header)
        return ({"action": "pass"}, b"") if header and data == b"ORIGINAL" and len(engine) == 1 else \
            ({"action": "block", "ct": "image/png"}, b"PLACEHOLDER")
    monkeypatch.setattr(S.protocol, "call_sync", classify)

    r = client.get(f"/v1/store/media/{app_id}/icon")
    assert r.status_code == 200 and r.content == b"ORIGINAL" and engine[0]["level"] == 1
    assert client.get(f"/v1/store/media/{app_id}/icon").content == b"ORIGINAL"
    assert len(fetched) == 1                                   # מהמטמון — בלי הורדה ובלי מנוע
    r = client.get(f"/v1/store/media/{app_id}/s0")              # המנוע חסם → התמונה החלופית
    assert r.content == b"PLACEHOLDER" and r.headers["content-type"] == "image/png"
    assert client.get(f"/v1/store/media/{app_id}/s9").status_code == 404
    assert client.get("/v1/store/media/999999/icon").status_code == 404


def test_store_media_is_never_served_unfiltered_when_the_engine_is_down(env, monkeypatch):
    svc, _ = env
    app_id = next(a["id"] for a in svc.managed_apps() if a["package"] == "com.waze")
    monkeypatch.setattr(S, "fetch_image", lambda url, n: (b"ORIGINAL", "image/jpeg"))

    def down(*a, **k):
        raise ConnectionRefusedError()
    monkeypatch.setattr(S.protocol, "call_sync", down)
    with pytest.raises(ApiError) as e:
        svc.store_media(app_id, "icon")
    assert e.value.status == 503
