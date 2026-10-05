"""עזרים משותפים לבדיקות ה-API."""
import base64

import fakeredis

from filterphone.api import db
from filterphone.api.app import enroll_limiter, login_limiter
from filterphone.api.service import Services
from filterphone.api.wg import PeerState
from filterphone.settings import Settings

CA_PEM = ("-----BEGIN CERTIFICATE-----\n" + base64.b64encode(b"fake certificate body").decode()
          + "\n-----END CERTIFICATE-----\n")


def wg_key(n: int) -> str:
    return base64.b64encode(bytes([n]) * 32).decode()


class FakeWG:
    def __init__(self):
        self.peers_ = {}

    def public_key(self):
        return wg_key(200)

    def peers(self):
        return {k: PeerState(k, 0, 0, 0) for k in self.peers_}

    def add_peer(self, key, psk, ip):
        self.peers_[key] = (psk, ip)

    def remove_peer(self, key):
        self.peers_.pop(key, None)

    def reconcile(self, desired):
        self.peers_ = dict(desired)
        return len(desired), 0


def make_env(tmp_path):
    ca = tmp_path / "ca.pem"
    ca.write_text(CA_PEM)
    cfg = Settings(data_dir=str(tmp_path), mitm_ca_cert=str(ca))
    db.init(cfg.db_path)
    svc = Services(cfg, FakeWG(), fakeredis.FakeRedis(decode_responses=True))
    svc.set_admin("admin", "correct horse battery")
    enroll_limiter._hits.clear()
    login_limiter._hits.clear()
    return svc


def admin_headers(client):
    r = client.post("/v1/admin/login", json={"username": "admin", "password": "correct horse battery"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}
