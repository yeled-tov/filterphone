"""אייפון: פרופיל התקנה (‎.mobileconfig‎) — תעודת הסינון + מנהרת WireGuard עם חיבור אוטומטי.

כך בודקים את הסינון על אייפון בלי חשבון מפתח של אפל ובלי אפליקציה משלנו: האפליקציה הרשמית של WireGuard
(חינם ב-App Store) מריצה את המנהרה, והפרופיל מגדיר אותה ומתקין את תעודת הסינון. אותם רכיבים (payloads) ישמשו
גם בשלב המנוהל (מכשיר ב-Supervision / MDM) — שם הפרופיל לא ניתן להסרה והחיבור לא ניתן לכיבוי. ראו docs/IOS.md.

מבנה הפרופיל: https://git.zx2c4.com/wireguard-apple/plain/MOBILECONFIG.md (VPNSubType = מזהה האפליקציה של
WireGuard, VendorConfig.WgQuickConfig = ההגדרות בפורמט wg-quick) + מפתחות החיבור האוטומטי של אפל
(VPN.OnDemandEnabled / OnDemandRules). הכול טהור — נבדק ביחידה.
"""
from __future__ import annotations

import base64
import plistlib
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

WG_APP = "com.wireguard.ios"                 # האפליקציה הרשמית של WireGuard ב-App Store
PROFILE_ID = "com.filterphone.ios"           # קבוע: פרופיל חדש מחליף את הקודם (לא מצטבר)
CONTENT_TYPE = "application/x-apple-aspen-config"
_NS = uuid.UUID("6f1c8a52-3f0e-4c57-9b0a-2d4c6a1e7b90")


def keypair() -> tuple[str, str]:
    """מפתח WireGuard חדש: (פרטי, ציבורי) ב-base64. באייפון בלי אפליקציה משלנו המפתח נוצר בשרת — הוא נמסר
    לטלפון בתוך הפרופיל (HTTPS, קישור חד-פעמי לזמן קצר) ואז נמחק מהשרת."""
    key = X25519PrivateKey.generate()
    private = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                serialization.NoEncryption())
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(private).decode(), base64.b64encode(public).decode()


def wg_quick(private_key: str, vpn: dict) -> str:
    """ההגדרות בפורמט wg-quick מתוך vpn_config של השרת (אותן הגדרות שהאפליקציה באנדרואיד מקבלת)."""
    lines = [
        "[Interface]",
        f"PrivateKey = {private_key}",
        f"Address = {vpn['address']}",
        f"DNS = {', '.join(vpn['dns'])}",
        f"MTU = {int(vpn['mtu'])}",
        "",
        "[Peer]",
        f"PublicKey = {vpn['server_public_key']}",
    ]
    if vpn.get("preshared_key"):
        lines.append(f"PresharedKey = {vpn['preshared_key']}")
    lines += [
        f"AllowedIPs = {', '.join(vpn['allowed_ips'])}",
        f"Endpoint = {vpn['endpoint']}",
        f"PersistentKeepalive = {int(vpn['persistent_keepalive'])}",
    ]
    return "\n".join(lines) + "\n"


def pem_to_der(pem: str) -> bytes:
    body = "".join(line.strip() for line in pem.splitlines() if line.strip() and not line.startswith("-----"))
    return base64.b64decode(body)


def _uuid(device_id: int, part: str) -> str:
    return str(uuid.uuid5(_NS, f"device-{device_id}-{part}")).upper()


def build(*, device_id: int, ca_pem: str, vpn: dict, private_key: str, locked: bool = False) -> bytes:
    """הפרופיל למכשיר. [locked] — למכשיר מנוהל (Supervised): הפרופיל לא ניתן להסרה והמתג "חיבור לפי דרישה"
    נעול. במכשיר רגיל אפל מתעלמת מ"לא ניתן להסרה", ולכן בבדיקה זה כבוי."""
    vpn_settings = {
        "RemoteAddress": vpn["endpoint"],
        "AuthenticationMethod": "Password",
        # חיבור אוטומטי: המערכת מרימה את המנהרה לבד בכל רשת, וגם אחרי הפעלה מחדש
        "OnDemandEnabled": 1,
        "OnDemandRules": [{"Action": "Connect"}],
    }
    if locked:
        vpn_settings["OnDemandUserOverrideDisabled"] = 1
    profile = {
        "PayloadType": "Configuration",
        "PayloadVersion": 1,
        "PayloadIdentifier": PROFILE_ID,
        "PayloadUUID": _uuid(device_id, "profile"),
        "PayloadDisplayName": "FilterPhone",
        "PayloadDescription": "הסינון של FilterPhone: חיבור מסונן (VPN) ותעודת הסינון.",
        "PayloadOrganization": "FilterPhone",
        "PayloadContent": [
            {
                "PayloadType": "com.apple.security.root",
                "PayloadVersion": 1,
                "PayloadIdentifier": f"{PROFILE_ID}.ca",
                "PayloadUUID": _uuid(device_id, "ca"),
                "PayloadDisplayName": "FilterPhone CA",
                "PayloadDescription": "תעודת הסינון — כדי שתמונות ואתרים מאובטחים ייבדקו.",
                "PayloadCertificateFileName": "FilterPhoneCA.cer",
                "PayloadContent": pem_to_der(ca_pem),
            },
            {
                "PayloadType": "com.apple.vpn.managed",
                "PayloadVersion": 1,
                "PayloadIdentifier": f"{PROFILE_ID}.vpn",
                "PayloadUUID": _uuid(device_id, "vpn"),
                "PayloadDisplayName": "VPN",
                "UserDefinedName": "FilterPhone",
                "VPNType": "VPN",
                "VPNSubType": WG_APP,
                "VendorConfig": {"WgQuickConfig": wg_quick(private_key, vpn)},
                "VPN": vpn_settings,
            },
        ],
    }
    if locked:
        profile["PayloadRemovalDisallowed"] = True
    return plistlib.dumps(profile, fmt=plistlib.FMT_XML, sort_keys=False)
