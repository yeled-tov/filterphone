#!/usr/bin/env python3
"""בדיקת קובצי ה-APK שנבנו + FilterPhone.json (הגרסה שמתפרסמת בדף ההורדה).

    python3 tools/apk_release.py --aapt2 <path/to/aapt2> dist/

לכל APK בתיקייה (FilterPhone-<גרסה>-arm64.apk / -universal.apk) נבדק, מתוך ה-APK עצמו:
  - שם החבילה, versionCode / versionName, minSdk ו-targetSdk;
  - שאינו debuggable, שאין גיבוי (allowBackup) ושאין תעבורה לא מוצפנת (cleartext);
  - ההרשאות שההגנה צריכה — ושאין הרשאות פרטיות (הודעות, אנשי קשר, מיקום, מיקרופון...);
  - הרכיבים: VPN, השירות הקדמי (specialUse), המגן (נגישות), מנהל המכשיר, הקמת Device Owner,
    המשימה התקופתית ותוצאת העדכון — כל אחד עם ההרשאה שמגינה עליו;
  - שכל רכיב שחשוף לאפליקציות אחרות (exported) מוגן בהרשאה, חוץ מהמסך הראשי ומקבל האתחול.
נכשל (exit 1) אם משהו לא תקין — בנייה כזו לא מתפרסמת.

FilterPhone.json: לכל קובץ — השם בדף ההורדה, versionCode, המעבדים, הגודל ו-SHA-256. השרת מציע
ממנו עדכון לטלפונים (במנהל מכשיר — עדכון שקט, אחרי בדיקת טביעה וחתימה בטלפון עצמו).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

PACKAGE = "com.filterphone.app"
MIN_SDK = 26
MIN_TARGET_SDK = 35
# השם בדף ההורדה לכל סוג בנייה (כמו ב-deploy.yml / publish_apk.sh)
PUBLISHED = {"arm64": "FilterPhone.apk", "universal": "FilterPhone-universal.apk"}

REQUIRED_PERMISSIONS = {
    "android.permission.INTERNET",
    "android.permission.ACCESS_NETWORK_STATE",
    "android.permission.RECEIVE_BOOT_COMPLETED",
    "android.permission.FOREGROUND_SERVICE",
    "android.permission.FOREGROUND_SERVICE_SPECIAL_USE",
    "android.permission.POST_NOTIFICATIONS",
    "android.permission.REQUEST_IGNORE_BATTERY_OPTIMIZATIONS",
}
# FilterPhone לא קוראת תוכן אישי — הרשאות כאלה לא אמורות להופיע אף פעם
FORBIDDEN_PERMISSIONS = {
    "android.permission.READ_SMS", "android.permission.RECEIVE_SMS", "android.permission.SEND_SMS",
    "android.permission.READ_CONTACTS", "android.permission.READ_CALL_LOG", "android.permission.READ_PHONE_NUMBERS",
    "android.permission.ACCESS_FINE_LOCATION", "android.permission.ACCESS_COARSE_LOCATION",
    "android.permission.ACCESS_BACKGROUND_LOCATION", "android.permission.RECORD_AUDIO", "android.permission.CAMERA",
    "android.permission.READ_EXTERNAL_STORAGE", "android.permission.READ_MEDIA_IMAGES",
    "android.permission.READ_MEDIA_VIDEO", "android.permission.MANAGE_EXTERNAL_STORAGE",
    "android.permission.GET_ACCOUNTS", "android.permission.SYSTEM_ALERT_WINDOW",
}
# (סוג, שם) → מאפיינים שחייבים להיות
REQUIRED_COMPONENTS: dict[tuple[str, str], dict[str, str]] = {
    ("activity", f"{PACKAGE}.MainActivity"): {},
    ("activity", f"{PACKAGE}.ProvisioningModeActivity"): {"permission": "android.permission.BIND_DEVICE_ADMIN"},
    ("activity", f"{PACKAGE}.PolicyComplianceActivity"): {"permission": "android.permission.BIND_DEVICE_ADMIN"},
    ("receiver", f"{PACKAGE}.DeviceAdmin"): {"permission": "android.permission.BIND_DEVICE_ADMIN"},
    ("receiver", f"{PACKAGE}.BootReceiver"): {},
    ("receiver", f"{PACKAGE}.UpdateReceiver"): {"exported": "false"},
    ("service", f"{PACKAGE}.ProtectionService"): {"foregroundServiceType": "0x40000000", "exported": "false"},
    ("service", f"{PACKAGE}.GuardService"): {"permission": "android.permission.BIND_ACCESSIBILITY_SERVICE"},
    ("service", f"{PACKAGE}.SyncJob"): {"permission": "android.permission.BIND_JOB_SERVICE"},
    ("service", "com.wireguard.android.backend.GoBackend$VpnService"):
        {"permission": "android.permission.BIND_VPN_SERVICE"},
}
# רכיבים שמותר להם להיות חשופים בלי הרשאה (מסנן פעולות בקוד)
EXPORTED_WITHOUT_PERMISSION = {f"{PACKAGE}.MainActivity", f"{PACKAGE}.BootReceiver"}

# "A: http://schemas.android.com/apk/res/android:name(0x01010003)="..." (Raw: "...")"
_ATTR = re.compile(r'^\s*A: (?:\S*:)?([A-Za-z]+)\(0x[0-9a-f]+\)=(.*)$')
_ELEM = re.compile(r"^(\s*)E: ([\w-]+)")


def run(aapt2: str, *args: str) -> str:
    return subprocess.run([aapt2, *args], check=True, capture_output=True, text=True).stdout


def attr_value(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith('"'):
        return raw[1:raw.index('"', 1)]
    return raw.split(" ", 1)[0]


def parse_manifest(xmltree: str) -> list[dict]:
    """כל אלמנט: {tag, depth, attrs}. מספיק כדי לבדוק רכיבים ומאפיינים."""
    elems: list[dict] = []
    for line in xmltree.splitlines():
        if m := _ELEM.match(line):
            elems.append({"tag": m.group(2), "depth": len(m.group(1)), "attrs": {}})
        elif (m := _ATTR.match(line)) and elems:
            elems[-1]["attrs"].setdefault(m.group(1), attr_value(m.group(2)))
    return elems


def check_apk(aapt2: str, apk: Path) -> tuple[dict, list[str]]:
    errors: list[str] = []
    badging = run(aapt2, "dump", "badging", str(apk))
    m = re.search(r"package: name='([^']+)' versionCode='(\d+)' versionName='([^']*)'", badging)
    if not m:
        return {}, ["לא נמצאה שורת package ב-badging"]
    name, code, version = m.group(1), int(m.group(2)), m.group(3)
    if name != PACKAGE:
        errors.append(f"שם חבילה {name} (צריך {PACKAGE})")
    min_sdk = int(re.search(r"minSdkVersion:'(\d+)'", badging).group(1))
    target = int(re.search(r"targetSdkVersion:'(\d+)'", badging).group(1))
    if min_sdk != MIN_SDK:
        errors.append(f"minSdk {min_sdk} (צריך {MIN_SDK})")
    if target < MIN_TARGET_SDK:
        errors.append(f"targetSdk {target} (צריך לפחות {MIN_TARGET_SDK})")
    if "application-debuggable" in badging:
        errors.append("הבנייה debuggable")
    perms = set(re.findall(r"uses-permission: name='([^']+)'", badging))
    if missing := REQUIRED_PERMISSIONS - perms:
        errors.append(f"חסרות הרשאות: {sorted(missing)}")
    if bad := FORBIDDEN_PERMISSIONS & perms:
        errors.append(f"הרשאות פרטיות שאסור שיהיו: {sorted(bad)}")
    abis = re.findall(r"'([^']+)'", (re.search(r"^native-code: (.*)$", badging, re.M) or [None, ""])[1])

    elems = parse_manifest(run(aapt2, "dump", "xmltree", "--file", "AndroidManifest.xml", str(apk)))
    app = next((e["attrs"] for e in elems if e["tag"] == "application"), {})
    if app.get("debuggable") not in (None, "false", "0x0"):
        errors.append("android:debuggable פעיל")
    if app.get("allowBackup") != "false":
        errors.append("android:allowBackup צריך להיות false (מפתח ה-VPN לא יוצא בגיבוי)")
    if app.get("usesCleartextTraffic") not in (None, "false"):
        errors.append("usesCleartextTraffic פעיל")
    components = {(e["tag"], e["attrs"].get("name", "")): e["attrs"] for e in elems
                  if e["tag"] in ("activity", "activity-alias", "receiver", "service", "provider")}
    for key, want in REQUIRED_COMPONENTS.items():
        got = components.get(key)
        if got is None:
            errors.append(f"חסר רכיב: {key[0]} {key[1]}")
            continue
        for a, v in want.items():
            if got.get(a) != v:
                errors.append(f"{key[1]}: {a}={got.get(a)} (צריך {v})")
    for (tag, cname), attrs in components.items():
        if attrs.get("exported") == "true" and not attrs.get("permission") and cname not in EXPORTED_WITHOUT_PERMISSION:
            errors.append(f"{tag} {cname} חשוף לאפליקציות אחרות בלי הרשאה")
    if "android.app.PROPERTY_SPECIAL_USE_FGS_SUBTYPE" not in {e["attrs"].get("name") for e in elems
                                                             if e["tag"] == "property"}:
        errors.append("חסר PROPERTY_SPECIAL_USE_FGS_SUBTYPE לשירות הקדמי")

    digest = hashlib.sha256(apk.read_bytes()).hexdigest()
    info = {"version_code": code, "version_name": version, "abis": abis, "size": apk.stat().st_size,
            "sha256": digest, "min_sdk": min_sdk, "target_sdk": target}
    return info, errors


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--aapt2", required=True)
    ap.add_argument("dist", type=Path)
    args = ap.parse_args()

    files, failed, names = [], False, set()
    for kind, published in PUBLISHED.items():
        found = sorted(args.dist.glob(f"FilterPhone-*-{kind}.apk"))
        if len(found) != 1:
            print(f"✗ צריך בדיוק קובץ אחד מסוג {kind} ב-{args.dist} (נמצאו {len(found)})")
            failed = True
            continue
        info, errors = check_apk(args.aapt2, found[0])
        for e in errors:
            print(f"✗ {found[0].name}: {e}")
        failed |= bool(errors)
        if info:
            names.add(info["version_name"])
            print(f"{'✗' if errors else '✓'} {found[0].name}: versionCode {info['version_code']} · targetSdk {info['target_sdk']} · "
                  f"{', '.join(info['abis']) or '—'} · {info['size'] / 1e6:.1f}MB")
            files.append({"name": published, "version_code": info["version_code"], "abis": info["abis"],
                          "size": info["size"], "sha256": info["sha256"]})
    if len(names) > 1:
        print(f"✗ גרסאות שונות בין הקבצים: {sorted(names)}")
        failed = True
    if failed:
        return 1
    out = args.dist / "FilterPhone.json"
    out.write_text(json.dumps({"version_name": names.pop(), "files": files}, indent=1) + "\n")
    print(f"✓ {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
