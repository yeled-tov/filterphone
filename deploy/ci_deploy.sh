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
#   images             אבחון סינון התמונות (לקריאה בלבד, בלי פרטי לקוחות): מדדי המנוע והפרוקסי,
#                      מונים לכל מכשיר, סיבות חסימה, ומדידה חיה — מקומי מול שרת ה-GPU (Modal)
#   web                אבחון רמות הגלישה (לקריאה בלבד, בלי פרטי לקוחות): רשימת האתרים למבוגרים,
#                      פסקי דין, בקשות 'שליחה לבדיקה', מכשירים לפי רמה, ובדיקה חיה של הבוט
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
    echo "== קול, תמונות ו-AI (7 ימים; בלי מספרי טלפון)"
    sudo -n journalctl -u fp-wabot --since "-7d" --no-pager -o short-iso 2>&1 \
        | grep -E 'voice|image|media \(|AI:|reply:|message:|unhandled' | sed -E 's/972[0-9]{7,}/972…/g' | tail -40 || true
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
import json, re, sqlite3, subprocess, time
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
    keep = {k: hb.get(k) for k in ("app_version", "via_vpn", "vpn", "vpn_bypass", "dpc", "accessibility", "protections",
                                   "device_owner", "guard", "policy_version", "policy_error", "always_on", "lockdown",
                                   "error", "network", "private_dns", "update", "background_restricted",
                                   "battery_unrestricted") if k in hb}
    try:
        pol = json.loads(d.get("policy") or "{}")
        keep["policy_dpc"] = {k: v for k, v in (pol.get("dpc") or {}).items() if k in ("always_on_vpn",)}
        if pol.get("allow_google_accounts"):
            keep["policy_google_accounts"] = True
    except Exception:
        pass
    caps = hb.get("capabilities") or {}
    keep["android"] = {"sdk": hb.get("sdk") or caps.get("sdk"), "maker": caps.get("manufacturer"), "model": caps.get("model")}
    h = hs.get(d["public_key"], 0)
    print(f"#{d['id']} {d['name']!r} status={d['status']}/{d.get('disabled_reason','')} app={d['app_version']} "
          f"seen={ago(d['last_seen_at'])} hb={ago(d.get('heartbeat_at'))} wg={ago(h) if h else 'never'} "
          f"vpn_ip={'yes' if d['vpn_ip'] else 'no'} policy={d.get('policy_version')}/{d.get('device_policy_version')} "
          f"pause={'yes' if (d.get('pause_until') or 0) > now else 'no'} unmanage={'yes' if d.get('unmanage_at') else 'no'}")
    print("    hb:", json.dumps(keep, ensure_ascii=False)[:1600])
    if d.get("vpn_ip"):
        # שרתי Google שהפרוקסי למד לא לפתוח למכשיר (אפליקציה שלא סומכת על התעודה עברה דרך ה-VPN) — שמות ציבוריים
        try:
            g = sorted(f.split("|", 1)[1] for f in subprocess.run(["redis-cli", "--raw", "HKEYS", "fp:pt"],
                       capture_output=True, text=True).stdout.split()
                       if f.startswith(d["vpn_ip"] + "|") and re.search(r"google|gstatic|gvt\d|android\.com", f))
            if g:
                print(f"    google דרך ה-VPN ({len(g)}):", ", ".join(g[:25]))
            # לאילו שרתי וואטסאפ המכשיר מתחבר (3 ימים) — open / blocked ומספר חיבורים
            raw = subprocess.run(["redis-cli", "--raw", "HGETALL", "fp:wa_sni"], capture_output=True, text=True).stdout.split()
            wa = sorted(((f.split("|", 1)[1], int(n)) for f, n in zip(raw[::2], raw[1::2])
                         if f.startswith(d["vpn_ip"] + "|")), key=lambda x: -x[1])
            if wa:
                print("    whatsapp:", ", ".join(f"{h}×{n}" for h, n in wa[:30]))
        except Exception as e:
            print("    redis:", e)
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
learned_keys = {f.lower() for f in sh("redis-cli", "--raw", "HKEYS", "fp:pt").splitlines() if "|" in f}
learned = collections.Counter(f.split("|", 1)[0] for f in learned_keys)
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

# AdGuard: הגבלת הקצב + העומס האמיתי לפי שנייה (מיומן השאילתות). שאילתה שנחסמה בהגבלת הקצב לא
# נרשמת ביומן ולא מקבלת תשובה — רואים אותה כ"תקרה שטוחה": שניות שבהן הסכום נעצר בדיוק על המגבלה.
rl, rl_len = 0, 24
try:
    import yaml
    agdns = (yaml.safe_load(open("/opt/AdGuardHome/AdGuardHome.yaml", encoding="utf-8")) or {}).get("dns") or {}
    rl = int(agdns.get("ratelimit") or 0)
    rl_len = int(agdns.get("ratelimit_subnet_len_ipv4") or 24)
    print("== AdGuard — הגבלת קצב:", {k: agdns.get(k) for k in (
        "ratelimit", "ratelimit_subnet_len_ipv4", "ratelimit_whitelist", "refuse_any", "cache_size",
        "upstream_mode", "max_goroutines")})
    print("   upstream:", agdns.get("upstream_dns"), "| bootstrap:", agdns.get("bootstrap_dns"))
except Exception as e:
    print("== AdGuard — קריאת ההגדרות נכשלה:", e)

# בדיקה חיה של הגבלת הקצב: 80 שאילתות בבת אחת מ-127.0.0.1 — "דלי" נפרד מזה של הטלפונים (10.8.0.x),
# כך שהבדיקה לא גוזלת להם כלום. כמה קיבלו תשובה תוך 2 שניות? (שאילתה שנחסמה בהגבלה — לא נענית בכלל)
import socket, struct
def dns_burst(n=80, server=("127.0.0.1", 53)):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    q = b"".join(bytes([len(p)]) + p.encode() for p in "example.com".split(".")) + b"\0" + struct.pack(">HH", 1, 1)
    t0 = time.time()
    for i in range(n):
        s.sendto(struct.pack(">HHHHHH", i, 0x0100, 1, 0, 0, 0) + q, server)
    sent_in = time.time() - t0
    got = set()
    s.settimeout(0.25)
    while time.time() - t0 < 2.5 and len(got) < n:
        try:
            got.add(struct.unpack(">H", s.recv(4096)[:2])[0])
        except socket.timeout:
            pass
    s.close()
    return len(got), sent_in
try:
    ans, took = dns_burst()
    print(f"== AdGuard — בדיקה חיה: 80 שאילתות ב-{took * 1000:.0f}ms מ-127.0.0.1 → נענו {ans}/80"
          + (" ← הגבלת הקצב זורקת שאילתות" if ans < 80 else " ← בלי זריקות"))
except Exception as e:
    print("== AdGuard — הבדיקה החיה נכשלה:", e)

import calendar, os
ql_path = "/opt/AdGuardHome/data/querylog.json"
q_since = now - 86400
per_sec, per_sec_ip, per_hour_ip, sec_hosts = (collections.Counter(), collections.Counter(),
                                               collections.Counter(), collections.defaultdict(collections.Counter))
ts_cache = {}
def q_ts(t):
    k = t[:19] + t[-6:]
    v = ts_cache.get(k)
    if v is None:
        m = re.match(r"(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)(?:\.\d+)?(Z|[+-]\d\d:\d\d)?$", t)
        if not m:
            return None
        v = calendar.timegm(tuple(int(x) for x in m.groups()[:6]))
        tzs = m.group(7) or "Z"
        if tzs != "Z":
            v -= (1 if tzs[0] == "+" else -1) * (int(tzs[1:3]) * 3600 + int(tzs[4:6]) * 60)
        ts_cache[k] = v
    return v
try:
    size = os.path.getsize(ql_path)
    with open(ql_path, "rb") as f:
        if size > 800_000_000:
            f.seek(size - 800_000_000)
            f.readline()
        for raw in f:
            line = raw.decode("utf-8", "replace")
            mt = re.search(r'"T":\s*"([^"]+)"', line)
            mi = re.search(r'"IP":\s*"([^"]+)"', line)
            if not mt or not mi:
                continue
            ts = q_ts(mt.group(1))
            if ts is None or ts < q_since:
                continue
            ip = mi.group(1)
            if not ip.startswith("10."):
                continue
            s = int(ts)
            per_sec[s] += 1
            per_sec_ip[(s, ip)] += 1
            per_hour_ip[(s // 3600, ip)] += 1
            mh = re.search(r'"QH":\s*"([^"]+)"', line)
            if mh:
                sec_hosts[s][(ip, mh.group(1))] += 1
    print(f"== AdGuard — יומן שאילתות: {size / 1e6:.0f}MB, {sum(per_sec.values())} שאילתות מהטלפונים ב-24 שעות")
except FileNotFoundError:
    print("== AdGuard — אין יומן שאילתות בקובץ", ql_path)
except Exception as e:
    print("== AdGuard — קריאת יומן השאילתות נכשלה:", e)

if per_sec:
    cap = rl if rl else None
    ips = [d["vpn_ip"] for d in recent[:4]]
    names = {d["vpn_ip"]: f"#{d['id']}" for d in recent}
    print(f"== DNS לפי שעה (שעון ישראל) — כל הטלפונים יחד: שאילתות / שיא לשנייה / שניות על התקרה ({cap})"
          + "".join(f" | {names[i]}: שאילתות שיא" for i in ips))
    hours = sorted({s // 3600 for s in per_sec})
    for h in hours[-24:]:
        secs = [n for s, n in per_sec.items() if s // 3600 == h]
        sat = sum(1 for n in secs if cap and n >= cap - 1)
        row = f"   {il(h * 3600)}  {sum(secs):6d} {max(secs):4d}/s {sat:4d}"
        for i in ips:
            pk = max((n for (s, ip), n in per_sec_ip.items() if ip == i and s // 3600 == h), default=0)
            row += f" | {per_hour_ip.get((h, i), 0):6d} {pk:3d}/s"
        print(row)
    print("== DNS — השניות העמוסות ביותר (מי שאל ומה):")
    for s, n in per_sec.most_common(8):
        top = ", ".join(f"{names.get(ip, ip)}:{host}×{c}" for (ip, host), c in sec_hosts[s].most_common(5))
        print(f"   {il(s)}:{time.gmtime(s).tm_sec:02d}  {n:4d} שאילתות  — {top[:200]}")

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
        m = re.search(r"\bfor ([a-z0-9][a-z0-9.-]*\.[a-z]{2,})", low) or re.search(r"([a-z0-9-]+(?:\.[a-z0-9-]+)+\.[a-z]{2,})", low)
        ip = re.search(r"\b(10\.\d+\.\d+\.\d+)\b", line)
        if any(m in low for m in ("does not trust", "certificate unknown", "unknown ca", "bad certificate",
                                  "certificate_unknown", "unknown_ca", "bad_certificate")):
            # במצב "תמונות באפליקציות: חסום" שרתי תמונות לא נלמדים בכוונה — כל חיבור אליהם נכשל מחדש
            c = ("distrust→learned" if not (m and ip) or f"{ip.group(1)}|{m.group(1)}" in learned_keys
                 else "distrust→NOT-learned(images)")
        elif any(m in low for m in ("disconnect", "reset", "eof", "closed", "timeout", "timed out")):
            c = "client-closed→NOT-learned"
        elif "server" in low:
            c = "server-side"
        else:
            c = "other-tls"
        cats[c] += 1
        if m:
            hosts[(c, m.group(1))] += 1
        if ip:
            per_ip[ip.group(1)] += 1
        if len(samples) < 12 and not c.startswith("distrust"):
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
web)
    # עם הגדרות השרת (FP_DATA_DIR / FP_REDIS_URL וכו') — כמו השירותים עצמם; כלום מהן לא מודפס
    sudo -n bash -c 'set -a; [ -f /etc/filterphone/filterphone.env ] && . /etc/filterphone/filterphone.env; set +a
        exec "$0" "$1"' "$A/venv/bin/python" "$A/src/deploy/diag_web.py" 2>&1 || true
    echo "== fp-api / fp-proxy — רמות גלישה (3 שעות)"
    sudo -n journalctl -u fp-api -u fp-proxy --since "-3h" --no-pager -o cat 2>&1 | grep -iE 'web|adult|site check|serp' | tail -30 || true
    ;;
images)
    sudo -n "$A/venv/bin/python" "$A/src/deploy/diag_images.py" 2>&1 || true
    echo "== fp-engine — אזהרות ושגיאות (3 שעות)"
    sudo -n journalctl -u fp-engine --since "-3h" --no-pager -o cat 2>&1 | grep -E 'WARNING|ERROR|Traceback|Modal' | tail -30 || true
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
