#!/usr/bin/env bash
# =====================================================================
# FilterPhone — חזרה למערכת הישנה (לשעת חירום). רץ כ-root בשרת.
#   sudo bash /opt/filterphone/src/deploy/rollback.sh /root/fp-backups/<תאריך>
# =====================================================================
set -euo pipefail
B=${1:?"צריך לציין תיקיית גיבוי, למשל /root/fp-backups/20261005-120000"}
[ -d "$B" ] || { echo "אין תיקייה $B"; exit 1; }

systemctl disable --now fp-proxy fp-api fp-engine caddy 2>/dev/null || true
[ -f "$B/rules.v4" ] && cp -a "$B/rules.v4" /etc/iptables/rules.v4 && iptables-restore < /etc/iptables/rules.v4
[ -f "$B/AdGuardHome.yaml" ] && { systemctl stop adguardhome; cp -a "$B/AdGuardHome.yaml" /opt/AdGuardHome/AdGuardHome.yaml; systemctl start adguardhome; }
[ -f "$B/wg0.conf" ] && { cp -a "$B/wg0.conf" /etc/wireguard/wg0.conf; systemctl restart wg-quick@wg0; }
systemctl enable --now filterphone-mitm 2>/dev/null || true
sudo -u ubuntu pm2 start filterphone-server filterphone-ui 2>/dev/null || true
rm -f /var/lib/filterphone/.legacy-migrated
echo "חזרנו למערכת הישנה. הגיבויים נשארו ב-$B"
