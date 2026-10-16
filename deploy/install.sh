#!/usr/bin/env bash
# =====================================================================
# FilterPhone — התקנה / עדכון של השרת.
# רץ כ-root מתוך עותק של המאגר בשרת (deploy/deploy.sh מעלה ומריץ אותו).
# בטוח להרצה חוזרת: כל שלב בודק אם כבר בוצע.
# =====================================================================
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP=/opt/filterphone
VENV=$APP/venv
DATA=/var/lib/filterphone
ETC=/etc/filterphone
BACKUP=/root/fp-backups/$(date +%Y%m%d-%H%M%S)
USER_NAME=filterphone

log()  { printf '\033[1;34m[%s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }
warn() { printf '\033[1;33m[אזהרה]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[שגיאה]\033[0m %s\n' "$*"; exit 1; }

[ "$(id -u)" = 0 ] || die "צריך להריץ כ-root"
mkdir -p "$BACKUP"

# ------------------------------------------------------------ 1. משתמש ותיקיות
log "משתמש מערכת ותיקיות"
id "$USER_NAME" &>/dev/null || useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin "$USER_NAME"
install -d -o "$USER_NAME" -g "$USER_NAME" -m 750 "$DATA" "$DATA/models" "$DATA/mitmproxy"
install -d -m 755 "$APP"
install -d -m 750 -g "$USER_NAME" "$ETC"

# ------------------------------------------------------------ 1א. גיבוי לפני שינויים
# מסד הנתונים (מכשירים, לקוחות, בקשות) וההגדרות — כדי שאפשר יהיה לחזור לגרסה הקודמת
# (deploy/rollback_version.sh). גיבוי "חי" של SQLite: עקבי גם כשה-API רץ.
if [ -f "$DATA/filterphone.db" ]; then
    log "גיבוי מסד הנתונים → $BACKUP/filterphone.db"
    python3 - "$DATA/filterphone.db" "$BACKUP/filterphone.db" <<'PY'
import sqlite3, sys
src, dst = sqlite3.connect(sys.argv[1], timeout=30), sqlite3.connect(sys.argv[2])
src.backup(dst)
n = dst.execute("SELECT count(*) FROM devices").fetchone()[0]
dst.close(); src.close()
print(f"  {n} מכשירים בגיבוי")
PY
    chmod 600 "$BACKUP/filterphone.db"
    chown "$USER_NAME:$USER_NAME" "$DATA"/filterphone.db* 2>/dev/null || true
fi
[ -f "$ETC/filterphone.env" ] && cp -a "$ETC/filterphone.env" "$BACKUP/filterphone.env"

# ------------------------------------------------------------ 2. חבילות מערכת
log "חבילות מערכת"
export DEBIAN_FRONTEND=noninteractive
apt-get install -y -q python3.12-venv python3-yaml python3-bcrypt wireguard-tools redis-server curl gpg >/dev/null
if ! command -v caddy >/dev/null; then
    log "התקנת Caddy (שרת HTTPS עם תעודות אוטומטיות)"
    curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
        | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt \
        > /etc/apt/sources.list.d/caddy-stable.list
    apt-get update -q >/dev/null
    apt-get install -y -q caddy >/dev/null
fi

# ------------------------------------------------------------ 3. קוד וסביבת פייתון
log "סביבת פייתון והתקנת הקוד"
[ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q --upgrade "$SRC/server"
install -m 644 "$SRC/deploy/mitm_addon.py" "$APP/mitm_addon.py"
"$VENV/bin/python" -c "from PIL import features; import sys; sys.exit(0 if features.check('avif') else 1)" \
    || warn "Pillow בלי תמיכה ב-AVIF — תמונות AVIF יטופלו לפי מדיניות 'לא ניתן לבדוק'"

# ------------------------------------------------------------ 4. מודלים
log "מודלים (עם אימות חתימה)"
"$VENV/bin/python" "$SRC/tools/fetch_models.py" "$DATA/models"
chown -R "$USER_NAME:$USER_NAME" "$DATA/models"
[ -f "$VENV/lib/python3.12/site-packages/filterphone/engine/heads.json" ] \
    || die "heads.json חסר — צריך להריץ קודם את האימון ב-GitHub (workflow: heads)"

# ------------------------------------------------------------ 5. תעודת הסינון (CA)
CA_DIR=$DATA/mitmproxy
if [ ! -f "$CA_DIR/mitmproxy-ca.pem" ]; then
    log "יצירת תעודת FilterPhone CA חדשה"
    tmp=$(mktemp -d)
    openssl req -x509 -new -nodes -newkey rsa:2048 -sha256 -days 3650 \
        -subj "/C=IL/O=FilterPhone/CN=FilterPhone CA" \
        -addext "basicConstraints=critical,CA:TRUE" \
        -addext "keyUsage=critical,keyCertSign,cRLSign" \
        -addext "subjectKeyIdentifier=hash" \
        -keyout "$tmp/key.pem" -out "$tmp/cert.pem" 2>/dev/null
    cat "$tmp/key.pem" "$tmp/cert.pem" > "$CA_DIR/mitmproxy-ca.pem"
    cp "$tmp/cert.pem" "$CA_DIR/mitmproxy-ca-cert.pem"
    openssl x509 -in "$tmp/cert.pem" -outform DER -out "$CA_DIR/mitmproxy-ca-cert.cer"
    rm -rf "$tmp"
    chown -R "$USER_NAME:$USER_NAME" "$CA_DIR"
    chmod 600 "$CA_DIR/mitmproxy-ca.pem"
    chmod 644 "$CA_DIR/mitmproxy-ca-cert.pem" "$CA_DIR/mitmproxy-ca-cert.cer"
fi

# ------------------------------------------------------------ 6. הגדרות
if [ ! -f "$ETC/filterphone.env" ]; then
    install -m 640 -g "$USER_NAME" "$SRC/deploy/filterphone.env.example" "$ETC/filterphone.env"
fi
set -a; . "$ETC/filterphone.env"; set +a
# כתובת IP חלופית שהטלפונים מקבלים (כשאין DNS מיד אחרי אתחול) — מוסיפים פעם אחת אם חסרה
if ! grep -q '^FP_PUBLIC_IP=' "$ETC/filterphone.env"; then
    ep="${FP_VPN_ENDPOINT:-vpn.filterphone.com:51820}"
    ip4=$(getent ahostsv4 "${ep%%:*}" 2>/dev/null | awk 'NR==1{print $1}' || true)
    if [[ "$ip4" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        printf '\n# נוסף אוטומטית ב-install.sh (%s) — כתובת השרת לטלפונים כשה-DNS לא זמין\nFP_PUBLIC_IP=%s\n' \
            "$(date +%F)" "$ip4" >> "$ETC/filterphone.env"
        export FP_PUBLIC_IP="$ip4"
        log "  FP_PUBLIC_IP=$ip4 (מ-${ep%%:*})"
    fi
fi

# טוקן לבוט הוואטסאפ (שירות fp-wabot ↔ ה-API) — נוצר פעם אחת
if ! grep -q '^FP_WA_BOT_TOKEN=.' "$ETC/filterphone.env"; then
    sed -i '/^FP_WA_BOT_TOKEN=[[:space:]]*$/d' "$ETC/filterphone.env"
    FP_WA_BOT_TOKEN=$(openssl rand -hex 32)
    printf '\n# נוצר אוטומטית ב-install.sh (%s) — הטוקן של בוט הוואטסאפ (fp-wabot)\nFP_WA_BOT_TOKEN=%s\n' \
        "$(date +%F)" "$FP_WA_BOT_TOKEN" >> "$ETC/filterphone.env"
    export FP_WA_BOT_TOKEN
    log "  נוצר טוקן לבוט הוואטסאפ"
fi

# ------------------------------------------------------------ 7. Redis
log "Redis: תקרת זיכרון 512MB, פינוי רק של רשומות קאש (עם תפוגה)"
redis-cli CONFIG SET maxmemory 512mb >/dev/null
redis-cli CONFIG SET maxmemory-policy volatile-lru >/dev/null
redis-cli CONFIG REWRITE >/dev/null || warn "CONFIG REWRITE נכשל — ההגדרה תחזיק עד אתחול Redis"

# ------------------------------------------------------------ 8. מעבר מהמערכת הישנה (פעם אחת)
if [ ! -f "$DATA/.legacy-migrated" ]; then
    log "מעבר מהמערכת הישנה"
    cp -a /etc/wireguard/wg0.conf "$BACKUP/wg0.conf"
    systemctl disable --now filterphone-mitm.service 2>/dev/null || true
    # ייבוא הלקוחות הישנים למסד הנתונים החדש
    "$VENV/bin/python" -m filterphone.api.cli import-legacy - < /etc/wireguard/wg0.conf
    chown "$USER_NAME:$USER_NAME" "$DATA"/filterphone.db*
    # wg0.conf נשאר רק עם [Interface] (שלב 9); את הלקוחות ה-API מוסיף בעלייה (מקור אמת אחד)
    # PM2: ה-API והממשק הישנים מוחלפים — עוצרים (filtertube-api נשאר)
    sudo -u ubuntu pm2 stop filterphone-server filterphone-ui >/dev/null 2>&1 || true
    sudo -u ubuntu pm2 save >/dev/null 2>&1 || true
    touch "$DATA/.legacy-migrated"
fi

# wg0.conf: רק [Interface] + MTU קבוע (בטוח להרצה חוזרת)
cp -a /etc/wireguard/wg0.conf "$BACKUP/wg0.conf.pre-strip"
python3 "$SRC/deploy/wg_strip_peers.py" /etc/wireguard/wg0.conf
ip link set wg0 mtu 1420 2>/dev/null || true

# ------------------------------------------------------------ 9. Docker (לא בשימוש)
if systemctl is-enabled docker.service &>/dev/null; then
    if [ -z "$(docker ps -aq 2>/dev/null)" ]; then
        log "Docker לא בשימוש — מכבה (חוסך זיכרון)"
        systemctl disable --now docker.service docker.socket containerd.service >/dev/null 2>&1 || true
    fi
fi

# ------------------------------------------------------------ 10. חומת אש
log "חומת אש"
cp -a /etc/iptables/rules.v4 "$BACKUP/rules.v4"
bash "$SRC/deploy/firewall.sh"

# ------------------------------------------------------------ 11. AdGuard
log "AdGuard: חיפוש בטוח, חסימת עקיפות DNS, פינוי פורט 443"
cp -a /opt/AdGuardHome/AdGuardHome.yaml "$BACKUP/AdGuardHome.yaml"
AG_ARGS=()
if [ -z "${FP_ADGUARD_PASSWORD:-}" ]; then
    # חסימות ה-DNS לפי מכשיר: ה-API צריך גישה ל-AdGuard. יוצרים לו משתמש משלו עם סיסמה אקראית
    # (המשתמש של בעל השרת לא משתנה). הסיסמה נשמרת רק בקובץ ההגדרות של השרת.
    AG_USER=filterphone-api
    AG_PASSWORD=$(openssl rand -hex 24)
    if AG_HASH=$(AG_PW="$AG_PASSWORD" python3 -c 'import bcrypt, os; print(bcrypt.hashpw(os.environ["AG_PW"].encode(), bcrypt.gensalt(10)).decode())'); then
        AG_ARGS=(--api-user "$AG_USER" --api-hash "$AG_HASH")
    else
        warn "python3-bcrypt חסר — בלי משתמש API ב-AdGuard (החסימות לפי מכשיר רק בפרוקסי)"
    fi
fi
ag_restore() {   # ה-DNS של כל הלקוחות תלוי ב-AdGuard: בכל תקלה — חוזרים להגדרות הקודמות ומפעילים
    systemctl stop adguardhome || true
    cp -a "$BACKUP/AdGuardHome.yaml" /opt/AdGuardHome/AdGuardHome.yaml
    systemctl start adguardhome
}
# AdGuard הוא ה-DNS של כל הטלפונים: עצירה שלו = "אין אינטרנט" בכל הטלפונים עד שהוא עולה.
# לכן קודם מריצים את העדכון על עותק — ואם ההגדרות כבר נכונות, לא נוגעים בו בכלל.
ag_out=""
ag_tmp=$(mktemp)
cp -a /opt/AdGuardHome/AdGuardHome.yaml "$ag_tmp"
if systemctl is-active --quiet adguardhome \
    && python3 "$SRC/deploy/adguard_patch.py" "$ag_tmp" "${AG_ARGS[@]}" >/dev/null 2>&1 \
    && python3 -c 'import sys, yaml; a, b = (yaml.safe_load(open(p, encoding="utf-8")) for p in sys.argv[1:3]); sys.exit(a != b)' \
        /opt/AdGuardHome/AdGuardHome.yaml "$ag_tmp"; then
    echo "  AdGuard: ההגדרות כבר עדכניות — בלי הפעלה מחדש (ה-DNS של הטלפונים לא נקטע)"
else
    systemctl stop adguardhome
    if ! ag_out=$(python3 "$SRC/deploy/adguard_patch.py" /opt/AdGuardHome/AdGuardHome.yaml "${AG_ARGS[@]}"); then
        rm -f "$ag_tmp"
        ag_restore
        die "עדכון הגדרות AdGuard נכשל — ההגדרות הקודמות שוחזרו ו-AdGuard פועל"
    fi
    echo "$ag_out"
    systemctl start adguardhome
    sleep 3
    if ! systemctl is-active --quiet adguardhome; then
        warn "AdGuard לא עלה עם ההגדרות החדשות — משחזר את הקודמות"
        ag_restore
        ag_out=""
    fi
fi
rm -f "$ag_tmp"
if [ ${#AG_ARGS[@]} -gt 0 ] && grep -q "משתמש API" <<<"$ag_out"; then
    AG_URL=$(python3 "$SRC/deploy/adguard_patch.py" /opt/AdGuardHome/AdGuardHome.yaml --print-url)
    {
        echo
        echo "# נוצר אוטומטית ב-install.sh ($(date +%F)) — משתמש AdGuard של ה-API (חסימות DNS לפי מכשיר)"
        echo "FP_ADGUARD_URL=$AG_URL"
        echo "FP_ADGUARD_USER=$AG_USER"
        echo "FP_ADGUARD_PASSWORD=$AG_PASSWORD"
    } >> "$ETC/filterphone.env"
    export FP_ADGUARD_URL="$AG_URL" FP_ADGUARD_USER="$AG_USER" FP_ADGUARD_PASSWORD="$AG_PASSWORD"
    log "  חסימות DNS לפי מכשיר: מחובר ל-AdGuard ($AG_URL)"
fi

# ------------------------------------------------------------ 12. שירותים
log "שירותי systemd"
install -m 644 "$SRC"/deploy/systemd/*.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable fp-engine.service fp-proxy.service fp-api.service >/dev/null
# המנוע והפרוקסי הם שרשרת התעבורה של הטלפונים: כל HTTPS של טלפון עובר בפרוקסי. הפעלה מחדש שלהם
# מנתקת לרגע את האינטרנט בכל הטלפונים — לכן רק כשהקוד / ההגדרות שלהם באמת השתנו (העלאה של API,
# דשבורד או בוט בלבד לא נוגעת בהם). הטביעה: הקבצים שלהם + התלויות + קובץ ההגדרות של השרת.
DATAPATH_STAMP=$DATA/.datapath.sha256
datapath_hash=$( { (cd "$SRC" && find server/filterphone/proxy server/filterphone/engine \
        server/filterphone/settings.py server/filterphone/protocol.py server/filterphone/metrics.py \
        server/filterphone/sdnotify.py server/filterphone/logs.py server/filterphone/__init__.py \
        server/pyproject.toml deploy/systemd/fp-engine.service deploy/systemd/fp-proxy.service \
        -type f ! -name '*.pyc' ! -path '*/__pycache__/*' -print0 | sort -z | xargs -0 sha256sum)
    sha256sum "$ETC/filterphone.env"; } | sha256sum | cut -d' ' -f1)
if [ "$datapath_hash" != "$(cat "$DATAPATH_STAMP" 2>/dev/null || true)" ] \
    || ! systemctl is-active --quiet fp-engine.service || ! systemctl is-active --quiet fp-proxy.service; then
    systemctl restart fp-engine.service
    for _ in $(seq 1 60); do [ -S /run/filterphone/engine.sock ] && break; sleep 1; done
    [ -S /run/filterphone/engine.sock ] || die "המנוע לא עלה — journalctl -u fp-engine"
    systemctl restart fp-proxy.service
    echo "$datapath_hash" > "$DATAPATH_STAMP"
    echo "  מנוע ופרוקסי: עודכנו והופעלו מחדש"
else
    echo "  מנוע ופרוקסי: בלי שינוי — לא מופעלים מחדש (האינטרנט של הטלפונים לא נקטע)"
fi
systemctl restart fp-api.service

# ------------------------------------------------------------ 12א. בוט הוואטסאפ (לא עוצר את ההתקנה)
log "בוט הוואטסאפ (fp-wabot)"
bash "$SRC/deploy/wabot_install.sh" || warn "בוט הוואטסאפ לא הותקן — שאר השרת תקין. פרטים למעלה / journalctl -u fp-wabot"

# ------------------------------------------------------------ 13. Caddy
log "Caddy (HTTPS ל-${FP_PUBLIC_HOST:-api.filterphone.com}) ודף הורדת האפליקציה"
install -d -m 755 /var/www/filterphone
install -m 644 "$SRC/deploy/www/index.html" /var/www/filterphone/index.html
sed "s/__PUBLIC_HOST__/${FP_PUBLIC_HOST:-api.filterphone.com}/" "$SRC/deploy/Caddyfile" > /etc/caddy/Caddyfile
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null
systemctl enable caddy >/dev/null
# טעינה מחדש "חלקה" (בלי לנתק חיבורים) — Caddy מגיש גם את ה-API שהאפליקציה מדברת איתו
if systemctl is-active --quiet caddy; then systemctl reload caddy; else systemctl restart caddy; fi

# ------------------------------------------------------------ 13א. ערוץ העלאה מ-GitHub
# הפקודה היחידה שמפתח ההעלאה של GitHub Actions מורשה להריץ (forced command) — ראו deploy/ci_deploy.sh
install -m 755 -o root -g root "$SRC/deploy/ci_deploy.sh" /usr/local/sbin/fp-ci-deploy

# ------------------------------------------------------------ 14. בדיקות
log "בדיקות"
sleep 3
ok=1
for s in fp-engine fp-proxy fp-api adguardhome caddy redis-server wg-quick@wg0; do
    if systemctl is-active --quiet "$s"; then echo "  ✓ $s"; else echo "  ✗ $s"; ok=0; fi
done
curl -fsS http://127.0.0.1:8090/health >/dev/null && echo "  ✓ API עונה" || { echo "  ✗ API"; ok=0; }
systemctl is-active --quiet fp-wabot && echo "  ✓ fp-wabot (בוט וואטסאפ)" || echo "  · fp-wabot לא פעיל (לא חוסם)"
ss -ltn | grep -q ':8080 ' && echo "  ✓ פרוקסי מאזין על 8080" || { echo "  ✗ פרוקסי"; ok=0; }
dig +short +time=2 @127.0.0.1 example.com >/dev/null && echo "  ✓ DNS עונה" || { echo "  ✗ DNS"; ok=0; }
echo "  גיבויים: $BACKUP"
[ "$ok" = 1 ] && log "ההתקנה הסתיימה בהצלחה" || die "חלק מהבדיקות נכשלו"
