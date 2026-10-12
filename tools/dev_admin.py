"""ממשק הניהול על המחשב, עם נתוני דוגמה — לפיתוח ולהדגמה, בלי שרת ובלי WireGuard.

    pip install -e "server[dev]"
    python tools/dev_admin.py            # http://127.0.0.1:8099/admin   (admin / demo-password)

WireGuard מדומה, Redis בזיכרון (fakeredis), מסד נתונים בתיקייה זמנית.
"בדיקת תמונה" עובדת אם מנוע רץ מקומית: FP_RUN_DIR=<תיקייה עם engine.sock>.
"""
from __future__ import annotations

import base64
import json
import os
import random
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

import fakeredis  # noqa: E402
import uvicorn  # noqa: E402
from filterphone.api import db  # noqa: E402
from filterphone.api.app import create_app  # noqa: E402
from filterphone.api.service import Services  # noqa: E402
from filterphone.api.wg import PeerState  # noqa: E402
from filterphone.settings import Settings  # noqa: E402

CA = "-----BEGIN CERTIFICATE-----\n" + base64.b64encode(b"demo certificate").decode() + "\n-----END CERTIFICATE-----\n"


def key(n: int) -> str:
    return base64.b64encode(bytes([n]) * 32).decode()


class FakeWG:
    def __init__(self) -> None:
        self.peers_: dict[str, tuple[str, str]] = {}

    def public_key(self) -> str:
        return key(250)

    def peers(self) -> dict[str, PeerState]:
        t = int(time.time())
        return {k: PeerState(k, t - (30 if i % 3 else 4000), 5_000_000 * (i + 1), 900_000 * (i + 1))
                for i, k in enumerate(self.peers_)}

    def add_peer(self, k: str, psk: str, ip: str) -> None:
        self.peers_[k] = (psk, ip)

    def remove_peer(self, k: str) -> None:
        self.peers_.pop(k, None)

    def reconcile(self, desired: dict) -> tuple[int, int]:
        self.peers_ = dict(desired)
        return 0, 0


def seed(svc: Services) -> None:
    svc.set_admin("admin", "demo-password")
    people = [
        ("משה כהן", "050-1234567", "moshe@example.com", 1, ["whatsapp_profile", "whatsapp_status", "play_store",
                                                           "browsers_other", "apk_sites"], "גם לחסום את אתר X בבקשה"),
        ("שרה לוי", "052-7654321", "", 2, ["instagram", "tiktok", "facebook"], ""),
        ("יעקב פרידמן", "053-1112223", "yf@example.org", 2, ["whatsapp_media", "video_off"],
         "יש לי ילדים בבית — הכי מחמיר שאפשר בוואטסאפ"),
        ("אברהם ברגר", "054-9998887", "", 3, ["aliexpress", "temu", "shein"], ""),
        ("רחל גולדברג", "058-4445556", "rachel@example.co.il", 1, ["images_off"], "בלי תמונות בכלל"),
        ("דוד שטרן", "050-1234567", "", 1, ["whatsapp_profile"], "טלפון שני של משה"),
    ]
    tokens = []
    for i, (name, phone, email, level, opts, note) in enumerate(people):
        r = svc.register(name=name, phone=phone, email=email, level=level, options=opts, note=note,
                         public_key=key(i + 1), device_name=f"Galaxy A{30 + i * 5}", platform="android",
                         app_version="1.2.0")
        tokens.append(r)
    reqs = sorted(svc.list_requests(), key=lambda r: r["id"])
    for r in reqs[:4]:
        svc.approve_request(r["id"], "admin", {"payment": "paid", "paid_until": int(time.time()) + 86400 * random.choice([5, 40, 200])})
    devices = {d["name"]: d for d in svc.list_devices()}
    galaxy = next(d for d in devices.values() if d["status"] == "active")
    svc.report_device(svc.device_row(galaxy["id"]), [
        {"package": "com.whatsapp", "label": "WhatsApp"}, {"package": "com.android.chrome", "label": "Chrome", "system": True},
        {"package": "org.mozilla.firefox", "label": "Firefox"}, {"package": "com.android.vending", "label": "Google Play", "system": True},
        {"package": "com.waze", "label": "Waze"}, {"package": "com.google.android.youtube", "label": "YouTube", "system": True},
        {"package": "com.king.candycrushsaga", "label": "Candy Crush"}, {"package": "il.co.mot.rav_kav", "label": "רב-קו"},
    ], {"accessibility": True, "device_owner": False, "always_on": True, "lockdown": True, "ca_installed": True,
        "private_dns": "off"})
    svc.device_request_change(svc.device_row(galaxy["id"]), 2, ["whatsapp_profile", "play_store"],
                              "אפשר לעבור לרמה 2?")
    code = svc.create_code("משפחת וייס", 1, 3, 30)
    svc.enroll(code["code"], key(40), "הטלפון של אמא", "android", "1.1.0")
    # אירועים וסטטיסטיקה
    active = [d for d in svc.list_devices() if d["status"] == "active"]
    hosts = [("www.ynet.co.il", "image"), ("ae-pic-a1.aliexpress-media.com", "image"), ("www.google.com", "inline"),
             ("www.instagram.com", "host"), ("pps.whatsapp.net", "host"), ("www.walla.co.il", "image")]
    now = int(time.time())
    for n in range(80):
        d = random.choice(active)
        host, kind = random.choice(hosts)
        action = "site" if kind == "host" else random.choice(["block", "block", "replace"])
        reason = {"site": "site blocked", "block": f"female {random.uniform(.8, 1):.2f}",
                  "replace": f"skin={random.uniform(.1, .4):.2f} immodest={random.uniform(.3, .9):.2f}"}[action]
        svc.redis.xadd("fp:events", {"ip": d["vpn_ip"], "host": host, "action": action, "reason": reason, "kind": kind,
                                     "path": "/images/a.jpg" if kind != "host" else "/", "ts": now - (80 - n) * 300})
    for day_back in range(14):
        day = time.strftime("%Y%m%d", time.localtime(now - day_back * 86400))
        for d in active:
            for what, base in (("scanned", 900), ("block", 60), ("replace", 15), ("site", 25)):
                svc.redis.hset(f"fp:stats:{day}", f"{d['vpn_ip']}|{what}", int(base * random.uniform(.3, 1.4)))
    svc.redis.hset("fp:pt", f"{galaxy['vpn_ip']}|api.someapp.com", now + 86400)
    # דיווחים תקופתיים (heartbeat) — כדי לראות את מצבי התאימות בממשק
    good = {"app_version": "2.0.0", "sdk": 34, "ca_installed": True, "battery_unrestricted": True,
            "vpn": {"state": "UP", "always_on": True, "lockdown": True, "last_health_ok": now - 120},
            "accessibility": {"enabled": True, "running": True}, "private_dns": "off",
            "protections": {"apps_blocked": 12, "screens_blocked": 3, "wa_profile_photo": 2}}
    variants = [
        {**good, "device_owner": True, "dpc": {"applied": ["block_uninstall", "always_on_vpn", "disallow_safe_boot"]}},
        {**good, "accessibility": {"enabled": False}},                      # אדום: המגן כובה
        {**good, "battery_unrestricted": False},                            # צהוב: סוללה
        {**good, "vpn": {"state": "UP", "always_on": False}},               # צהוב: בלי תמיד-מופעל
    ]
    for d, hb in zip(active, variants, strict=False):
        row = svc.device_row(d["id"])
        svc.heartbeat(row, {**hb, "policy_version": row["policy_version"]}, d["vpn_ip"])
        svc.heartbeat(svc.device_row(d["id"]), {**hb, "policy_version": row["policy_version"]}, d["vpn_ip"])
    print(f"נתוני דוגמה: {len(svc.list_devices())} מכשירים, {len(svc.list_requests())} בקשות ממתינות")
    json.dumps(tokens)


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="fp-dev-"))
    (tmp / "ca.pem").write_text(CA)
    (tmp / "FilterPhone-universal.apk").write_bytes(b"PK\x03\x04 demo apk")      # ל-QR של Device Owner
    cfg = Settings(data_dir=str(tmp), mitm_ca_cert=str(tmp / "ca.pem"), apk_dir=str(tmp),
                   run_dir=os.environ.get("FP_RUN_DIR", str(tmp)))
    db.init(cfg.db_path)
    svc = Services(cfg, FakeWG(), fakeredis.FakeRedis(decode_responses=True))
    seed(svc)
    port = int(os.environ.get("PORT", "8099"))
    print(f"http://127.0.0.1:{port}/admin   (admin / demo-password)")
    uvicorn.run(create_app(svc), host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
