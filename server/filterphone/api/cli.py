"""כלי שורת פקודה לניהול השרת.

    python -m filterphone.api.cli set-admin <שם>             (הסיסמה נקראת מ-stdin)
    python -m filterphone.api.cli create-code --label "משפחת כהן" --level 1
    python -m filterphone.api.cli import-legacy - < /etc/wireguard/wg0.conf
    python -m filterphone.api.cli sync
"""
from __future__ import annotations

import argparse
import getpass
import re
import sys

from ..proxy.policy import _LEGACY_PATTERN
from . import db
from .app import build_services
from .security import is_wg_key


def _read_password() -> str:
    if sys.stdin.isatty():
        p1 = getpass.getpass("סיסמה: ")
        if p1 != getpass.getpass("שוב: "):
            raise SystemExit("הסיסמאות לא תואמות")
        return p1
    return sys.stdin.readline().rstrip("\n")


def parse_wg_conf(text: str) -> list[dict]:
    """מחלץ [Peer] מקובץ wg-quick, כולל שם מתוך שורת ההערה שלפניו (בפורמט של המערכת הישנה)."""
    peers, current, comment = [], None, ""
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            comment = line
            continue
        if line == "[Peer]":
            current = {"comment": comment}
            peers.append(current)
            comment = ""
            continue
        if line.startswith("["):
            current = None
            continue
        if current is not None and "=" in line:
            k, v = (x.strip() for x in line.split("=", 1))
            current[k.lower()] = v
    out = []
    for p in peers:
        ip = p.get("allowedips", "").split("/")[0]
        named = re.search(r"לקוח:\s*(.+?)\s*\(ID", p["comment"])
        numbered = re.search(r"ID:\s*(\d+)", p["comment"])
        name = named.group(1) if named else f"לקוח {numbered.group(1) if numbered else ip}"
        if is_wg_key(p.get("publickey", "")) and ip:
            out.append({"public_key": p["publickey"], "psk": p.get("presharedkey", ""), "ip": ip, "name": name})
    return out


def main() -> None:
    ap = argparse.ArgumentParser(prog="filterphone-admin")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("set-admin")
    a.add_argument("username")
    c = sub.add_parser("create-code")
    c.add_argument("--label", default="")
    c.add_argument("--level", type=int, default=1)
    c.add_argument("--uses", type=int, default=1)
    c.add_argument("--days", type=int, default=30)
    i = sub.add_parser("import-legacy")
    i.add_argument("conf", help="קובץ wg0.conf, או - לקריאה מ-stdin")
    sub.add_parser("sync")
    args = ap.parse_args()

    svc = build_services()
    if args.cmd == "set-admin":
        svc.set_admin(args.username, _read_password())
        print(f"מנהל '{args.username}' עודכן")
    elif args.cmd == "create-code":
        r = svc.create_code(args.label, args.level, args.uses, args.days)
        print(f"קוד הפעלה: {r['code']}  (רמה {r['level']}, {r['uses_left']} שימושים)")
    elif args.cmd == "import-legacy":
        text = sys.stdin.read() if args.conf == "-" else open(args.conf, encoding="utf-8").read()
        legacy_levels = {}
        for key in svc.redis.scan_iter(match=_LEGACY_PATTERN):
            val = svc.redis.get(key)
            if val and val.isdigit():
                legacy_levels[key.split(":")[2]] = int(val)
        n = 0
        with db.closing(svc.conn()) as conn, db.transaction(conn):
            for p in parse_wg_conf(text):
                if conn.execute("SELECT 1 FROM devices WHERE public_key = ? OR vpn_ip = ?",
                                (p["public_key"], p["ip"])).fetchone():
                    continue
                conn.execute(
                    "INSERT INTO devices (name, label, platform, public_key, preshared_key, vpn_ip, level,"
                    " legacy, created_at) VALUES (?,?,?,?,?,?,?,1,?)",
                    (p["name"], "מהמערכת הישנה", "", p["public_key"], p["psk"], p["ip"],
                     legacy_levels.get(p["ip"], svc.cfg.default_level), db.now()))
                n += 1
        print(f"יובאו {n} מכשירים ישנים")
        svc.sync_all()
    elif args.cmd == "sync":
        svc.sync_all()
        print("סונכרן")


if __name__ == "__main__":
    main()
