#!/usr/bin/env bash
# =====================================================================
# FilterPhone — בדיקה מקצה לקצה בשרת עצמו, בלי טלפון.
#
# יוצר "טלפון וירטואלי": network namespace עם לקוח WireGuard אמיתי.
# הלקוח נרשם דרך ה-API עם קוד הפעלה, מתחבר למנהרה, וגולש דרך הסינון.
# בסוף הכול נמחק (גם המכשיר מהמסד).
#
#   sudo bash tools/e2e_netns.sh [רמה]
# =====================================================================
set -uo pipefail

LEVEL=${1:-1}
NS=fpe2e
VENV=/opt/filterphone/venv
CA=/var/lib/filterphone/mitmproxy/mitmproxy-ca-cert.pem
UA="FilterPhoneE2E/1.0 (https://filterphone.com)"
IMG_WOMAN="https://upload.wikimedia.org/wikipedia/commons/thumb/e/ec/Mona_Lisa%2C_by_Leonardo_da_Vinci%2C_from_C2RMF_retouched.jpg/500px-Mona_Lisa%2C_by_Leonardo_da_Vinci%2C_from_C2RMF_retouched.jpg"
IMG_MAN="https://upload.wikimedia.org/wikipedia/commons/thumb/3/3e/Einstein_1921_by_F_Schmutzer_-_restoration.jpg/500px-Einstein_1921_by_F_Schmutzer_-_restoration.jpg"
IMG_LANDSCAPE="https://upload.wikimedia.org/wikipedia/commons/thumb/3/3f/Fronalpstock_big.jpg/500px-Fronalpstock_big.jpg"
# אתר אקראי — כדי שהרצה חוזרת תוך דקה (אותה כתובת VPN) לא תמצא אותו כבר "נלמד"
SITES=(https://www.example.org/ https://www.example.net/ https://www.iana.org/ https://www.w3.org/ https://www.python.org/ https://www.debian.org/)
PLAIN_SITE=${SITES[$((RANDOM % ${#SITES[@]}))]}

pass=0; fail=0
ok()   { echo "  ✓ $*"; pass=$((pass+1)); }
bad()  { echo "  ✗ $*"; fail=$((fail+1)); }
in_ns() { ip netns exec "$NS" "$@"; }

tmp=$(mktemp -d)
DEVICE_ID=""
PUB=""
cleanup() {
    ip netns del "$NS" 2>/dev/null
    [ -n "$PUB" ] && wg set wg0 peer "$PUB" remove 2>/dev/null
    rm -rf "$tmp"
    if [ -n "$DEVICE_ID" ]; then
        sudo -u filterphone "$VENV/bin/python" - "$DEVICE_ID" 2>/dev/null <<'PY'
import logging, sys
logging.disable(logging.WARNING)       # ה-peer כבר הוסר כ-root למעלה
from filterphone.api.app import build_services
build_services().delete_device(int(sys.argv[1]))
print("  (מכשיר הבדיקה נמחק)")
PY
    fi
}
trap cleanup EXIT

# ה-namespace בלי resolv.conf משלו — מתרגמים שמות דרך AdGuard במנהרה ומעבירים ל-curl
resolve_args() {
    local host ip
    host=$(echo "$1" | awk -F/ '{print $3}')
    ip=$(in_ns dig +short +time=3 @10.8.0.1 "$host" A | grep -E '^[0-9.]+$' | head -1)
    echo "--resolve $host:443:$ip --resolve $host:80:$ip"
}

# מחזיר: קוד HTTP, סוג תוכן, כותרת x-filterphone (או -)
fetch() {
    # shellcheck disable=SC2046
    in_ns curl -s -m 25 $(resolve_args "$1") --cacert "$CA" -A "$UA" -H "Sec-Fetch-Dest: image" \
        -D - -o "$tmp/body" "$1" \
        | tr -d '\r' \
        | awk 'tolower($1)=="content-type:"{ct=$2} tolower($1)=="x-filterphone:"{x=$2} /^HTTP/{c=$2} END{print c, ct, (x?x:"-")}'
}

expect() {  # תיאור, url, פעולה צפויה (block/pass/replace)
    local code ct xfp got
    read -r code ct xfp < <(fetch "$2")
    got=${xfp/-/pass}
    if [ "$code" != 200 ] || [[ "$ct" != image/* ]]; then
        bad "$1 → האתר לא החזיר תמונה ($code $ct) — הבדיקה לא תקפה"
    elif [ "$got" = "$3" ]; then
        ok "$1 → $got ($code $ct)"
    else
        bad "$1 → קיבלנו $got ($code $ct), ציפינו ל-$3"
    fi
}

# 0.0.0.0 = חסימה מקומית של AdGuard; ‏94.140.14.35 / 94.140.15.16 = דף החסימה של AdGuard Family
blocked_ip() { [[ -z "$1" || "$1" == "0.0.0.0" || "$1" == "94.140.14.35" || "$1" == "94.140.15.16" ]]; }

echo "== הרשמה דרך ה-API (רמה $LEVEL)"
CODE=$(sudo -u filterphone "$VENV/bin/python" -m filterphone.api.cli create-code --label "בדיקת e2e" --level "$LEVEL" --days 1 \
       | grep -oE '[A-Z0-9]{5}-[A-Z0-9]{5}')
PRIV=$(wg genkey); PUB=$(echo "$PRIV" | wg pubkey)
curl -s -X POST http://127.0.0.1:8090/v1/enroll -H 'Content-Type: application/json' \
    -d "{\"code\":\"$CODE\",\"public_key\":\"$PUB\",\"name\":\"טלפון וירטואלי\",\"platform\":\"e2e\"}" > "$tmp/enroll.json"
if read -r DEVICE_ID ADDR SPUB PSK < <(python3 -c '
import json, sys
d = json.load(open(sys.argv[1])); v = d["vpn"]
print(d["device_id"], v["address"], v["server_public_key"], v["preshared_key"])' "$tmp/enroll.json" 2>/dev/null); then
    ok "נרשם: מכשיר $DEVICE_ID, כתובת $ADDR"
else
    bad "ההרשמה נכשלה: $(cat "$tmp/enroll.json")"; exit 1
fi

echo "== חיבור WireGuard מתוך namespace"
ip netns add "$NS"
ip link add fpe2e-wg type wireguard          # נוצר ב-namespace הראשי — ה-socket המוצפן נשאר כאן
ip link set fpe2e-wg netns "$NS"
echo "$PRIV" > "$tmp/priv"; echo "$PSK" > "$tmp/psk"
in_ns wg set fpe2e-wg private-key "$tmp/priv" peer "$SPUB" preshared-key "$tmp/psk" \
    endpoint 127.0.0.1:51820 allowed-ips 0.0.0.0/0 persistent-keepalive 25
in_ns ip addr add "$ADDR" dev fpe2e-wg
in_ns ip link set lo up
in_ns ip link set fpe2e-wg up
in_ns ip route add default dev fpe2e-wg
sleep 2
if in_ns ping -c1 -W3 10.8.0.1 >/dev/null; then ok "המנהרה עובדת (ping לשרת)"; else bad "אין חיבור במנהרה"; fi
hs=$(wg show wg0 latest-handshakes | awk -v k="$PUB" '$1==k {print $2}')
if [ "${hs:-0}" -gt 0 ]; then ok "השרת רואה handshake מהמכשיר"; else bad "אין handshake"; fi

echo "== DNS"
if in_ns dig +short +time=3 @10.8.0.1 example.com | grep -qE '^[0-9.]+$'; then ok "DNS דרך AdGuard עונה"; else bad "DNS לא עונה"; fi
r=$(in_ns dig +short +time=3 @8.8.8.8 doubleclick.net | head -1)
if blocked_ip "$r"; then ok "בקשה ל-8.8.8.8 מנותבת בכוח ל-AdGuard (פרסומות: ${r:-NXDOMAIN})"; else bad "DNS חיצוני עוקף את הסינון: $r"; fi
r=$(in_ns dig +short +time=3 @8.8.8.8 pornhub.com | head -1)
if blocked_ip "$r"; then ok "אתר פורנוגרפי חסום גם דרך 8.8.8.8 (${r:-NXDOMAIN})"; else bad "אתר פורנוגרפי לא נחסם: $r"; fi
if in_ns timeout 5 bash -c 'exec 3<>/dev/tcp/1.1.1.1/853' 2>/dev/null; then bad "DNS מוצפן (853) פתוח"; else ok "DNS מוצפן חיצוני (853) חסום"; fi

echo "== תמונות דרך הסינון (HTTPS עם תעודת FilterPhone)"
if [ "$LEVEL" = 1 ]; then
    expect "אישה (מונה ליזה), רמה 1" "$IMG_WOMAN" block
else
    expect "אישה (מונה ליזה), רמה $LEVEL" "$IMG_WOMAN" pass
fi
expect "גבר (איינשטיין)" "$IMG_MAN" pass
expect "נוף (הרים)" "$IMG_LANDSCAPE" pass
t0=$(date +%s%N); fetch "$IMG_LANDSCAPE" >/dev/null; t1=$(date +%s%N)
ok "בקשה חוזרת לאותה תמונה: $(( (t1-t0)/1000000 ))ms (כולל הורדה מהאתר)"

echo "== אפליקציה שלא סומכת על התעודה (passthrough אוטומטי)"
# shellcheck disable=SC2046
if in_ns curl -s -m 15 $(resolve_args "$PLAIN_SITE") -o /dev/null "$PLAIN_SITE" 2>/dev/null; then
    bad "בלי התעודה לא היה אמור לעבוד בפעם הראשונה"
else
    ok "בלי התעודה: הפעם הראשונה נכשלת (כמו אפליקציה עם pinning)"
fi
sleep 1
# shellcheck disable=SC2046
if in_ns curl -s -m 15 $(resolve_args "$PLAIN_SITE") -o /dev/null -w '%{http_code}' "$PLAIN_SITE" | grep -q 200; then
    ok "בפעם השנייה עוברת ישירות — המערכת למדה"
else
    bad "ה-passthrough האוטומטי לא עבד"
fi

echo "== ה-API הציבורי מתוך המנהרה (כמו האפליקציה בטלפון)"
API="https://${FP_PUBLIC_HOST:-api.filterphone.com}"
TOKEN=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["token"])' "$tmp/enroll.json")
# shellcheck disable=SC2046
r=$(in_ns curl -s -m 15 $(resolve_args "$API/") -H "Authorization: Bearer $TOKEN" "$API/v1/device")
if echo "$r" | grep -q '"via_vpn":true'; then
    ok "השרת מזהה שהבקשה הגיעה דרך המנהרה (via_vpn)"
else
    bad "השרת לא מזהה את המנהרה: ${r:0:200}"
fi

echo "== QUIC (UDP 443) חסום — הדפדפן נאלץ לעבור ל-TCP שעובר בסינון"
if iptables -C FORWARD -i wg0 -p udp -m udp --dport 443 -j REJECT --reject-with icmp-port-unreachable 2>/dev/null; then
    ok "כלל חסימת QUIC פעיל"
else
    bad "אין כלל חסימת QUIC"
fi

echo
echo "סיכום: $pass עברו, $fail נכשלו"
[ "$fail" = 0 ]
