#!/bin/bash
# ===================================
# התקנת WireGuard VPN - FilterPhone
# רשת VPN: 10.8.0.0/24
# פורט: 51820 UDP
# DNS ללקוחות: 10.8.0.1 (AdGuard Home)
# ===================================

set -e

# צבעים להודעות
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo_ok()   { echo -e "${GREEN}✓ $1${NC}"; }
echo_warn() { echo -e "${YELLOW}⚠ $1${NC}"; }
echo_err()  { echo -e "${RED}✗ $1${NC}"; }

# בדיקת הרשאות root
if [ "$EUID" -ne 0 ]; then
  echo_err "יש להריץ כ-root: sudo bash install-wireguard.sh"
  exit 1
fi

echo ""
echo "============================================"
echo "  התקנת WireGuard VPN - FilterPhone"
echo "============================================"
echo ""

# ----- שלב 1: זיהוי IP ציבורי -----
echo_warn "מזהה IP ציבורי של השרת..."
SERVER_IP=$(curl -s --max-time 5 https://api.ipify.org 2>/dev/null || \
            curl -s --max-time 5 https://ifconfig.me 2>/dev/null || \
            hostname -I | awk '{print $1}')

if [ -z "$SERVER_IP" ]; then
  echo_err "לא ניתן לזהות IP ציבורי. הכנס ידנית:"
  read -r SERVER_IP
fi
echo_ok "IP שרת: $SERVER_IP"

# ----- שלב 2: זיהוי ממשק הרשת הראשי -----
MAIN_IFACE=$(ip route | grep '^default' | awk '{print $5}' | head -1)
if [ -z "$MAIN_IFACE" ]; then
  MAIN_IFACE="ens3"
  echo_warn "לא זוהה ממשק רשת ראשי, משתמש ב-$MAIN_IFACE"
else
  echo_ok "ממשק רשת ראשי: $MAIN_IFACE"
fi

# ----- שלב 3: התקנת WireGuard -----
echo_warn "מתקין WireGuard ו-qrencode..."
apt-get update -qq
apt-get install -y -qq wireguard wireguard-tools qrencode iptables-persistent
echo_ok "WireGuard ו-qrencode הותקנו"

# ----- שלב 4: הפעלת IP forwarding -----
echo_warn "מפעיל IP forwarding..."
cat > /etc/sysctl.d/99-wireguard.conf << 'EOF'
# FilterPhone - IP forwarding עבור VPN
net.ipv4.ip_forward = 1
net.ipv6.conf.all.forwarding = 1
EOF
sysctl --system -q
echo_ok "IP forwarding הופעל"

# ----- שלב 5: יצירת מפתחות שרת -----
echo_warn "יוצר מפתחות הצפנה לשרת..."
mkdir -p /etc/wireguard
chmod 700 /etc/wireguard

# יצירת מפתחות אם לא קיימים
if [ ! -f /etc/wireguard/server_private.key ]; then
  wg genkey | tee /etc/wireguard/server_private.key | wg pubkey > /etc/wireguard/server_public.key
  chmod 600 /etc/wireguard/server_private.key
  echo_ok "מפתחות שרת נוצרו"
else
  echo_ok "מפתחות שרת קיימים - משתמש בקיימים"
fi

SERVER_PRIVATE=$(cat /etc/wireguard/server_private.key)
SERVER_PUBLIC=$(cat /etc/wireguard/server_public.key)

# ----- שלב 6: יצירת קובץ WireGuard -----
echo_warn "יוצר /etc/wireguard/wg0.conf..."

# שמירת ה-IP של השרת לשימוש בסקריפטים אחרים
echo "$SERVER_IP" > /etc/wireguard/server_ip.txt
echo "$MAIN_IFACE" > /etc/wireguard/server_iface.txt

cat > /etc/wireguard/wg0.conf << EOF
# ===================================
# WireGuard Server Config - FilterPhone
# נוצר אוטומטית על ידי install-wireguard.sh
# ===================================
[Interface]
# כתובת השרת ברשת ה-VPN
Address = 10.8.0.1/24

# פורט האזנה
ListenPort = 51820

# המפתח הפרטי של השרת
PrivateKey = ${SERVER_PRIVATE}

# הפעלת NAT - כל תעבורה מרשת ה-VPN תצא לאינטרנט
PostUp = iptables -A FORWARD -i wg0 -j ACCEPT; iptables -A FORWARD -o wg0 -j ACCEPT; iptables -t nat -A POSTROUTING -o ${MAIN_IFACE} -j MASQUERADE
PostDown = iptables -D FORWARD -i wg0 -j ACCEPT; iptables -D FORWARD -o wg0 -j ACCEPT; iptables -t nat -D POSTROUTING -o ${MAIN_IFACE} -j MASQUERADE

# === לקוחות מוגדרים ===
# (מתווספים על ידי create-client.sh)
EOF

chmod 600 /etc/wireguard/wg0.conf
echo_ok "wg0.conf נוצר"

# ----- שלב 7: יצירת תיקיית לקוחות -----
mkdir -p /home/ubuntu/vpn-clients
chown ubuntu:ubuntu /home/ubuntu/vpn-clients 2>/dev/null || true
echo_ok "תיקיית /home/ubuntu/vpn-clients נוצרה"

# ----- שלב 8: הפעלת WireGuard -----
echo_warn "מפעיל WireGuard..."
systemctl enable wg-quick@wg0
systemctl start wg-quick@wg0
echo_ok "WireGuard הופעל"

# המתנה לאתחול
sleep 2

# ----- שלב 9: iptables פתיחת פורט -----
echo_warn "פותח פורט 51820 ב-iptables..."
iptables -I INPUT -p udp --dport 51820 -j ACCEPT 2>/dev/null || true
netfilter-persistent save 2>/dev/null || true
echo_ok "פורט 51820 פתוח"

# ----- בדיקה -----
if wg show wg0 &>/dev/null; then
  echo ""
  echo "============================================"
  echo_ok "  WireGuard VPN הותקן בהצלחה!"
  echo "============================================"
  echo ""
  echo "  IP שרת:       $SERVER_IP"
  echo "  פורט VPN:     51820 UDP"
  echo "  רשת VPN:      10.8.0.0/24"
  echo "  DNS ללקוחות:  10.8.0.1 (AdGuard Home)"
  echo "  מפתח ציבורי: $SERVER_PUBLIC"
  echo ""
  echo "  לבדיקה:     wg show"
  echo "  יצירת לקוח: sudo bash create-client.sh <userId> <שם>"
  echo ""
else
  echo_err "WireGuard לא הופעל. בדוק: journalctl -u wg-quick@wg0 -n 50"
  exit 1
fi
