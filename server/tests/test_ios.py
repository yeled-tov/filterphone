"""אייפון בלי אפליקציה: קוד הפעלה בדפדפן → פרופיל התקנה (WireGuard + תעודת הסינון) → בדיקה חיה.
הקישור חד-פעמי לשעתיים, והמפתח הפרטי של המנהרה נמחק מהשרת כשהוא פג."""
import base64
import plistlib
import time
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from helpers import admin_headers, wg_key

from filterphone.api import compliance, db, iosprofile, security
from filterphone.api.service import PASSTHROUGH_KEY
from filterphone.proxy.hosts import HostPolicy, load_sections

PACKAGE_DIR = Path(iosprofile.__file__).resolve().parents[1]


def _code(client, h, **extra):
    r = client.post("/v1/admin/codes", headers=h, json={"label": "אייפון בדיקה", "level": 2, "uses": 1,
                                                        "expires_days": 7, **extra})
    assert r.status_code in (200, 201), r.text
    return r.json()["code"]


def _enroll(client, h, name="האייפון של משה"):
    r = client.post("/v1/ios", data={"code": _code(client, h), "name": name}, follow_redirects=False)
    assert r.status_code == 303, r.text
    path = r.headers["location"]
    assert path.startswith("/v1/ios/p/")
    return path, path.rsplit("/", 1)[1]


def _device(svc, platform="ios"):
    with svc.db() as c:
        return c.execute("SELECT * FROM devices WHERE platform = ? ORDER BY id DESC", (platform,)).fetchone()


def _public_of(private_b64: str) -> str:
    key = X25519PrivateKey.from_private_bytes(base64.b64decode(private_b64))
    return base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw,
                                                           serialization.PublicFormat.Raw)).decode()


def test_start_page_and_bad_code(env):
    _, client = env
    page = client.get("/v1/ios")
    assert page.status_code == 200 and "קוד הפעלה" in page.text and 'dir="rtl"' in page.text
    assert "Safari" in page.text
    bad = client.post("/v1/ios", data={"code": "ABCDE-FGHJK", "name": "x"}, follow_redirects=False)
    assert bad.status_code == 403 and "קוד ההפעלה לא תקין" in bad.text
    assert 'value="ABCDE-FGHJK"' in bad.text                       # מה שהוקלד נשאר בטופס
    assert client.post("/v1/ios", data={"code": "", "name": ""}, follow_redirects=False).status_code == 400


def test_code_gives_an_install_page_and_a_profile_with_the_ca_and_the_tunnel(env):
    svc, client = env
    h = admin_headers(client)
    path, token = _enroll(client, h)
    dev = _device(svc)
    assert dev["name"] == "האייפון של משה" and dev["status"] == "active" and dev["level"] == 2
    assert security.is_wg_key(dev["public_key"]) and dev["public_key"] in svc.wg.peers_

    page = client.get(path)
    assert page.status_code == 200 and "האייפון של משה" in page.text
    for step in ("WireGuard", "הורדת הפרופיל", "הגדרות אמון באישורים", "apps.apple.com"):
        assert step in page.text, step
    assert page.headers["cache-control"] == "no-store"

    r = client.get(f"{path}/FilterPhone.mobileconfig")
    assert r.status_code == 200 and r.headers["content-type"] == "application/x-apple-aspen-config"
    profile = plistlib.loads(r.content)
    assert profile["PayloadType"] == "Configuration" and profile["PayloadIdentifier"] == "com.filterphone.ios"
    assert "PayloadRemovalDisallowed" not in profile                  # במכשיר לא מנוהל אפל מתעלמת — לא מבטיחים
    ca, vpn = profile["PayloadContent"]
    assert ca["PayloadType"] == "com.apple.security.root" and ca["PayloadContent"] == b"fake certificate body"
    assert vpn["PayloadType"] == "com.apple.vpn.managed" and vpn["VPNType"] == "VPN"
    assert vpn["VPNSubType"] == "com.wireguard.ios" and vpn["UserDefinedName"] == "FilterPhone"
    assert vpn["VPN"]["OnDemandEnabled"] == 1 and vpn["VPN"]["OnDemandRules"] == [{"Action": "Connect"}]
    assert vpn["VPN"]["RemoteAddress"] == svc.cfg.vpn_endpoint
    conf = dict(line.split(" = ", 1) for line in vpn["VendorConfig"]["WgQuickConfig"].splitlines() if " = " in line)
    assert conf["Address"] == f"{dev['vpn_ip']}/32" and conf["DNS"] == svc.cfg.vpn_server_ip
    assert conf["PublicKey"] == wg_key(200) and conf["PresharedKey"] == dev["preshared_key"]
    assert conf["AllowedIPs"] == "0.0.0.0/0, ::/0" and conf["Endpoint"] == svc.cfg.vpn_endpoint
    assert _public_of(conf["PrivateKey"]) == dev["public_key"]       # המפתח שבפרופיל הוא של המכשיר שנרשם
    # הורדה חוזרת בזמן ההתקנה — אותו פרופיל (אותם מזהים: התקנה חוזרת מחליפה, לא מצטברת)
    assert client.get(f"{path}/FilterPhone.mobileconfig").content == r.content
    # הקוד נשרף
    again = client.post("/v1/ios", data={"code": "ZZZZZ-ZZZZZ", "name": ""}, follow_redirects=False)
    assert again.status_code == 403


def test_live_check_needs_the_phone_itself_and_cleans_what_was_learned_during_setup(env):
    svc, client = env
    h = admin_headers(client)
    path, token = _enroll(client, h)
    dev = _device(svc)
    st = client.get(f"{path}/status").json()
    assert st == {"connected": False, "via_vpn": False}               # הדף נפתח מחוץ למנהרה, ואין handshake
    assert client.post(f"{path}/ready").status_code == 403            # "סיימתי" — רק מהאייפון, דרך המנהרה
    assert svc.ios_via_vpn(token, dev["vpn_ip"]) and not svc.ios_via_vpn(token, "10.9.9.9")
    # handshake טרי בשרת = מחובר
    svc.wg.peers = lambda: {dev["public_key"]: type("P", (), {"latest_handshake": int(time.time()) - 5})()}
    assert client.get(f"{path}/status").json()["connected"] is True
    # אתרים שנכשלו לפני שהתעודה אושרה נלמדו כ"עקיפה" — בסיום ההתקנה הם נמחקים
    svc.redis.hset(PASSTHROUGH_KEY, f"{dev['vpn_ip']}|www.example.org", time.time() + 9999)
    svc.redis.hset(PASSTHROUGH_KEY, "10.9.9.9|other.example.org", time.time() + 9999)
    assert svc.ios_ready(token) == {"ok": True, "cleared": 1}
    assert list(svc.redis.hkeys(PASSTHROUGH_KEY)) == ["10.9.9.9|other.example.org"]
    assert any(e["kind"] == "ios_ready" for e in svc.device_log(dev["id"]))


def test_link_expires_and_the_private_key_is_deleted(env):
    svc, client = env
    h = admin_headers(client)
    path, token = _enroll(client, h)
    with svc.db() as c:
        assert c.execute("SELECT count(*) FROM ios_profiles").fetchone()[0] == 1
        c.execute("UPDATE ios_profiles SET expires_at = ?", (db.now() - 1,))
    gone = client.get(path)
    assert gone.status_code == 404 and "הקישור לא בתוקף" in gone.text
    assert client.get(f"{path}/FilterPhone.mobileconfig").status_code == 404
    assert client.get(f"{path}/status").status_code == 404
    with svc.db() as c:
        assert c.execute("SELECT count(*) FROM ios_profiles").fetchone()[0] == 0
    assert client.get("/v1/ios/p/not-a-real-token").status_code == 404


def test_admin_can_issue_a_new_link_with_a_new_key(env):
    svc, client = env
    h = admin_headers(client)
    path, token = _enroll(client, h)
    dev = _device(svc)
    r = client.post(f"/v1/admin/devices/{dev['id']}/ios-profile", headers=h)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["link"] == f"https://{svc.cfg.public_host}{out['path']}" and out["token"] != token
    assert client.get(path).status_code == 404                        # הקישור הישן בוטל
    fresh = _device(svc)
    assert fresh["public_key"] != dev["public_key"] and fresh["vpn_ip"] == dev["vpn_ip"]
    assert dev["public_key"] not in svc.wg.peers_ and fresh["public_key"] in svc.wg.peers_
    profile = plistlib.loads(client.get(f"{out['path']}/FilterPhone.mobileconfig").content)
    conf = profile["PayloadContent"][1]["VendorConfig"]["WgQuickConfig"]
    private = next(line.split(" = ", 1)[1] for line in conf.splitlines() if line.startswith("PrivateKey"))
    assert _public_of(private) == fresh["public_key"]
    assert client.post(f"/v1/admin/devices/{dev['id']}/ios-profile").status_code in (401, 403)   # רק מנהל
    # מכשיר אנדרואיד — אין לו פרופיל
    code = _code(client, h)
    a = client.post("/v1/enroll", json={"code": code, "public_key": wg_key(7), "name": "גלקסי", "platform": "android"})
    assert client.post(f"/v1/admin/devices/{a.json()['device_id']}/ios-profile", headers=h).status_code == 409


def test_iphone_compliance_is_never_green_and_shows_in_the_dashboard(env):
    svc, client = env
    h = admin_headers(client)
    _enroll(client, h)
    dev = _device(svc)
    now = db.now()
    off = compliance.evaluate(dev, {}, {}, 0, False, False, now, 1800, 172800, 600)
    assert off.state == "yellow" and not off.online and off.vpn == "unknown"
    assert [i["code"] for i in off.issues] == ["ios_profile", "ios_offline"]
    on = compliance.evaluate(dev, {}, {}, now - 10, False, False, now, 1800, 172800, 600)
    assert on.state == "yellow" and on.online and on.vpn == "up" and [i["code"] for i in on.issues] == ["ios_profile"]
    lost = compliance.evaluate(dev, {}, {}, now - 300000, False, False, now, 1800, 172800, 600)
    assert lost.state == "red" and lost.issues[-1]["code"] == "ios_lost"
    d = client.get(f"/v1/admin/devices/{dev['id']}", headers=h).json()
    assert d["platform"] == "ios" and d["compliance"]["issues"][0]["code"] == "ios_profile"


def test_locked_profile_for_a_supervised_phone_and_pure_helpers():
    private, public = iosprofile.keypair()
    assert security.is_wg_key(public) and _public_of(private) == public
    vpn = {"address": "10.8.0.9/32", "dns": ["10.8.0.1"], "mtu": 1280, "server_public_key": wg_key(200),
           "preshared_key": "", "endpoint": "vpn.example.com:51820", "allowed_ips": ["0.0.0.0/0", "::/0"],
           "persistent_keepalive": 25}
    assert "PresharedKey" not in iosprofile.wg_quick(private, vpn)
    pem = "-----BEGIN CERTIFICATE-----\n" + base64.b64encode(b"x" * 40).decode() + "\n-----END CERTIFICATE-----\n"
    locked = plistlib.loads(iosprofile.build(device_id=3, ca_pem=pem, vpn=vpn, private_key=private, locked=True))
    assert locked["PayloadRemovalDisallowed"] is True
    assert locked["PayloadContent"][1]["VPN"]["OnDemandUserOverrideDisabled"] == 1
    other = plistlib.loads(iosprofile.build(device_id=4, ca_pem=pem, vpn=vpn, private_key=private))
    assert other["PayloadUUID"] != locked["PayloadUUID"]


def test_the_trust_probe_host_is_always_inspected_and_never_learned():
    """דף ההתקנה בודק שהתעודה מאושרת בפנייה ל-PROBE_URL דרך הפרוקסי. אם הפרוקסי היה "לומד לעקוף" אותו אחרי
    הכישלון הראשון — הבדיקה הייתה מצליחה גם בלי תעודה."""
    from filterphone.api import iospages
    host = iospages.PROBE_URL.split("/")[2]
    s = load_sections(PACKAGE_DIR / "proxy" / "passthrough.txt")
    hp = HostPolicy(s["passthrough"], s["inspect"], 3600, strict=s["strict"])
    assert hp.is_strict(host)
    assert hp.learn("10.8.0.9", host, strict_images=False) is False
    assert hp.should_passthrough("10.8.0.9", host) is False
