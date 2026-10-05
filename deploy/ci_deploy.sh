#!/usr/bin/env bash
# =====================================================================
# FilterPhone — הדבר היחיד שמפתח ההעלאה של GitHub מורשה להריץ בשרת.
#
# מותקן ב-/usr/local/sbin/fp-ci-deploy (root, לקריאה בלבד), ונקבע כ-forced command
# של המפתח ב-~ubuntu/.ssh/authorized_keys:
#   restrict,command="/usr/local/sbin/fp-ci-deploy" ssh-ed25519 AAAA... fp-ci-deploy
# כך שמי שמחזיק במפתח לא מקבל shell — רק את הפקודות כאן:
#   deploy <rev>       stdin: tar.gz של server/ tools/ deploy/ → התקנה (כמו deploy/deploy.sh)
#   apk                stdin: tar עם FilterPhone.apk + FilterPhone-universal.apk → דף ההורדה
#   status             גרסה, שירותים, מקום פנוי
#   rollback <גיבוי>   חזרה לגרסה הקודמת (deploy/rollback_version.sh) עם /root/fp-backups/<גיבוי>
# =====================================================================
set -euo pipefail

A=/opt/filterphone
read -r cmd arg extra <<<"${SSH_ORIGINAL_COMMAND:-}"
[ -z "${extra:-}" ] || { echo "פקודה לא מוכרת"; exit 2; }

case "${cmd:-}" in
deploy)
    [[ "${arg:-}" =~ ^[0-9a-f]{7,40}$ ]] || { echo "גרסה לא חוקית"; exit 2; }
    sudo -n rm -rf "$A/src.new"
    sudo -n mkdir -p "$A/src.new"
    sudo -n tar -C "$A/src.new" -xzf - server tools deploy
    # כמו deploy.sh: הגרסה שרצה נשמרת ב-src.prev, והעלאה חוזרת של אותה גרסה לא דורסת אותה
    if [ -d "$A/src" ] && [ "$(cat "$A/src/REVISION" 2>/dev/null)" != "$arg" ]; then
        sudo -n rm -rf "$A/src.prev"
        sudo -n mv "$A/src" "$A/src.prev"
    else
        sudo -n rm -rf "$A/src"
    fi
    sudo -n mv "$A/src.new" "$A/src"
    echo "$arg" | sudo -n tee "$A/src/REVISION" >/dev/null
    exec sudo -n bash "$A/src/deploy/install.sh"
    ;;
apk)
    tmp=$(mktemp -d)
    trap 'rm -rf "$tmp"' EXIT
    tar -C "$tmp" -xf - FilterPhone.apk FilterPhone-universal.apk
    for f in FilterPhone.apk FilterPhone-universal.apk; do
        [ "$(head -c 2 "$tmp/$f")" = "PK" ] || { echo "$f אינו APK"; exit 2; }
    done
    sudo -n install -m 644 -o root -g root "$tmp/FilterPhone.apk" "$tmp/FilterPhone-universal.apk" /var/www/filterphone/
    ls -lh /var/www/filterphone/
    ;;
status)
    echo "גרסה: $(cat "$A/src/REVISION" 2>/dev/null || echo '?') · קודמת: $(cat "$A/src.prev/REVISION" 2>/dev/null || echo '-')"
    for s in fp-engine fp-proxy fp-api adguardhome caddy redis-server wg-quick@wg0; do
        printf '  %-16s %s\n' "$s" "$(systemctl is-active "$s" || true)"
    done
    df -h / | tail -1
    echo "גיבויים אחרונים:"
    sudo -n ls -1t /root/fp-backups | head -5
    ;;
rollback)
    [[ "${arg:-}" =~ ^[0-9]{8}-[0-9]{6}$ ]] || { echo "שם גיבוי לא חוקי (למשל 20261005-213000)"; exit 2; }
    exec sudo -n bash "$A/src/deploy/rollback_version.sh" "/root/fp-backups/$arg"
    ;;
*)
    echo "פקודה לא מוכרת"
    exit 2
    ;;
esac
