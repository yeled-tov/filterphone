#!/bin/bash
# ===================================
# יצירת לקוח VPN חדש - FilterPhone
# שימוש: sudo bash create-client.sh <userId> <שם-לקוח>
# דוגמה: sudo bash create-client.sh 5 "ישראל ישראלי"
# ===================================

set -e

# צבעים
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo_ok()   { echo -e "${GREEN}✓ $1${NC}"; }
echo_warn() { echo -e "${YELLOW}⚠ $1${NC}"; }
echo_err()  { echo -e "${RED}✗ $1${NC}"; }

# בדיקת פרמטרים
if [ "$#" -lt 2 ]; then
  echo_err "שימוש: sudo bash create-client.sh <userId> <שם-לקוח>"
  echo "  דוגמה: sudo bash create-client.sh 5 \"ישראל ישראלי\""
  exit 1
fi

USER_ID="$1"
CLIENT_NAME="$2"
WG_CONF="/etc/wireguard/wg0.conf"
CLIENTS_DIR="/home/ubuntu/vpn-clients"

# בדיקת הרשאות
if [ "$EUID" -ne 0 ]; then
  echo_err "יש להריץ כ-root: sudo bash create-client.sh"
  exit 1
fi

# בדיקה שמותקן WireGuard
if [ ! -f "$WG_CONF" ]; then
  echo_err "WireGuard לא מותקן. הרץ תחילה: sudo bash install-wireguard.sh"
  exit 1
fi

# בדיקה שהלקוח לא קיים כבר
if [ -f "${CLIENTS_DIR}/${USER_ID}.conf" ]; then
  echo_warn "קונפיג VPN כבר קיים ל-userId=${USER_ID}"
  echo "  קובץ: ${CLIENTS_DIR}/${USER_ID}.conf"
  exit 0
fi

echo ""
echo "============================================"
echo "  יוצר לקוח VPN: $CLIENT_NAME (ID: $USER_ID)"
echo "============================================"

# ----- קריאת פרטי שרת -----
SERVER_PUBLIC=$(cat /etc/wireguard/server_public.key 2>/dev/null)
SERVER_IP=$(cat /etc/wireguard/server_ip.txt 2>/dev/null || curl -s --max-time 5 https://api.ipify.org)

if [ -z "$SERVER_PUBLIC" ] || [ -z "$SERVER_IP" ]; then
  echo_err "לא נמצאו פרטי שרת. האם install-wireguard.sh רץ?"
  exit 1
fi

# ----- חישוב IP ללקוח -----
# מציאת האוקטט הגבוה ביותר הקיים ב-AllowedIPs, ולקיחת הבא אחריו
MAX_OCTET=$(grep "AllowedIPs = 10\.8\.0\." "$WG_CONF" 2>/dev/null \
  | grep -oE '10\.8\.0\.([0-9]+)/32' \
  | grep -oE '[0-9]+/32' \
  | cut -d/ -f1 \
  | sort -n | tail -1)
MAX_OCTET=$((${MAX_OCTET:-1} + 0))  # ברירת מחדל 1 (כי 10.8.0.1 הוא השרת)
CLIENT_IP_LAST=$((MAX_OCTET + 1))

# בדיקה שה-IP לא חורג מהרשת
if [ "$CLIENT_IP_LAST" -gt 254 ]; then
  echo_err "רשת VPN מלאה (254 לקוחות מקסימום)"
  exit 1
fi

CLIENT_IP="10.8.0.${CLIENT_IP_LAST}"

# ווידוא שה-IP תקין לפני המשך
if ! echo "$CLIENT_IP" | grep -qE '^10\.8\.0\.[0-9]+$'; then
  echo_err "IP לא תקין חושב: '${CLIENT_IP}'. בדוק את wg0.conf."
  exit 1
fi

echo_ok "כתובת IP של הלקוח: $CLIENT_IP"

# ----- יצירת מפתחות לקוח -----
echo_warn "יוצר מפתחות הצפנה ללקוח..."
CLIENT_PRIVATE=$(wg genkey)
CLIENT_PUBLIC=$(echo "$CLIENT_PRIVATE" | wg pubkey)
CLIENT_PRESHARED=$(wg genpsk)
echo_ok "מפתחות לקוח נוצרו"

# ----- יצירת קובץ קונפיג ללקוח -----
mkdir -p "$CLIENTS_DIR"
CLIENT_CONF="${CLIENTS_DIR}/${USER_ID}.conf"

cat > "$CLIENT_CONF" << EOF
# ===================================
# WireGuard Config - FilterPhone
# לקוח: $CLIENT_NAME (ID: $USER_ID)
# נוצר: $(date '+%Y-%m-%d %H:%M:%S')
# ===================================

[Interface]
# כתובת הלקוח ברשת ה-VPN
Address = ${CLIENT_IP}/24

# DNS = AdGuard Home על השרת (סינון DNS!)
DNS = 10.8.0.1

# המפתח הפרטי של הלקוח (שמור בסוד!)
PrivateKey = ${CLIENT_PRIVATE}

[Peer]
# פרטי שרת ה-VPN
PublicKey = ${SERVER_PUBLIC}
PresharedKey = ${CLIENT_PRESHARED}

# כתובת השרת ופורט
Endpoint = ${SERVER_IP}:51820

# כל התעבורה עוברת דרך ה-VPN (כולל DNS)
AllowedIPs = 0.0.0.0/0, ::/0

# שמירה על חיבור פעיל (30 שניות)
PersistentKeepalive = 30
EOF

chmod 600 "$CLIENT_CONF"
echo_ok "קובץ קונפיג נוצר: $CLIENT_CONF"

# ----- הוספה ל-wg0.conf -----
echo_warn "מוסיף לקוח ל-wg0.conf..."
cat >> "$WG_CONF" << EOF

# לקוח: $CLIENT_NAME (ID: $USER_ID) | IP: $CLIENT_IP | נוצר: $(date '+%Y-%m-%d')
[Peer]
PublicKey = ${CLIENT_PUBLIC}
PresharedKey = ${CLIENT_PRESHARED}
AllowedIPs = ${CLIENT_IP}/32
EOF

echo_ok "לקוח נוסף ל-wg0.conf"

# ----- רענון WireGuard בלי לנתק לקוחות -----
echo_warn "מרענן WireGuard (בלי ניתוק לקוחות קיימים)..."
if wg show wg0 &>/dev/null; then
  # syncconf - מעדכן בלי הפרעה ללקוחות מחוברים
  wg syncconf wg0 <(wg-quick strip wg0)
  echo_ok "WireGuard רוענן"
else
  echo_warn "WireGuard לא פעיל. מפעיל..."
  systemctl start wg-quick@wg0
fi

# ----- יצירת QR code -----
echo_warn "יוצר QR code..."
QR_FILE="${CLIENTS_DIR}/${USER_ID}.png"
qrencode -t PNG -o "$QR_FILE" -r "$CLIENT_CONF" 2>/dev/null && echo_ok "QR code נוצר: $QR_FILE" || echo_warn "qrencode לא זמין, מדלג על QR"

# הדפסת QR בטרמינל
echo ""
echo "QR Code (לסריקה מהמכשיר):"
qrencode -t ANSIUTF8 < "$CLIENT_CONF" 2>/dev/null || true

# ----- שמירת מפתח ציבורי -----
echo "$CLIENT_PUBLIC" > "${CLIENTS_DIR}/${USER_ID}.pubkey"
echo "$CLIENT_IP" > "${CLIENTS_DIR}/${USER_ID}.ip"

# ----- העברת בעלות ל-ubuntu כדי שהשרת יוכל לקרוא -----
chown ubuntu:ubuntu "${CLIENTS_DIR}/${USER_ID}.conf" \
  "${CLIENTS_DIR}/${USER_ID}.pubkey" \
  "${CLIENTS_DIR}/${USER_ID}.ip" 2>/dev/null || true
[ -f "${CLIENTS_DIR}/${USER_ID}.png" ] && chown ubuntu:ubuntu "${CLIENTS_DIR}/${USER_ID}.png" || true
echo_ok "הרשאות קבצים עודכנו"

# ----- סיכום -----
echo ""
echo "============================================"
echo_ok "  לקוח VPN נוצר בהצלחה!"
echo "============================================"
echo ""
echo "  לקוח:        $CLIENT_NAME"
echo "  User ID:     $USER_ID"
echo "  VPN IP:      $CLIENT_IP"
echo "  קובץ קונפיג: $CLIENT_CONF"
echo "  QR Code:     $QR_FILE"
echo ""
echo "  לשליחה ללקוח: cat $CLIENT_CONF"
echo ""
