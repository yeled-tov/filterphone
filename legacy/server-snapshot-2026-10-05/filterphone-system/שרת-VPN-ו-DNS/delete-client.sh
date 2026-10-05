#!/bin/bash
# ===================================
# מחיקת לקוח VPN - FilterPhone
# שימוש: sudo bash delete-client.sh <userId>
# דוגמה: sudo bash delete-client.sh 5
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
if [ "$#" -lt 1 ]; then
  echo_err "שימוש: sudo bash delete-client.sh <userId>"
  echo "  דוגמה: sudo bash delete-client.sh 5"
  exit 1
fi

USER_ID="$1"
WG_CONF="/etc/wireguard/wg0.conf"
CLIENTS_DIR="/home/ubuntu/vpn-clients"
CLIENT_CONF="${CLIENTS_DIR}/${USER_ID}.conf"
PUBKEY_FILE="${CLIENTS_DIR}/${USER_ID}.pubkey"

# בדיקת הרשאות
if [ "$EUID" -ne 0 ]; then
  echo_err "יש להריץ כ-root: sudo bash delete-client.sh"
  exit 1
fi

# בדיקה שהלקוח קיים
if [ ! -f "$CLIENT_CONF" ]; then
  echo_err "לקוח לא נמצא: userId=${USER_ID}"
  echo "  קובץ לא קיים: $CLIENT_CONF"
  exit 1
fi

echo ""
echo "============================================"
echo "  מוחק לקוח VPN - ID: $USER_ID"
echo "============================================"

# ----- קריאת מפתח ציבורי של הלקוח -----
if [ -f "$PUBKEY_FILE" ]; then
  CLIENT_PUBLIC=$(cat "$PUBKEY_FILE")
else
  # חילוץ מהקובץ קונפיג
  CLIENT_PUBLIC=$(grep -m1 "PrivateKey" "$CLIENT_CONF" | awk '{print $3}' | wg pubkey 2>/dev/null || echo "")
fi

# ----- הסרת peer מ-WireGuard (בלי הפרעה לאחרים) -----
if [ -n "$CLIENT_PUBLIC" ] && wg show wg0 &>/dev/null; then
  echo_warn "מסיר peer מ-WireGuard..."
  wg set wg0 peer "$CLIENT_PUBLIC" remove 2>/dev/null && echo_ok "Peer הוסר מ-WireGuard" || echo_warn "לא נמצא peer פעיל"
fi

# ----- הסרה מ-wg0.conf -----
echo_warn "מסיר מ-wg0.conf..."

if [ -n "$CLIENT_PUBLIC" ]; then
  # מציאת הבלוק של הלקוח ומחיקתו
  # מחפשים את שורת ה-Peer עם ה-PublicKey הספציפי ומוחקים את הבלוק כולו
  python3 << PYEOF
import re

with open('${WG_CONF}', 'r') as f:
    content = f.read()

# חלוקה לחלקים - header ו-peers
# כל peer מתחיל ב-[Peer] וגם ה-comment לפניו
public_key = '${CLIENT_PUBLIC}'

# מחיקת הבלוק כולל comment לפניו
pattern = r'\n# לקוח:.*?ID: ${USER_ID}.*?\[Peer\]\nPublicKey = ' + re.escape(public_key) + r'.*?(?=\n# לקוח:|\n\[Interface\]|\Z)'
new_content = re.sub(pattern, '', content, flags=re.DOTALL)

if new_content == content:
    # ניסיון שני - מחיקה פשוטה יותר
    pattern2 = r'\n\[Peer\]\nPublicKey = ' + re.escape(public_key) + r'.*?(?=\n\[Peer\]|\Z)'
    new_content = re.sub(pattern2, '', content, flags=re.DOTALL)

with open('${WG_CONF}', 'w') as f:
    f.write(new_content)

print("✓ הבלוק הוסר מ-wg0.conf")
PYEOF
else
  echo_warn "לא נמצא מפתח ציבורי - עדכן wg0.conf ידנית"
fi

# ----- מחיקת קבצי הלקוח -----
echo_warn "מוחק קבצי לקוח..."
rm -f "$CLIENT_CONF"
rm -f "${CLIENTS_DIR}/${USER_ID}.pubkey"
rm -f "${CLIENTS_DIR}/${USER_ID}.ip"
rm -f "${CLIENTS_DIR}/${USER_ID}.png"
echo_ok "קבצי לקוח נמחקו"

# ----- רענון WireGuard -----
if wg show wg0 &>/dev/null; then
  echo_warn "מרענן WireGuard..."
  wg syncconf wg0 <(wg-quick strip wg0) 2>/dev/null || true
  echo_ok "WireGuard רוענן"
fi

echo ""
echo "============================================"
echo_ok "  לקוח VPN נמחק בהצלחה!"
echo "============================================"
echo ""
echo "  User ID: $USER_ID"
echo "  ה-VPN שלו כבר לא יעבוד."
echo ""
