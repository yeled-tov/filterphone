import os

from helpers import CA_PEM, admin_headers, wg_key

from filterphone.api.cli import parse_wg_conf


def test_full_enrollment_flow(env):
    svc, client = env
    code = svc.create_code("משפחת כהן", level=2, uses=1, expires_days=30)["code"]
    r = client.post("/v1/enroll", json={"code": code.lower(), "public_key": wg_key(1), "name": "גלקסי"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["level"] == 2
    assert body["vpn"]["address"] == "10.8.0.10/32"
    assert body["vpn"]["dns"] == ["10.8.0.1"]
    assert "::/0" in body["vpn"]["allowed_ips"]
    assert body["ca"]["pem"] == CA_PEM
    assert wg_key(1) in svc.wg.peers_
    assert svc.redis.hget("fp:levels", "10.8.0.10") == "2"

    # הקוד נוצל — אי אפשר שוב
    r2 = client.post("/v1/enroll", json={"code": code, "public_key": wg_key(2)})
    assert r2.status_code == 403

    # המכשיר מזדהה עם הטוקן
    me = client.get("/v1/device", headers={"Authorization": f"Bearer {body['token']}"})
    assert me.status_code == 200 and me.json()["level_name"] == "בינוני"
    assert me.json()["via_vpn"] is False        # לקוח הבדיקה לא מגיע מכתובת ה-VPN




def test_enroll_rejects_bad_key_and_duplicates(env):
    svc, client = env
    code = svc.create_code("", level=1, uses=5, expires_days=None)["code"]
    bad = client.post("/v1/enroll", json={"code": code, "public_key": "x" * 44})
    assert bad.status_code == 400
    assert client.post("/v1/enroll", json={"code": code, "public_key": wg_key(3)}).status_code == 201
    assert client.post("/v1/enroll", json={"code": code, "public_key": wg_key(3)}).status_code == 409


def test_ip_allocation_skips_used(env):
    svc, client = env
    code = svc.create_code("", level=1, uses=3, expires_days=None)["code"]
    ips = [client.post("/v1/enroll", json={"code": code, "public_key": wg_key(10 + i)}).json()["vpn"]["address"]
           for i in range(3)]
    assert ips == ["10.8.0.10/32", "10.8.0.11/32", "10.8.0.12/32"]


def test_admin_requires_login(env):
    _, client = env
    assert client.get("/v1/admin/devices").status_code == 401
    assert client.get("/v1/admin/devices", headers={"Authorization": "Bearer nope"}).status_code == 401
    r = client.post("/v1/admin/login", json={"username": "admin", "password": "wrong"})
    assert r.status_code == 401


def test_admin_changes_level_and_disables_device(env):
    svc, client = env
    h = admin_headers(client)
    code = client.post("/v1/admin/codes", json={"label": "בדיקה", "level": 1}, headers=h).json()["code"]
    token = client.post("/v1/enroll", json={"code": code, "public_key": wg_key(5)}).json()["token"]
    dev = client.get("/v1/admin/devices", headers=h).json()[0]

    assert client.patch(f"/v1/admin/devices/{dev['id']}", json={"level": 3}, headers=h).status_code == 200
    assert svc.redis.hget("fp:levels", dev["vpn_ip"]) == "3"

    assert client.patch(f"/v1/admin/devices/{dev['id']}", json={"status": "disabled"}, headers=h).status_code == 200
    assert wg_key(5) not in svc.wg.peers_
    assert svc.redis.hget("fp:levels", dev["vpn_ip"]) is None
    assert client.get("/v1/device/vpn", headers={"Authorization": f"Bearer {token}"}).status_code == 403

    assert client.patch(f"/v1/admin/devices/{dev['id']}", json={"level": 9}, headers=h).status_code == 400
    assert client.delete(f"/v1/admin/devices/{dev['id']}", headers=h).status_code == 204
    assert client.get("/v1/admin/devices", headers=h).json() == []


def test_enroll_rate_limit(env):
    _, client = env
    codes = [client.post("/v1/enroll", json={"code": "WRONG-CODE1", "public_key": wg_key(7)}).status_code
             for _ in range(11)]
    assert codes[:10] == [403] * 10 and codes[10] == 429


def test_sync_all_restores_wireguard_and_redis(env):
    svc, client = env
    code = svc.create_code("", level=2, uses=2, expires_days=None)["code"]
    client.post("/v1/enroll", json={"code": code, "public_key": wg_key(8)})
    svc.wg.peers_.clear()
    svc.redis.delete("fp:levels")
    svc.sync_all()
    assert wg_key(8) in svc.wg.peers_
    assert svc.redis.hget("fp:levels", "10.8.0.10") == "2"


def test_ca_download(env):
    _, client = env
    r = client.get("/v1/ca.crt")
    assert r.status_code == 200 and "BEGIN CERTIFICATE" in r.text
    assert "FilterPhone-CA.crt" in r.headers["content-disposition"]


def test_admin_page_served_with_csp(env):
    _, client = env
    r = client.get("/admin")
    assert r.status_code == 200 and "script-src 'self'" in r.headers["content-security-policy"]


def test_parse_legacy_wg_conf():
    conf = f"""[Interface]
Address = 10.8.0.1/24
PrivateKey = SECRET

# לקוח: ישראל ישראלי (ID: 2) | IP: 10.8.0.6 | נוצר: 2026-04-06
[Peer]
PublicKey = {wg_key(42)}
PresharedKey = {wg_key(43)}
AllowedIPs = 10.8.0.6/32

# לקוח ID: 3 | IP: 10.8.0.3 | שוחזר
[Peer]
PublicKey = {wg_key(44)}
AllowedIPs = 10.8.0.3/32
"""
    peers = parse_wg_conf(conf)
    assert [(p["name"], p["ip"]) for p in peers] == [("ישראל ישראלי", "10.8.0.6"), ("לקוח 3", "10.8.0.3")]
    assert peers[0]["psk"] == wg_key(43) and peers[1]["psk"] == ""
    assert "SECRET" not in str(peers)


def test_no_secrets_leak_in_device_list(env):
    svc, client = env
    h = admin_headers(client)
    code = svc.create_code("", level=1, uses=1, expires_days=None)["code"]
    client.post("/v1/enroll", json={"code": code, "public_key": wg_key(9)})
    listing = client.get("/v1/admin/devices", headers=h).text
    assert "preshared" not in listing and "token" not in listing
    assert os.stat(svc.cfg.db_path).st_mode & 0o077 == 0 or os.name == "nt"
