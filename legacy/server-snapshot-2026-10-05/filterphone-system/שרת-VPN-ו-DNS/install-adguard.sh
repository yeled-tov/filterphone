#!/bin/bash
# ===================================
# התקנת AdGuard Home - FilterPhone
# מריץ את AdGuard Home כ-DNS (פורט 53)
# ממשק ניהול על פורט 3002
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
  echo_err "יש להריץ כ-root: sudo bash install-adguard.sh"
  exit 1
fi

echo ""
echo "============================================"
echo "  התקנת AdGuard Home - FilterPhone"
echo "============================================"
echo ""

# ----- שלב 1: עצירת systemd-resolved שחוסם פורט 53 -----
echo_warn "עוצר systemd-resolved (כדי לפנות פורט 53)..."
systemctl stop systemd-resolved 2>/dev/null || true
systemctl disable systemd-resolved 2>/dev/null || true
# מגדיר DNS זמני כדי שהשרת ימשיך לעבוד
echo "nameserver 8.8.8.8" > /etc/resolv.conf
echo "nameserver 1.1.1.1" >> /etc/resolv.conf
echo_ok "systemd-resolved הופסק"

# ----- שלב 2: הורדת AdGuard Home -----
echo_warn "מוריד AdGuard Home..."
ARCH=$(uname -m)
case $ARCH in
  x86_64)  AGH_ARCH="amd64" ;;
  aarch64) AGH_ARCH="arm64" ;;
  armv7l)  AGH_ARCH="armv7" ;;
  *)       echo_err "ארכיטקטורה לא נתמכת: $ARCH"; exit 1 ;;
esac

AGH_VERSION="v0.107.54"
AGH_URL="https://github.com/AdguardTeam/AdGuardHome/releases/download/${AGH_VERSION}/AdGuardHome_linux_${AGH_ARCH}.tar.gz"

cd /tmp
wget -q --show-progress -O adguardhome.tar.gz "$AGH_URL"
tar -xzf adguardhome.tar.gz
echo_ok "AdGuard Home הורד"

# ----- שלב 3: התקנה -----
echo_warn "מתקין AdGuard Home..."
mkdir -p /opt/AdGuardHome
cp /tmp/AdGuardHome/AdGuardHome /opt/AdGuardHome/
chmod +x /opt/AdGuardHome/AdGuardHome
rm -rf /tmp/AdGuardHome /tmp/adguardhome.tar.gz
echo_ok "AdGuard Home הועתק ל-/opt/AdGuardHome"

# ----- שלב 4: יצירת קובץ קונפיגורציה -----
echo_warn "יוצר קובץ הגדרות..."
mkdir -p /opt/AdGuardHome/data

# סיסמת ברירת מחדל: REDACTED
# מוצפנת ב-bcrypt (נוצרה מראש)
ADMIN_PASS_HASH='$2a$10$YourBcryptHashHere'

# נשתמש ב-htpasswd לייצור ה-hash האמיתי אם זמין, אחרת נשתמש בעזרת Python
if command -v python3 &>/dev/null; then
  ADMIN_PASS_HASH=$(python3 -c "
import subprocess, sys
try:
    import bcrypt
    h = bcrypt.hashpw(b'REDACTED', bcrypt.gensalt()).decode()
    print(h)
except ImportError:
    # אם bcrypt לא מותקן, נשתמש ב-AdGuard API לאחר ההפעלה
    print('PENDING')
")
fi

cat > /opt/AdGuardHome/AdGuardHome.yaml << 'YAMLEOF'
http:
  pprof:
    port: 6060
    enabled: false
  address: 0.0.0.0:3002
  session_ttl: 720h
users:
  - name: admin
    password: ADMIN_PASS_PLACEHOLDER
dns:
  bind_hosts:
    - 0.0.0.0
  port: 53
  anonymize_client_ip: false
  ratelimit: 20
  ratelimit_subnet_len_ipv4: 24
  ratelimit_subnet_len_ipv6: 56
  ratelimit_whitelist: []
  refuse_any: true
  upstream_dns:
    - 94.140.14.15
    - 94.140.15.16
    - https://dns.adguard-dns.com/dns-query
  upstream_dns_file: ""
  bootstrap_dns:
    - 9.9.9.10
    - 149.112.112.10
    - 2620:fe::10
    - 2620:fe::fe:10
  fallback_dns: []
  upstream_mode: load_balance
  fastest_timeout: 1s
  allowed_clients: []
  disallowed_clients: []
  blocked_hosts:
    - version.bind
    - id.server
    - hostname.bind
  trusted_proxies:
    - 127.0.0.0/8
    - ::1/128
  cache_size: 4194304
  cache_ttl_min: 0
  cache_ttl_max: 0
  cache_optimistic: false
  bogus_nxdomain: []
  aaaa_disabled: false
  enable_dnssec: false
  edns_client_subnet:
    custom_ip: ""
    enabled: false
    use_custom: false
  max_goroutines: 300
  handle_ddr: true
  ipset: []
  ipset_file: ""
  bootstrap_prefer_ipv6: false
  filtering_enabled: true
  filters_update_interval: 24
  parental_enabled: true
  safebrowsing_enabled: true
  safebrowsing_cache_size: 1048576
  safesearch:
    enabled: true
    bing: true
    duckduckgo: true
    google: true
    pixabay: true
    yandex: true
    youtube: true
  safesearch_cache_size: 1048576
  rewrites: []
  blocked_services:
    schedule:
      time_zone: UTC
    ids: []
  protection_enabled: true
  blocking_mode: default
  blocking_ipv4: ""
  blocking_ipv6: ""
  blocked_response_ttl: 10
  protection_disabled_until: null
  private_networks: []
  use_private_ptr_resolvers: true
  local_ptr_upstreams: []
  use_dns64: false
  dns64_prefixes: []
  serve_http3: false
  use_http3_upstreams: false
  serve_plain_dns: true
  hostsfile_enabled: true
filtering:
  blocking_ipv4: ""
  blocking_ipv6: ""
  blocked_services:
    schedule:
      time_zone: UTC
    ids: []
  protection_enabled: true
  filtering_enabled: true
  parental_enabled: true
  safebrowsing_enabled: true
  safesearch:
    enabled: true
    bing: true
    duckduckgo: true
    google: true
    pixabay: true
    yandex: true
    youtube: true
filters:
  - enabled: true
    url: https://adguardteam.github.io/AdGuardSDNSFilter/Filters/filter.txt
    name: AdGuard DNS filter (פרסומות וטרקינג)
    id: 1
  - enabled: true
    url: https://adguardteam.github.io/HostlistsRegistry/assets/filter_30.txt
    name: AdGuard Family filter (פורנו ואתרי מבוגרים)
    id: 2
  - enabled: true
    url: https://adguardteam.github.io/HostlistsRegistry/assets/filter_33.txt
    name: AdGuard Tracking Protection (מעקב)
    id: 3
  - enabled: true
    url: https://easylist.to/easylist/easylist.txt
    name: EasyList (פרסומות)
    id: 4
  - enabled: true
    url: https://raw.githubusercontent.com/hagezi/dns-blocklists/main/adblock/pro.plus.txt
    name: Hagezi Pro++ (חסימה מקיפה)
    id: 5
whitelist_filters: []
user_rules: []
os:
  group: ""
  user: ""
  rlimit_nofile: 0
schema_version: 29
YAMLEOF

echo_ok "קובץ הגדרות נוצר"

# ----- שלב 5: הגדרת סיסמת admin -----
echo_warn "מגדיר סיסמת admin..."

# יצירת hash עם htpasswd אם זמין
if command -v htpasswd &>/dev/null; then
  HASH=$(htpasswd -bnBC 10 "" "REDACTED" | tr -d ':\n' | sed 's/^//')
  sed -i "s|ADMIN_PASS_PLACEHOLDER|${HASH}|g" /opt/AdGuardHome/AdGuardHome.yaml
elif command -v python3 &>/dev/null; then
  # נסה להתקין bcrypt ולייצר hash
  pip3 install bcrypt -q 2>/dev/null || true
  HASH=$(python3 -c "
import bcrypt
h = bcrypt.hashpw(b'REDACTED', bcrypt.gensalt(10)).decode()
print(h)
" 2>/dev/null)
  if [ -n "$HASH" ]; then
    sed -i "s|ADMIN_PASS_PLACEHOLDER|${HASH}|g" /opt/AdGuardHome/AdGuardHome.yaml
  else
    # שמור placeholder - יצריך הגדרה ידנית
    sed -i "s|ADMIN_PASS_PLACEHOLDER|\$2y\$10\$CHANGE_THIS_HASH|g" /opt/AdGuardHome/AdGuardHome.yaml
    echo_warn "לא ניתן לייצר hash אוטומטי. הפעל ידנית: /opt/AdGuardHome/AdGuardHome -s install"
  fi
fi

echo_ok "סיסמת admin הוגדרה"

# ----- שלב 6: התקנה כשירות systemd -----
echo_warn "מגדיר שירות systemd..."
cat > /etc/systemd/system/adguardhome.service << 'EOF'
[Unit]
Description=AdGuard Home - FilterPhone DNS
After=network.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/AdGuardHome
ExecStart=/opt/AdGuardHome/AdGuardHome -s run -c /opt/AdGuardHome/AdGuardHome.yaml -w /opt/AdGuardHome/data
ExecStop=/opt/AdGuardHome/AdGuardHome -s stop
Restart=on-failure
RestartSec=10
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable adguardhome
systemctl start adguardhome
echo_ok "שירות AdGuard Home הופעל"

# ----- שלב 7: המתנה ובדיקה -----
echo_warn "ממתין לאתחול AdGuard Home..."
sleep 5

if systemctl is-active --quiet adguardhome; then
  echo_ok "AdGuard Home רץ בהצלחה"
else
  echo_err "AdGuard Home לא הופעל. בדוק: journalctl -u adguardhome -n 50"
  exit 1
fi

# ----- סיכום -----
echo ""
echo "============================================"
echo_ok "  AdGuard Home הותקן בהצלחה!"
echo "============================================"
echo ""
echo "  ממשק ניהול:  http://$(hostname -I | awk '{print $1}'):3002"
echo "  משתמש:       admin"
echo "  סיסמה:       REDACTED"
echo "  DNS פורט:    53"
echo ""
echo "  לבדיקה: dig @127.0.0.1 google.com"
echo ""
