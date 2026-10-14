"""אפליקציות מחוץ ל-VPN (FilterTube), חזרת חסימת YouTube, הפרדת אפליקציית גוגל מג'מיני,
והסרת החריגים הגלובליים ש"תיקון ה-DNS" מ-12/10 הוסיף."""
import importlib.util
import json
from pathlib import Path

import pytest
import yaml
from helpers import admin_headers, wg_key

from filterphone.api.app import device_limiter, enroll_limiter, heartbeat_limiter
from filterphone.api.catalog import (
    CATALOG,
    DPC_DEFAULTS,
    NEVER_BYPASS,
    SPLIT_APPS,
    VPN_BYPASS_APPS,
    DeviceSettings,
    compile_settings,
    policy_checksum,
    resolve_vpn_bypass,
)
from filterphone.api.service import sanitize_heartbeat
from filterphone.proxy.hosts import HostPolicy, load_lists

REPO = Path(__file__).resolve().parents[2]
CERT = "ab" * 32
GSA = "com.google.android.googlequicksearchbox"


@pytest.fixture(autouse=True)
def _limits():
    enroll_limiter._hits.clear()
    device_limiter._hits.clear()
    heartbeat_limiter._hits.clear()


def enroll(svc, client, n=1):
    code = svc.create_code("t", 2, 1, 1)["code"]
    r = client.post("/v1/enroll", json={"code": code, "public_key": wg_key(n), "name": f"טלפון {n}",
                                        "platform": "android", "app_version": "2.0.0"})
    assert r.status_code == 201, r.text
    j = r.json()
    return j["device_id"], {"Authorization": f"Bearer {j['token']}"}


# ------------------------------------------------------------------ FilterTube מחוץ ל-VPN


def test_both_filtertube_variants_bypass_the_vpn_by_default():
    eff = resolve_vpn_bypass({})
    assert eff["enabled"] is True
    assert [a["package"] for a in eff["apps"]] == ["com.filtertube.app", "com.filtertube.filtertube"]
    assert all(a["certs"] == [] and a["label"] for a in eff["apps"])
    assert "com.google.android.youtube" not in {a["package"] for a in eff["apps"]}   # יוטיוב הרשמי — בסינון


def test_admin_can_add_remove_pin_and_disable():
    s = DeviceSettings()
    assert s.update({"vpn_bypass": {"extra": ["org.example.safe"], "removed": ["com.filtertube.filtertube"],
                                    "pins": {"com.filtertube.app": [CERT.upper()]}}}) == []
    eff = resolve_vpn_bypass(s.vpn_bypass)
    assert {a["package"]: a["certs"] for a in eff["apps"]} == {"com.filtertube.app": [CERT], "org.example.safe": []}
    assert s.update({"vpn_bypass": {"pins": {"com.filtertube.app": []}}}) == []       # ביטול נעילה
    assert resolve_vpn_bypass(s.vpn_bypass)["apps"][0]["certs"] == []
    assert s.update({"vpn_bypass": {"enabled": False}}) == []
    assert resolve_vpn_bypass(s.vpn_bypass) == {"enabled": False, "apps": []}
    # נשמר ונטען בחזרה
    assert DeviceSettings.load(s.dump()).vpn_bypass == s.vpn_bypass


def test_browsers_and_stores_can_never_leave_the_filter():
    s = DeviceSettings()
    errors = s.update({"vpn_bypass": {"extra": ["com.android.chrome"]}})
    assert errors and "דפדפן" in errors[0]
    assert s.vpn_bypass == {}                                       # כלום לא נשמר
    assert s.update({"vpn_bypass": {"pins": {"com.filtertube.app": ["not-a-hash"]}}})
    assert {"com.android.chrome", "com.android.vending", GSA, "com.filterphone.app"} <= NEVER_BYPASS
    # גם אם הגיע איכשהו למסד — לא מוחרג
    hacked = DeviceSettings.load(json.dumps({"vpn_bypass": {"extra": ["com.android.chrome", "org.ok.app"]}}))
    assert [a["package"] for a in resolve_vpn_bypass(hacked.vpn_bypass)["apps"]] == [
        "com.filtertube.app", "com.filtertube.filtertube", "org.ok.app"]


def test_bypass_is_part_of_the_phone_policy_and_its_checksum():
    base = compile_settings(2, DeviceSettings())
    assert base.device_json()["vpn_bypass"]["apps"][0]["package"] in VPN_BYPASS_APPS
    off = DeviceSettings()
    off.update({"vpn_bypass": {"enabled": False}})
    assert policy_checksum(2, base) != policy_checksum(2, compile_settings(2, off))


def test_admin_patch_reaches_the_phone_with_a_new_policy_version(env):
    svc, client = env
    h = admin_headers(client)
    did, dh = enroll(svc, client)
    v0 = client.get("/v1/device", headers=dh).json()["device_policy"]
    assert [a["package"] for a in v0["vpn_bypass"]["apps"]] == sorted(VPN_BYPASS_APPS)
    r = client.patch(f"/v1/admin/devices/{did}", headers=h,
                     json={"vpn_bypass": {"pins": {"com.filtertube.app": [CERT]}}})
    assert r.status_code == 200, r.text
    v1 = client.get("/v1/device", headers=dh).json()["device_policy"]
    assert v1["version"] == v0["version"] + 1
    assert v1["vpn_bypass"]["apps"][0] == {"package": "com.filtertube.app", "label": "FilterTube", "certs": [CERT]}
    bad = client.patch(f"/v1/admin/devices/{did}", headers=h, json={"vpn_bypass": {"extra": ["org.mozilla.firefox"]}})
    assert bad.status_code == 400
    detail = client.get(f"/v1/admin/devices/{did}", headers=h).json()
    assert detail["settings"]["vpn_bypass_effective"]["enabled"] is True
    assert {d["package"] for d in detail["settings"]["vpn_bypass_defaults"]} == set(VPN_BYPASS_APPS)


def test_heartbeat_bypass_report_is_sanitized(env):
    hb = sanitize_heartbeat({"vpn_bypass": {
        "applied": ["com.filtertube.app", "bad name!", 7], "mismatch": ["com.filtertube.filtertube"],
        "certs": {"com.filtertube.app": "AB:" * 31 + "AB", "x": "zz", "org.ok.app": "short"},
        "lockdown_conflict": True, "extra": "ignored"}})
    assert hb["vpn_bypass"] == {"applied": ["com.filtertube.app"], "mismatch": ["com.filtertube.filtertube"],
                                "certs": {"com.filtertube.app": CERT}, "lockdown_conflict": True}
    assert "vpn_bypass" not in sanitize_heartbeat({"vpn_bypass": "x"})

    svc, client = env
    did, dh = enroll(svc, client)
    client.post("/v1/device/heartbeat", headers=dh, json={"policy_version": 1, "vpn_bypass": {
        "applied": ["com.filtertube.app"], "certs": {"com.filtertube.app": CERT}}})
    detail = client.get(f"/v1/admin/devices/{did}", headers=admin_headers(client)).json()
    assert detail["heartbeat"]["vpn_bypass"]["certs"] == {"com.filtertube.app": CERT}


def test_server_update_refreshes_only_devices_whose_policy_changed(env):
    svc, client = env
    a, _ = enroll(svc, client, 1)
    b, _ = enroll(svc, client, 2)
    with svc.db() as c:
        # מדמים מכשיר שהמדיניות שלו חושבה בגרסת שרת קודמת (טביעה אחרת)
        c.execute("UPDATE devices SET policy_checksum = 'old-server-checksum' WHERE id = ?", (a,))
        before = {r["id"]: r["policy_version"] for r in c.execute("SELECT id, policy_version FROM devices")}
    assert svc.refresh_stale_policies() == 1
    with svc.db() as c:
        after = {r["id"]: r["policy_version"] for r in c.execute("SELECT id, policy_version FROM devices")}
    assert after[a] == before[a] + 1 and after[b] == before[b]
    assert svc.refresh_stale_policies() == 0                       # פעם שנייה — כלום


# ------------------------------------------------------------------ YouTube חזר להיות כמו לפני 12/10


def test_youtube_can_be_blocked_per_device_again():
    yt = CATALOG["youtube"]
    assert "googlevideo.com" in yt.hosts and "com.google.android.youtube" in yt.apps
    comp = compile_settings(2, DeviceSettings(options=["youtube"]))
    assert "youtube.com" in comp.hosts and "com.google.android.youtube" in comp.apps


def test_youtube_thumbnails_go_through_image_filtering_again():
    hosts = HostPolicy(*load_lists(REPO / "server" / "filterphone" / "proxy" / "passthrough.txt"), 3600)
    for h in ("i.ytimg.com", "yt3.ggpht.com"):
        assert not hosts.is_static(h), h
        assert hosts.is_inspect_only(h), h
    for h in ("www.youtube.com", "youtu.be", "m.youtube.com"):
        assert not hosts.is_static(h), h
    assert hosts.is_static("rr3---sn-abc.googlevideo.com")        # וידאו גולמי — כמו קודם (בלי פתיחה)
    assert hosts.is_static("api.github.com")                       # החריג של GitHub נשאר


def test_adguard_patch_removes_hotfix_exceptions_and_restores_youtube_safesearch(tmp_path):
    spec = importlib.util.spec_from_file_location("adguard_patch", REPO / "deploy" / "adguard_patch.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    cfg = tmp_path / "AdGuardHome.yaml"
    cfg.write_text(yaml.safe_dump({
        "filtering": {"safe_search": {"enabled": True, "google": True, "youtube": False}},
        "user_rules": ["@@||youtube.com^$important", "@@||ytimg.com^$important",
                       "@@||googlevideo.com^$important", "@@||github.com^$important",
                       "||i.scdn.co^", "||my-own-rule.example^"],
    }), encoding="utf-8")
    mod.main(str(cfg))
    doc = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert doc["filtering"]["safe_search"]["youtube"] is True
    rules = doc["user_rules"]
    assert not any("youtube" in r or "ytimg" in r or "googlevideo" in r for r in rules)
    assert "@@||github.com^$important" in rules and "||i.scdn.co^" in rules and "||my-own-rule.example^" in rules
    mod.main(str(cfg))                                             # הרצה חוזרת — אותו דבר
    assert yaml.safe_load(cfg.read_text(encoding="utf-8"))["user_rules"] == rules


# ------------------------------------------------------------------ גוגל ≠ ג'מיני


def test_blocking_google_blocks_only_the_google_app_screens():
    for s in (DeviceSettings(options=["google_app"]),
              DeviceSettings.load(json.dumps({"app_rules": {GSA: {"mode": "accessibility"}}}))):
        comp = compile_settings(2, s)
        assert GSA not in comp.apps                                 # לא כאפליקציה שלמה, לא מוסתרת
        assert comp.split_apps == (GSA,)
        screens = {x["package"]: x["match"] for x in comp.screens}
        assert set(SPLIT_APPS[GSA]) <= set(screens[GSA])
        # ג'מיני / Circle to Search / Lens רצים מתוך אותה חבילה — אף תבנית לא תופסת אותם
        for gemini_window in ("com.google.android.apps.search.assistant.surfaces.voice.robin.ui.RobinActivity",
                              "com.google.android.apps.bard.shellapp.BardEntryPointActivity",
                              "com.google.android.apps.search.lens.LensActivity"):
            assert not any(p in gemini_window.lower() for p in screens[GSA]), gemini_window
        assert any(p in "com.google.android.apps.search.googleapp.activity.GoogleAppActivity".lower()
                   for p in screens[GSA])


def test_new_device_owner_defaults():
    assert DPC_DEFAULTS["suspend_blocked_apps"] is True
    assert DPC_DEFAULTS["disallow_tethering"] is True
    assert DPC_DEFAULTS["hide_blocked_apps"] is True               # גרסאות ישנות של האפליקציה ממשיכות להסתיר


def test_wireguard_keepalive_is_server_tunable_within_a_safe_range(env):
    """keepalive מהשרת (בלי גרסת אפליקציה חדשה), אבל רק בטווח שלא שובר push וספי "מחובר"."""
    import dataclasses

    from filterphone.api.service import keepalive_s
    svc, client = env
    assert keepalive_s(25) == 25 and keepalive_s(5) == 15 and keepalive_s(300) == 50
    svc.cfg = dataclasses.replace(svc.cfg, wg_keepalive_s=40)
    code = svc.create_code("x", 2, 1, 1)["code"]
    j = client.post("/v1/enroll", json={"code": code, "public_key": wg_key(30), "name": "x"}).json()
    assert j["vpn"]["persistent_keepalive"] == 40
