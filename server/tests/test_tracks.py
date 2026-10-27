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


def test_allow_google_accounts_toggle_opens_passthrough_persistently(env):
    from filterphone.api.service import PASSTHROUGH_KEY, Services
    svc, client = env
    h = admin_headers(client)
    did, token = active_customer(svc, client)
    ip = svc.device_row(did)["vpn_ip"]
    d = client.get(f"/v1/admin/devices/{did}", headers=h).json()
    assert d["settings"]["allow_google_accounts"] is False
    assert not [k for k in svc.redis.hkeys(PASSTHROUGH_KEY) if k.startswith(f"{ip}|")]
    # הדלקה — שרתי מסך החשבון נפתחים ב-passthrough למכשיר הזה
    r = client.patch(f"/v1/admin/devices/{did}", headers=h, json={"allow_google_accounts": True})
    assert r.status_code == 200 and r.json()["settings"]["allow_google_accounts"] is True
    hosts = {k.split("|", 1)[1] for k in svc.redis.hkeys(PASSTHROUGH_KEY) if k.startswith(f"{ip}|")}
    assert set(Services.ACCOUNT_SETUP_HOSTS) <= hosts
    # מתחדש גם בדיווח תקופתי (TTL ארוך)
    client.post("/v1/device/heartbeat", headers={"Authorization": f"Bearer {token}"}, json={"policy_version": 1})
    assert {k.split("|", 1)[1] for k in svc.redis.hkeys(PASSTHROUGH_KEY) if k.startswith(f"{ip}|")} >= set(Services.ACCOUNT_SETUP_HOSTS)
    # נשמר בגיבוי/טעינה
    from filterphone.api.catalog import DeviceSettings
    assert DeviceSettings.load(DeviceSettings.load(svc.device_row(did)["policy"]).dump()).allow_google_accounts is True
    assert client.patch(f"/v1/admin/devices/{did}", headers=h, json={"allow_google_accounts": False}).json()["settings"]["allow_google_accounts"] is False
