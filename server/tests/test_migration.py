"""הגירת מסד קיים (גרסה 0 — מה שרץ היום בשרת) לגרסה האחרונה, בלי לאבד אף מכשיר, קוד או מנהל."""
import json
import sqlite3

import fakeredis
from fastapi.testclient import TestClient
from helpers import CA_PEM, FakeWG, admin_headers, wg_key

from filterphone.api import db, security
from filterphone.api.app import create_app, enroll_limiter, login_limiter
from filterphone.api.service import Services
from filterphone.settings import Settings

T0 = 1_759_000_000
LEGACY_TOKEN = None
CODE_TOKEN = "device-token-from-code"

DEVICES = [
    # name, public_key, psk, vpn_ip, level, status, token, code_id, legacy, created_at, last_seen_at
    ("אבא — מהמערכת הישנה", wg_key(1), "psk-1", "10.8.0.2", 1, "active", LEGACY_TOKEN, None, 1, T0, T0 + 50),
    ("Pixel של משה", wg_key(2), "psk-2", "10.8.0.10", 2, "active", CODE_TOKEN, 1, 0, T0 + 100, T0 + 200),
    ("טלפון ישן", wg_key(3), "psk-3", "10.8.0.11", 3, "disabled", "old-token", 1, 0, T0 + 300, None),
]


def _v0_database(path: str) -> None:
    """בונה מסד בדיוק כמו בגרסה הקודמת: הסכמה המקורית, user_version=0, ונתונים אמיתיים."""
    c = sqlite3.connect(path)
    c.executescript(db.SCHEMA)
    assert c.execute("PRAGMA user_version").fetchone()[0] == 0
    c.execute("INSERT INTO admins (id, username, password_hash, created_at) VALUES (1, 'admin', ?, ?)",
              (security.hash_password("correct horse battery"), T0))
    c.execute("INSERT INTO admin_sessions VALUES ('session-hash', 1, ?)", (T0 + 10**6,))
    c.execute("INSERT INTO codes (id, code_hash, hint, label, level, uses_left, expires_at, created_at)"
              " VALUES (1, ?, 'ABCD', 'משפחת כהן', 2, 1, NULL, ?)", (security.code_hash("ABCDE-FGHIJ"), T0))
    c.execute("INSERT INTO codes (id, code_hash, hint, label, level, uses_left, expires_at, created_at)"
              " VALUES (2, ?, 'ZZZZ', 'קוד שלא נוצל', 1, 2, ?, ?)", (security.code_hash("ZZZZZ-ZZZZZ"), 4_000_000_000, T0))
    for name, key, psk, ip, level, status, token, code_id, legacy, created, seen in DEVICES:
        c.execute("INSERT INTO devices (name, platform, app_version, public_key, preshared_key, vpn_ip, level,"
                  " status, token_hash, code_id, legacy, created_at, last_seen_at)"
                  " VALUES (?, 'android', '1.0.3', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  (name, key, psk, ip, level, status, security.token_hash(token) if token else None,
                   code_id, legacy, created, seen))
    c.commit()
    c.close()


def _services(tmp_path) -> Services:
    ca = tmp_path / "ca.pem"
    ca.write_text(CA_PEM)
    cfg = Settings(data_dir=str(tmp_path), mitm_ca_cert=str(ca))
    _v0_database(cfg.db_path)
    db.init(cfg.db_path)                     # מה שקורה כשה-API עולה בגרסה החדשה
    enroll_limiter._hits.clear()
    login_limiter._hits.clear()
    return Services(cfg, FakeWG(), fakeredis.FakeRedis(decode_responses=True))


def test_migration_keeps_every_device_code_and_admin(tmp_path):
    svc = _services(tmp_path)
    with db.closing(db.connect(svc.cfg.db_path)) as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == db.VERSION == 7
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []
        rows = c.execute("SELECT * FROM devices ORDER BY id").fetchall()
        assert len(rows) == len(DEVICES)
        for row, (name, key, psk, ip, level, status, token, code_id, legacy, created, seen) in zip(
                rows, DEVICES, strict=True):
            assert (row["name"], row["public_key"], row["preshared_key"], row["vpn_ip"], row["level"]) == \
                (name, key, psk, ip, level)
            assert (row["status"], row["code_id"], row["legacy"], row["created_at"], row["last_seen_at"]) == \
                (status, code_id, legacy, created, seen)
            assert row["token_hash"] == (security.token_hash(token) if token else None)
            assert row["approved_at"] == created          # מכשיר קיים = אושר כשנוצר
            assert row["policy"] == "{}" and row["customer_id"] is None and row["apps"] is None
            # גרסה 2: גרסת מדיניות התחלתית, בלי דיווח עדיין
            assert row["policy_version"] == 1 and row["device_policy_version"] is None
            assert row["heartbeat"] is None and row["managed_at"] is None
        assert c.execute("SELECT value FROM meta WHERE key = 'policy_revision'").fetchone()[0] == "0"
        assert c.execute("SELECT count(*) FROM device_log").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM codes").fetchone()[0] == 2
        assert c.execute("SELECT uses_left FROM codes WHERE id = 2").fetchone()[0] == 2
        assert c.execute("SELECT count(*) FROM admin_sessions").fetchone()[0] == 1
        for table in ("customers", "requests", "audit"):
            assert c.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0

    # הרצה חוזרת (אתחול נוסף של ה-API) לא משנה כלום
    db.init(svc.cfg.db_path)
    with db.closing(db.connect(svc.cfg.db_path)) as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == db.VERSION
        assert c.execute("SELECT count(*) FROM devices").fetchone()[0] == len(DEVICES)


def test_existing_phones_and_admin_keep_working_after_migration(tmp_path):
    svc = _services(tmp_path)
    with TestClient(create_app(svc)) as client:
        # הטלפון שנרשם בקוד ממשיך לעבוד עם האסימון הישן — כולל הגדרות ה-VPN שלו
        me = client.get("/v1/device", headers={"Authorization": f"Bearer {CODE_TOKEN}"})
        assert me.status_code == 200, me.text
        assert me.json()["status"] == "active" and me.json()["level"] == 2
        vpn = client.get("/v1/device/vpn", headers={"Authorization": f"Bearer {CODE_TOKEN}"}).json()
        assert vpn["address"] == "10.8.0.10/32" and vpn["preshared_key"] == "psk-2"

        # המנהל נכנס עם אותה סיסמה ורואה את כל המכשירים
        h = admin_headers(client)
        devices = client.get("/v1/admin/devices", headers=h).json()
        assert {d["vpn_ip"] for d in devices} == {"10.8.0.2", "10.8.0.10", "10.8.0.11"}

        # רק המכשירים הפעילים מחוברים ל-VPN; הכתובות לא משתנות
        svc.sync_wireguard()
        assert {k: ip for k, (_, ip) in svc.wg.peers_.items()} == {wg_key(1): "10.8.0.2", wg_key(2): "10.8.0.10"}

        # קוד ישן שלא נוצל עדיין עובד, ומקבל כתובת שלא תפוסה
        enr = client.post("/v1/enroll", json={"code": "ZZZZZ-ZZZZZ", "public_key": wg_key(9), "name": "חדש",
                                              "platform": "android", "app_version": "1.0.3"})
        assert enr.status_code in (200, 201), enr.text
        assert enr.json()["vpn"]["address"] not in ("10.8.0.2/32", "10.8.0.10/32", "10.8.0.11/32")

        # ומדיניות חדשה (חסימה בלחיצה) עובדת גם על מכשיר ישן
        legacy_id = next(d["id"] for d in devices if d["vpn_ip"] == "10.8.0.2")
        r = client.patch(f"/v1/admin/devices/{legacy_id}", headers=h, json={"options": ["tiktok"]})
        assert r.status_code == 200, r.text
        detail = client.get(f"/v1/admin/devices/{legacy_id}", headers=h).json()
        assert detail["settings"]["options"] == ["tiktok"]
        pol = json.loads(svc.redis.hget("fp:policy", "10.8.0.2"))
        assert "tiktok.com" in pol["hosts"]
