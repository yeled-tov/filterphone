"""מחשבי Windows (FilterPhone למחשב): דיווח מצב ההגנה, מצב התאימות, הגבלות מהדשבורד וקודי הסרה."""
import json

import pytest
from helpers import admin_headers, wg_key

from filterphone.api import compliance as C
from filterphone.api import removal
from filterphone.api.app import device_limiter, enroll_limiter, heartbeat_limiter


@pytest.fixture(autouse=True)
def _limits():
    enroll_limiter._hits.clear()
    device_limiter._hits.clear()
    heartbeat_limiter._hits.clear()


def enroll_pc(svc, client, n=1):
    code = svc.create_code("pc", 2, 1, 1)["code"]
    r = client.post("/v1/enroll", json={"code": code, "public_key": wg_key(n), "name": f"מחשב {n}",
                                        "platform": "windows", "app_version": "0.9.0"})
    assert r.status_code == 201, r.text
    j = r.json()
    return j["device_id"], {"Authorization": f"Bearer {j['token']}"}


def row(svc, device_id):
    with svc.db() as c:
        return c.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()


ALL_OK = {layer: "ok" for layer in C.WINDOWS_LAYERS}


def report(**layers):
    return {"app_version": "0.9.0", "vpn": {"state": "UP"},
            "windows": {"overall": "Protected", "edition": "Professional", "build": "22631", **ALL_OK, **layers}}


# ------------------------------------------------------------------ דיווח

def test_heartbeat_keeps_the_windows_layers_and_drops_unknown_fields(env):
    svc, client = env
    did, dh = enroll_pc(svc, client)
    body = report(chrome_only="unsupported")
    body["windows"]["secret"] = "x"
    body["windows"]["tunnel"] = 5               # סוג לא נכון — נזרק
    client.post("/v1/device/heartbeat", headers=dh, json=body)
    win = json.loads(row(svc, did)["heartbeat"])["windows"]
    assert win["chrome_only"] == "unsupported" and win["edition"] == "Professional"
    assert "secret" not in win and "tunnel" not in win


def test_heartbeat_answers_a_pc_with_its_controls_not_phone_data(env):
    svc, client = env
    did, dh = enroll_pc(svc, client)
    r = client.post("/v1/device/heartbeat", headers=dh, json=report()).json()
    assert r["windows"] == {"chrome_only": True, "block_store": True}
    assert "managed_apps" not in r and "removal_key" not in r["windows"]


def test_dashboard_controls_reach_the_pc_and_need_valid_values(env):
    svc, client = env
    h = admin_headers(client)
    did, dh = enroll_pc(svc, client)
    assert client.patch(f"/v1/admin/devices/{did}", headers=h, json={"desktop": {"block_store": False}}).status_code == 200
    r = client.post("/v1/device/heartbeat", headers=dh, json=report()).json()
    assert r["windows"] == {"chrome_only": True, "block_store": False}
    detail = client.get(f"/v1/admin/devices/{did}", headers=h).json()
    assert detail["settings"]["desktop_effective"] == {"chrome_only": True, "block_store": False}
    bad = client.patch(f"/v1/admin/devices/{did}", headers=h, json={"desktop": {"block_store": "no"}})
    assert bad.status_code == 400
    bad = client.patch(f"/v1/admin/devices/{did}", headers=h, json={"desktop": {"disable_defender": True}})
    assert bad.status_code == 400


# ------------------------------------------------------------------ מצב תאימות

def fake_pc(**kw):
    base = {"status": "active", "platform": "windows", "heartbeat_at": None, "last_seen_at": None,
            "managed_at": None, "policy_version": 1, "device_policy_version": None, "policy_updated_at": 0}
    return {**base, **kw}


def ev(row_, hb, hs=0, now=1_000_000):
    return C.evaluate(row_, hb, {}, hs, False, False, now, 1800, 172800, 1500)


def codes(r):
    return [i["code"] for i in r.issues]


def test_windows_compliance_uses_the_pc_report_not_phone_checks():
    now = 1_000_000
    fresh = fake_pc(heartbeat_at=now - 60)
    ok = {"via_vpn": True, **report()}
    r = ev(fresh, ok, hs=now - 30)
    assert r.state == C.GREEN and r.vpn == "up" and r.management == "not_applicable" and not r.issues
    # Home: חסימה מראש לא אפשרית — צהוב (חלקי), לא ירוק ולא אדום
    r = ev(fresh, {"via_vpn": True, **report(chrome_only="unsupported", store_block="unsupported")}, hs=now - 30)
    assert r.state == C.YELLOW and codes(r) == ["win_limited"]
    # חסימת הגלישה מחוץ לסינון כבויה / בדיקת הסינון נכשלה / הגבלת הדפדפנים לא פועלת — אדום
    for layer, code in (("kill_switch", "win_lock_off"), ("image_path", "win_filter_failed"),
                        ("chrome_only", "win_browsers_open"), ("ca_trust", "ca_missing")):
        r = ev(fresh, {"via_vpn": True, **report(**{layer: "failed"})}, hs=now - 30)
        assert r.state == C.RED and code in codes(r), layer
    # דיווח שהגיע מחוץ למנהרה — אדום, גם אם המחשב אומר שהכול תקין
    r = ev(fresh, {"via_vpn": False, **report()})
    assert r.state == C.RED and r.vpn == "down" and "win_outside_vpn" in codes(r)
    # המנהרה למטה והחסימה פעילה: הדיווח עבר בחריג של השירות — צהוב (אין גלישה), לא אדום
    r = ev(fresh, {"via_vpn": False, **report(tunnel="failed")})
    assert r.state == C.YELLOW and "win_tunnel_down" in codes(r) and "win_outside_vpn" not in codes(r)
    r = ev(fresh, {"via_vpn": False, **report(tunnel="failed", kill_switch="failed")})
    assert r.state == C.RED and "win_outside_vpn" in codes(r)
    # החנות לא חסומה — צהוב
    assert ev(fresh, {"via_vpn": True, **report(store_block="failed")}, hs=now - 30).state == C.YELLOW
    # בלי דיווח מהמחשב — לא ידוע (צהוב), לא ירוק
    r = ev(fake_pc(last_seen_at=now - 10), {}, hs=now - 10)
    assert r.state == C.YELLOW and "win_no_report" in codes(r)
    # בלי קשר יומיים — אדום
    assert ev(fake_pc(heartbeat_at=now - 200_000), ok).state == C.RED


# ------------------------------------------------------------------ קודי הסרה

def test_removal_code_vector_matches_the_desktop_implementation():
    # אותו וקטור בדיוק נבדק גם במחשב (RemovalCodeTests.cs)
    key = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="
    assert removal.code_for(key, 42, 20_000) == "EEHMJ-S2M43"
    assert removal.normalize("eehmj s2m43") == "EEHMJ-S2M43"
    assert removal.verify(key, 42, "EEHMJ-S2M43", 20_000 * 86400 + 100)
    assert removal.verify(key, 42, "EEHMJ-S2M43", 20_001 * 86400 + 100)          # מחר
    assert not removal.verify(key, 42, "EEHMJ-S2M43", 20_002 * 86400 + 100)      # פג תוקף
    assert not removal.verify(key, 43, "EEHMJ-S2M43", 20_000 * 86400 + 100)      # מחשב אחר
    assert not removal.verify("", 42, "EEHMJ-S2M43", 20_000 * 86400)


def test_removal_code_flow(env):
    svc, client = env
    h = admin_headers(client)
    did, dh = enroll_pc(svc, client)
    # לפני שהמחשב ביקש מפתח — אין קוד
    assert client.post(f"/v1/admin/devices/{did}/removal-code", headers=h).status_code == 409
    assert not client.get(f"/v1/admin/devices/{did}", headers=h).json()["removal_ready"]
    r = client.post("/v1/device/heartbeat", headers=dh, json={**report(), "want_removal_key": True}).json()
    key = r["windows"]["removal_key"]
    again = client.post("/v1/device/heartbeat", headers=dh, json={**report(), "want_removal_key": True}).json()
    assert again["windows"]["removal_key"] == key                                 # אותו מפתח
    detail = client.get(f"/v1/admin/devices/{did}", headers=h).json()
    assert detail["removal_ready"] and key not in json.dumps(detail)    # המפתח עצמו לא נחשף לדשבורד
    issued = client.post(f"/v1/admin/devices/{did}/removal-code", headers=h).json()
    assert removal.verify(key, did, issued["code"], issued["valid_until"] - 86400 - 1)
    kinds = [e["kind"] for e in detail["device_log"]] + [e["kind"] for e in svc.device_log(did)]
    assert "removal_code" in kinds


def test_removal_code_only_for_windows(env):
    svc, client = env
    h = admin_headers(client)
    code = svc.create_code("t", 2, 1, 1)["code"]
    j = client.post("/v1/enroll", json={"code": code, "public_key": wg_key(9), "name": "טלפון",
                                        "platform": "android", "app_version": "2.0.0"}).json()
    assert client.post(f"/v1/admin/devices/{j['device_id']}/removal-code", headers=h).status_code == 409
