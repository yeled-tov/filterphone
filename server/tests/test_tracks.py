"""מסלולי סינון: בלי אינטרנט / ניווט ודרכים בלבד / בלי וואטסאפ — כל מסלול רק מוסיף חסימות."""
import pytest
from helpers import admin_headers
from test_bot import active_customer, auth

from filterphone.api.app import device_limiter, register_limiter, support_limiter
from filterphone.api.catalog import NAV_APPS, TRACKS, DeviceSettings, compile_settings, store_allowed


@pytest.fixture(autouse=True)
def _limits():
    support_limiter._hits.clear()
    device_limiter._hits.clear()
    register_limiter._hits.clear()


def test_tracks_only_add_blocks():
    plain = compile_settings(2, DeviceSettings.load('{"options": ["tiktok"]}'))
    assert plain.allowlist_apps is None and not plain.dpc["approved_apps_only"]
    for track in TRACKS:
        s = DeviceSettings.load('{"options": ["tiktok"]}')
        assert s.update({"track": track}) == []
        comp = compile_settings(2, s)
        assert set(plain.hosts) <= set(comp.hosts) and set(plain.apps) <= set(comp.apps), track
    nav = compile_settings(2, DeviceSettings.load('{"track": "navigation"}'))
    assert nav.allowlist_apps == NAV_APPS and nav.dpc["approved_apps_only"] and nav.dpc["disallow_tethering"]
    assert "com.android.chrome" in nav.apps and "com.whatsapp" in nav.apps
    none = compile_settings(1, DeviceSettings.load('{"track": "no_internet"}'))
    assert none.allowlist_apps == () and none.dpc["approved_apps_only"]
    nowa = compile_settings(2, DeviceSettings.load('{"track": "no_whatsapp"}'))
    assert "com.whatsapp" in nowa.apps and nowa.allowlist_apps is None
    assert DeviceSettings.load('{"track": "bogus"}').track == ""
    assert DeviceSettings.load("{}").update({"track": "bogus"}) == ["מסלול סינון לא מוכר"]


def test_store_per_track():
    assert store_allowed("", "com.whatsapp") and store_allowed("", "com.waze")
    assert not store_allowed("no_whatsapp", "com.whatsapp") and store_allowed("no_whatsapp", "com.waze")
    assert store_allowed("navigation", "com.waze") and not store_allowed("navigation", "com.whatsapp")
    assert not store_allowed("no_internet", "com.waze")


def test_track_reaches_the_phone_policy_allowlist_and_store(env):
    svc, client = env
    h = admin_headers(client)
    did, token = active_customer(svc, client)
    status = client.get("/v1/device", headers=auth(token)).json()
    all_store = {a["package"] for a in status["managed_apps"]}
    assert "com.whatsapp" in all_store and "com.waze" in all_store
    r = client.patch(f"/v1/admin/devices/{did}", headers=h, json={"track": "navigation"})
    assert r.status_code == 200 and r.json()["settings"]["track"] == "navigation"
    status = client.get("/v1/device", headers=auth(token)).json()
    pol = status["device_policy"]
    assert pol["allowlist"] == {"enabled": True, "apps": list(NAV_APPS)}
    assert {a["package"] for a in status["managed_apps"]} <= set(NAV_APPS)
    assert status["blocks"][0]["id"] == "track:navigation"
    hb = client.post("/v1/device/heartbeat", headers=auth(token), json={"policy_version": 1}).json()
    assert {a["package"] for a in hb["managed_apps"]} <= set(NAV_APPS)
    # בלי וואטסאפ: וואטסאפ לא בחנות, השאר כן
    client.patch(f"/v1/admin/devices/{did}", headers=h, json={"track": "no_whatsapp"})
    store = {a["package"] for a in client.get("/v1/device", headers=auth(token)).json()["managed_apps"]}
    assert "com.whatsapp" not in store and "com.waze" in store
    # המסלולים בקטלוג של הדשבורד
    tracks = {t["id"] for t in client.get("/v1/admin/catalog", headers=h).json()["tracks"]}
    assert tracks == set(TRACKS)


def test_allow_google_accounts_takes_only_google_services_out_of_the_vpn(env):
    """הוספת חשבון Google: רק שירותי Google (אפליקציית החשבון) יוצאים מה-VPN בטלפון — בלי לפתוח את שרתי
    התמונות של גוגל לכל המכשיר (כך כרום ממשיך לסנן גם את lh3...)."""
    from filterphone.api.catalog import GOOGLE_ACCOUNT_APP, DeviceSettings
    from filterphone.api.service import PASSTHROUGH_KEY, Services
    svc, client = env
    h = admin_headers(client)
    did, token = active_customer(svc, client)
    ip = svc.device_row(did)["vpn_ip"]

    def bypass_apps():
        pol = client.get("/v1/device", headers=auth(token)).json()["device_policy"]
        return [a["package"] for a in pol["vpn_bypass"]["apps"]]

    def passthrough():
        return {k.split("|", 1)[1] for k in svc.redis.hkeys(PASSTHROUGH_KEY) if k.startswith(f"{ip}|")}

    assert client.get(f"/v1/admin/devices/{did}", headers=h).json()["settings"]["allow_google_accounts"] is False
    assert GOOGLE_ACCOUNT_APP not in bypass_apps()
    # שארית מהגרסה הקודמת (שרתי תמונות פתוחים לכל המכשיר) — נמחקת
    svc.redis.hset(PASSTHROUGH_KEY, f"{ip}|lh3.googleusercontent.com", 9e12)
    r = client.patch(f"/v1/admin/devices/{did}", headers=h, json={"allow_google_accounts": True})
    assert r.status_code == 200 and r.json()["settings"]["allow_google_accounts"] is True
    assert GOOGLE_ACCOUNT_APP in bypass_apps() and "com.filtertube.app" in bypass_apps()
    assert not passthrough() & set(Services.LEGACY_ACCOUNT_HOSTS)          # שום שרת לא נפתח בפרוקסי
    # גם כשהמנהל כיבה את רשימת ההחרגות — המתג עדיין מוציא רק את שירותי Google
    client.patch(f"/v1/admin/devices/{did}", headers=h, json={"vpn_bypass": {"enabled": False}})
    assert bypass_apps() == [GOOGLE_ACCOUNT_APP]
    assert DeviceSettings.load(DeviceSettings.load(svc.device_row(did)["policy"]).dump()).allow_google_accounts is True
    client.patch(f"/v1/admin/devices/{did}", headers=h, json={"allow_google_accounts": False})
    assert GOOGLE_ACCOUNT_APP not in bypass_apps()
    # ידנית (בלי המתג) — אי אפשר להוציא את שירותי Google
    client.patch(f"/v1/admin/devices/{did}", headers=h, json={"vpn_bypass": {"enabled": True, "extra": [GOOGLE_ACCOUNT_APP]}})
    assert GOOGLE_ACCOUNT_APP not in bypass_apps()


def test_safe_browser_mode_follows_chrome_and_tracks():
    full = compile_settings(2, DeviceSettings.load("{}"))
    assert full.safe_browser == "full" and "safe_browser" not in full.device_json()   # בלי גרסה חדשה לכולם
    no_browsers = compile_settings(2, DeviceSettings.load('{"options": ["browsers_all"]}'))
    assert no_browsers.safe_browser == "off" and no_browsers.device_json()["safe_browser"] == "off"
    for track in ("no_internet", "navigation"):
        assert compile_settings(2, DeviceSettings.load(f'{{"track": "{track}"}}')).safe_browser == "off", track
    assert compile_settings(2, DeviceSettings.load('{"track": "no_whatsapp"}')).safe_browser == "full"
    essential = DeviceSettings.load('{"track": "no_internet", "safe_browser": "essential"}')
    assert compile_settings(2, essential).device_json()["safe_browser"] == "essential"   # בחירה מפורשת של המנהל
    assert DeviceSettings.load(essential.dump()).safe_browser == "essential"
    assert compile_settings(2, DeviceSettings.load('{"safe_browser": "off"}')).safe_browser == "off"
    assert DeviceSettings.load('{"safe_browser": "bogus"}').safe_browser == ""
    assert DeviceSettings.load("{}").update({"safe_browser": "bogus"}) == ["מצב הסייר המוגן לא מוכר"]


def test_safe_browser_reaches_the_phone_policy(env):
    svc, client = env
    h = admin_headers(client)
    did, token = active_customer(svc, client)
    assert "safe_browser" not in client.get("/v1/device", headers=auth(token)).json()["device_policy"]
    r = client.patch(f"/v1/admin/devices/{did}", headers=h, json={"safe_browser": "essential"})
    assert r.status_code == 200 and r.json()["settings"]["safe_browser"] == "essential"
    assert r.json()["compiled"]["safe_browser"] == "essential"
    assert client.get("/v1/device", headers=auth(token)).json()["device_policy"]["safe_browser"] == "essential"
    assert client.patch(f"/v1/admin/devices/{did}", headers=h, json={"safe_browser": "x"}).status_code == 400
    r = client.patch(f"/v1/admin/devices/{did}", headers=h, json={"safe_browser": ""})
    assert r.json()["settings"]["safe_browser"] == "" and r.json()["compiled"]["safe_browser"] == "full"
