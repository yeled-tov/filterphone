#!/usr/bin/env bash
# =====================================================================
# FilterPhone — הדבר היחיד שמפתח ההעלאה של GitHub מורשה להריץ בשרת.
#
# מותקן ב-/usr/local/sbin/fp-ci-deploy (root, לקריאה בלבד), ונקבע כ-forced command
# של המפתח ב-~ubuntu/.ssh/authorized_keys:
#   restrict,command="/usr/local/sbin/fp-ci-deploy" ssh-ed25519 AAAA... fp-ci-deploy
# כך שמי שמחזיק במפתח לא מקבל shell — רק את הפקודות כאן:
#   deploy <rev>       stdin: tar.gz של server/ tools/ deploy/ → התקנה (כמו deploy/deploy.sh)
#   apk                stdin: tar עם FilterPhone.apk + FilterPhone-universal.apk (+ FilterPhone.json) → דף ההורדה
#   status             גרסה, שירותים, מקום פנוי
#   rollback <גיבוי>   חזרה לגרסה הקודמת (deploy/rollback_version.sh) עם /root/fp-backups/<גיבוי>
#   wabot              אבחון בוט הוואטסאפ: מצב השירות, יומן אחרון, Chrome, הבוט הישן (pm2)
#   legacy-bot <start|stop|logs>   הבוט הישן (filterphone-bot ב-pm2) — להשוואה מול הבוט החדש
#   devices            אבחון מכשירים (לקריאה בלבד, בלי מספרי טלפון): מצב, גרסה, דיווח אחרון,
#                      חיבור VPN (WireGuard), ושגיאות אחרונות של השרת
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
    cat > "$tmp/in.tar"
    # רק השמות האלה — שום נתיב אחר לא נפתח
    while read -r name; do
        case "$name" in
            FilterPhone.apk|FilterPhone-universal.apk|FilterPhone.json) ;;
            *) echo "קובץ לא צפוי בחבילה: $name"; exit 2 ;;
        esac
    done < <(tar -tf "$tmp/in.tar")
    mkdir "$tmp/x"
    tar -C "$tmp/x" -xf "$tmp/in.tar"
    for f in FilterPhone.apk FilterPhone-universal.apk; do
        [ -f "$tmp/x/$f" ] && [ "$(head -c 2 "$tmp/x/$f")" = "PK" ] || { echo "$f חסר או אינו APK"; exit 2; }
    done
    files=("$tmp/x/FilterPhone.apk" "$tmp/x/FilterPhone-universal.apk")
    if [ -f "$tmp/x/FilterPhone.json" ]; then
        python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); assert isinstance(d.get("files"), list)' 2>/dev/null \
            "$tmp/x/FilterPhone.json" || { echo "FilterPhone.json לא תקין"; exit 2; }
        files+=("$tmp/x/FilterPhone.json")
    else
        # בלי רשימת גרסה: לא משאירים רשימה ישנה (השרת ממילא לא מציע קובץ שלא תואם לה)
        sudo -n rm -f /var/www/filterphone/FilterPhone.json
    fi
    sudo -n install -m 644 -o root -g root "${files[@]}" /var/www/filterphone/
    ls -lh /var/www/filterphone/
    ;;
status)
    echo "גרסה: $(cat "$A/src/REVISION" 2>/dev/null || echo '?') · קודמת: $(cat "$A/src.prev/REVISION" 2>/dev/null || echo '-')"
    for s in fp-engine fp-proxy fp-api fp-wabot adguardhome caddy redis-server wg-quick@wg0; do
        printf '  %-16s %s\n' "$s" "$(systemctl is-active "$s" || true)"
    done
    df -h / | tail -1
    echo "גיבויים אחרונים:"
    sudo -n ls -1t /root/fp-backups | head -5
    ;;
wabot)
    echo "== fp-wabot"
    systemctl status fp-wabot --no-pager 2>&1 | head -12 || true
    echo "== יומן הבוט (150 שורות אחרונות)"
    sudo -n journalctl -u fp-wabot -n 150 --no-pager -o short-iso 2>&1 | tail -150 || true
    echo "== Chrome"
    sudo -n cat /etc/filterphone/wabot.env 2>/dev/null | grep -E '^CHROME_(PATH|ALT)=' || true
    snap list chromium 2>/dev/null | tail -1 || true
    echo "== הבוט הישן (pm2)"
    bash -lc 'pm2 jlist' 2>/dev/null | python3 -c '
import json, sys
raw = sys.stdin.read(); i = raw.find("[")
for a in (json.loads(raw[i:]) if i >= 0 else []):
    e = a.get("pm2_env", {})
    print(a.get("name"), e.get("status"), e.get("pm_cwd"), "restarts:", e.get("restart_time"))
' || echo "pm2 לא זמין"
    ls -d /home/ubuntu/filterphone-bot/.wwebjs_auth/session-* 2>/dev/null || true
    ;;
legacy-bot)
    L=/home/ubuntu/filterphone-bot
    [ -f "$L/index.js" ] || { echo "הבוט הישן לא נמצא ב-$L"; exit 2; }
    case "${arg:-}" in
    start) bash -lc "cd $L && (pm2 describe filterphone-bot >/dev/null 2>&1 && pm2 restart filterphone-bot || pm2 start index.js --name filterphone-bot) && pm2 save" ;;
    stop)  bash -lc "pm2 stop filterphone-bot && pm2 save" || true ;;
    logs)  bash -lc "pm2 logs filterphone-bot --lines 80 --nostream" 2>&1 | tail -100 || true ;;
    *)     echo "start / stop / logs"; exit 2 ;;
    esac
    ;;
devices)
    echo "== מכשירים (24 השעות האחרונות / פעילים)"
    sudo -n python3 - <<'PY' || true
import json, sqlite3, subprocess, time
now = int(time.time())
hs = {}
try:
    out = subprocess.run(["wg", "show", "wg0", "latest-handshakes"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        k, _, t = line.partition("\t")
        hs[k.strip()] = int(t or 0)
except Exception as e:
    print("wg:", e)
ago = lambda t: "-" if not t else f"{(now - int(t)) // 60}m"
db = sqlite3.connect("file:/var/lib/filterphone/filterphone.db?mode=ro", uri=True)
db.row_factory = sqlite3.Row
rows = db.execute("SELECT * FROM devices WHERE status IN ('active','pending') OR last_seen_at > ? "
                  "ORDER BY COALESCE(heartbeat_at, last_seen_at, 0) DESC LIMIT 15", (now - 86400,)).fetchall()
for d in rows:
    d = dict(d)
    hb = {}
    try:
        hb = json.loads(d.get("heartbeat") or "{}")
    except Exception:
        pass
    keep = {k: hb.get(k) for k in ("app_version", "via_vpn", "vpn", "accessibility", "device_owner", "guard", "policy_version",
                                   "policy_error", "always_on", "lockdown", "error", "network") if k in hb}
    h = hs.get(d["public_key"], 0)
    print(f"#{d['id']} {d['name']!r} status={d['status']}/{d.get('disabled_reason','')} app={d['app_version']} "
          f"seen={ago(d['last_seen_at'])} hb={ago(d.get('heartbeat_at'))} wg={ago(h) if h else 'never'} "
          f"vpn_ip={'yes' if d['vpn_ip'] else 'no'} policy={d.get('policy_version')}/{d.get('device_policy_version')} "
          f"pause={'yes' if (d.get('pause_until') or 0) > now else 'no'} unmanage={'yes' if d.get('unmanage_at') else 'no'}")
    print("    hb:", json.dumps(keep, ensure_ascii=False)[:500])
peers = set(hs)
known = {r[0] for r in db.execute("SELECT public_key FROM devices WHERE status = 'active'")}
print(f"== WireGuard: {len(peers)} peers בשרת, {len(known)} מכשירים פעילים, חסרים ב-wg: {len(known - peers)}")
PY
    echo "== fp-api — שגיאות ותשובות 4xx/5xx אחרונות"
    sudo -n journalctl -u fp-api --since "-3h" --no-pager -o short-iso 2>&1 \
        | grep -E 'ERROR|Traceback|Exception| (4[0-9]{2}|5[0-9]{2}) |WARNING' | grep -v -E '/admin|/v1/bot/' | tail -60 || true
    echo "== fp-api — בקשות מכשירים אחרונות"
    sudo -n journalctl -u fp-api --since "-1h" --no-pager -o short-iso 2>&1 | grep -E '"(GET|POST|PUT) /v1/' \
        | grep -v -E '/admin|/v1/bot/' | sed -E 's/[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/x.x.x.x/g' | tail -40 || true
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
