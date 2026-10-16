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
#   net                אבחון שרשרת התעבורה של הטלפונים (DNS, פרוקסי, NAT, תעבורה לכל מכשיר)
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
net)
    # אבחון שרשרת התעבורה של הטלפונים (לקריאה בלבד): DNS → פרוקסי → NAT, ותעבורה אמיתית לכל מכשיר.
    # handshake טרי לבד לא מוכיח שיש אינטרנט — כאן רואים איפה התעבורה נעצרת.
    sudo -n python3 - <<'PY' || true
import collections, re, sqlite3, subprocess, time

def sh(*a):
    return subprocess.run(list(a), capture_output=True, text=True).stdout

now = time.time()
print("== ip_forward:", sh("sysctl", "-n", "net.ipv4.ip_forward").strip())
wgip = (re.search(r"inet (\d+\.\d+\.\d+\.\d+)", sh("ip", "-4", "-o", "addr", "show", "wg0")) or [None, "10.8.0.1"])[1]
print(f"== DNS דרך AdGuard ({wgip}) — כמו שהטלפון שואל:")
for n in ("google.com", "api.filterphone.com", "www.whatsapp.com", "play.googleapis.com", "connectivitycheck.gstatic.com"):
    t = time.time()
    r = sh("dig", "+short", "+time=2", "+tries=1", f"@{wgip}", n, "A").strip().splitlines()
    print(f"   {n:32s} → {(r[-1] if r else '(אין תשובה)'):18s} {int((time.time() - t) * 1000)}ms")

print("== שירותים (מתי עלו / כמה הפעלות מחדש):")
for s in ("fp-proxy", "adguardhome", "fp-api", "caddy", "redis-server", "wg-quick@wg0"):
    since = sh("systemctl", "show", "-p", "ActiveEnterTimestamp", "--value", s).strip()
    nr = sh("systemctl", "show", "-p", "NRestarts", "--value", s).strip()
    print(f"   {s:14s} since={since} restarts={nr}")

db = sqlite3.connect("file:/var/lib/filterphone/filterphone.db?mode=ro", uri=True)
db.row_factory = sqlite3.Row
devs = {r["public_key"]: dict(r) for r in db.execute(
    "SELECT id, name, vpn_ip, public_key, app_version FROM devices WHERE status = 'active'")}
tr, hs = {}, {}
for line in sh("wg", "show", "wg0", "transfer").splitlines():
    k, rx, tx = line.split("\t")
    tr[k] = (int(rx), int(tx))
for line in sh("wg", "show", "wg0", "latest-handshakes").splitlines():
    k, _, t = line.partition("\t")
    hs[k] = int(t or 0)
learned = collections.Counter(f.split("|", 1)[0] for f in sh("redis-cli", "--raw", "HKEYS", "fp:pt").splitlines() if "|" in f)
print("== תעבורה במנהרה לכל מכשיר (מהטלפון → / ← אל הטלפון), handshake, מעקפים שנלמדו:")
for k, d in sorted(devs.items(), key=lambda kv: -hs.get(kv[0], 0))[:12]:
    rx, tx = tr.get(k, (0, 0))
    h = hs.get(k, 0)
    print(f"   #{d['id']:<3} {d['name'][:18]!r:20s} app={d['app_version']:8s} {d['vpn_ip']:12s} "
          f"from_phone={rx / 1e6:8.2f}MB to_phone={tx / 1e6:8.2f}MB hs={(str(int((now - h) // 60)) + 'm') if h else 'never':>6s} "
          f"learned={learned.get(d['vpn_ip'], 0)}")
print(f"== Redis: fp:pt={sum(learned.values())} מעקפים | uptime={sh('redis-cli', 'INFO', 'server').split('uptime_in_seconds:')[-1].split()[0]}s")

# ציר זמן לכל מכשיר פעיל לאחרונה (24 שעות): VPN למטה/למעלה, החלפת מפתח, עדכון אפליקציה, "מחוץ ל-VPN"...
try:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Asia/Jerusalem")
except Exception:
    tz = None
def il(ts):
    return datetime.fromtimestamp(ts, tz).strftime("%d/%m %H:%M") if tz else time.strftime("%d/%m %H:%M", time.gmtime(ts))
print("== ציר זמן לכל מכשיר (24 שעות אחרונות, שעון ישראל):")
recent = [d for k, d in sorted(devs.items(), key=lambda kv: -hs.get(kv[0], 0)) if now - hs.get(k, 0) < 86400][:5]
for d in recent:
    rows = db.execute("SELECT ts, kind, detail FROM device_log WHERE device_id = ? AND ts > ? ORDER BY ts",
                      (d["id"], int(now - 86400))).fetchall()
    print(f"   #{d['id']} {d['name']!r} — {len(rows)} אירועים")
    for r in rows[-40:]:
        print(f"      {il(r['ts'])}  {r['kind']:22s} {r['detail'][:110]}")

print("== NAT PREROUTING (מונים):")
print(sh("iptables", "-t", "nat", "-L", "PREROUTING", "-v", "-n", "-x").rstrip())
print("== FORWARD (מונים):")
print(sh("iptables", "-L", "FORWARD", "-v", "-n", "-x").rstrip())

log = sh("journalctl", "-u", "fp-proxy", "--since", "-3h", "--no-pager", "-o", "cat")
cats, hosts, per_ip = collections.Counter(), collections.Counter(), collections.Counter()
samples = []
for line in log.splitlines():
    low = line.lower()
    if "handshake failed" in low or "tls" in low and ("fail" in low or "error" in low):
        if any(m in low for m in ("does not trust", "certificate unknown", "unknown ca", "bad certificate",
                                  "certificate_unknown", "unknown_ca", "bad_certificate")):
            c = "distrust→learned"
        elif any(m in low for m in ("disconnect", "reset", "eof", "closed", "timeout", "timed out")):
            c = "client-closed→NOT-learned"
        elif "server" in low:
            c = "server-side"
        else:
            c = "other-tls"
        cats[c] += 1
        m = re.search(r"\bfor ([a-z0-9][a-z0-9.-]*\.[a-z]{2,})", low) or re.search(r"([a-z0-9-]+(?:\.[a-z0-9-]+)+\.[a-z]{2,})", low)
        if m:
            hosts[(c, m.group(1))] += 1
        ip = re.search(r"\b(10\.\d+\.\d+\.\d+)\b", line)
        if ip:
            per_ip[ip.group(1)] += 1
        if len(samples) < 12 and c != "distrust→learned":
            samples.append(line[:260])
    elif "traceback" in low or "exception" in low or " error" in low:
        cats["error/exception"] += 1
        if len(samples) < 20:
            samples.append(line[:260])
print("== פרוקסי (3 שעות) — כשלים לפי סוג:", dict(cats))
print("== פרוקסי — אתרים מובילים בכשל:")
for (c, h), n in hosts.most_common(25):
    print(f"   {n:6d}  {c:26s} {h}")
print("== פרוקסי — כשלים לפי כתובת VPN:", dict(per_ip.most_common(10)))
print("== פרוקסי — דוגמאות שורות (לא 'distrust'):")
for s in samples:
    print("   ", s)
PY
    echo "== Caddy — שגיאות אחרונות"
    sudo -n journalctl -u caddy --since "-3h" --no-pager -o cat 2>&1 | grep -iE '"level":"error"|error' | tail -15 || true
    echo "== fp-api — אזהרות ושגיאות (3 שעות)"
    sudo -n journalctl -u fp-api --since "-3h" --no-pager -o cat 2>&1 | grep -E 'WARNING|ERROR|Traceback' | tail -25 || true
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
