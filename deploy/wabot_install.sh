#!/usr/bin/env bash
# =====================================================================
# בוט הוואטסאפ (שירות fp-wabot) — חלק מ-install.sh, רץ כ-root.
# תקלה כאן לא עוצרת את ההתקנה של השרת: install.sh רק מזהיר.
#
#   • Node 18+ ו-Chromium (מה שכבר מותקן; אחרת מתקין)
#   • משתמש fpbot עם בית ב-/home (Chromium מ-snap עובד רק כך), הקוד ב-/opt/filterphone/wabot
#   • הטוקן נכתב ב-filterphone.env (install.sh) ומועבר לבוט בקובץ נפרד (wabot.env) בלי שאר הסודות
#   • פעם אחת: מעבר מהבוט הישן (pm2 תחת ubuntu) — עוצר אותו, מעביר את החיבור לוואטסאפ ואת מפתח ה-Groq
# =====================================================================
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP=/opt/filterphone
VENV=$APP/venv
ETC=/etc/filterphone
DATA=/var/lib/filterphone
BOT_USER=fpbot
BOT_HOME=/home/$BOT_USER
BOT_APP=$APP/wabot
BOT_DATA=$BOT_HOME/wabot           # לא תיקייה נסתרת: ל-Chromium מ-snap אין גישה לתיקיות נסתרות בבית
LEGACY=/home/ubuntu/filterphone-bot

log()  { printf '\033[1;34m[%s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }
warn() { printf '\033[1;33m[אזהרה]\033[0m %s\n' "$*"; }

set -a; . "$ETC/filterphone.env"; set +a
[ -n "${FP_WA_BOT_TOKEN:-}" ] || { warn "חסר FP_WA_BOT_TOKEN ב-$ETC/filterphone.env"; exit 1; }

# ------------------------------------------------------------ Node 18+
node_major() { "$1" -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0; }
NODE=$(command -v node || true)
if [ -z "$NODE" ] || [ "$(node_major "$NODE")" -lt 18 ]; then
    log "  מתקין Node.js"
    apt-get install -y -q nodejs npm >/dev/null 2>&1 || apt-get install -y -q nodejs >/dev/null 2>&1 || true
    NODE=$(command -v node || true)
fi
if [ -z "$NODE" ] || [ "$(node_major "$NODE")" -lt 18 ]; then
    snap install node --classic --channel=20/stable >/dev/null 2>&1 || true
    [ -x /snap/bin/node ] && NODE=/snap/bin/node
fi
if [ -z "$NODE" ] || [ "$(node_major "$NODE")" -lt 18 ]; then
    warn "צריך Node 18 ומעלה (יש: $([ -n "$NODE" ] && "$NODE" -v || echo 'אין'))"
    exit 1
fi
NPM="$(dirname "$NODE")/npm"
[ -x "$NPM" ] || NPM=$(command -v npm || true)
[ -n "$NPM" ] || { warn "npm חסר"; exit 1; }
export PATH="$(dirname "$NODE"):$PATH"

# ------------------------------------------------------------ Chromium
find_chrome() {
    local p
    for p in "${CHROME_PATH:-}" /usr/bin/google-chrome /usr/bin/google-chrome-stable /usr/bin/chromium \
             /snap/bin/chromium /usr/bin/chromium-browser; do
        [ -n "$p" ] && [ -x "$p" ] && { echo "$p"; return 0; }
    done
    return 1
}
if ! CHROME=$(find_chrome); then
    log "  מתקין Chromium"
    snap install chromium >/dev/null 2>&1 || apt-get install -y -q chromium >/dev/null 2>&1 || true
    CHROME=$(find_chrome) || { warn "אין Chromium — התקן: sudo snap install chromium"; exit 1; }
fi

# ------------------------------------------------------------ משתמש ותיקיות
id "$BOT_USER" &>/dev/null || useradd --create-home --home-dir "$BOT_HOME" --shell /usr/sbin/nologin --user-group "$BOT_USER"
install -d -o "$BOT_USER" -g "$BOT_USER" -m 700 "$BOT_HOME" "$BOT_DATA" "$BOT_DATA/auth"
loginctl enable-linger "$BOT_USER" >/dev/null 2>&1 || true      # /run/user/<uid> קבוע — Chromium מ-snap צריך
BOT_UID=$(id -u "$BOT_USER")

# ------------------------------------------------------------ הקוד (node_modules נבנה מחדש רק כשהתלויות השתנו)
WANT=$(cat "$SRC/deploy/whatsapp-bot/package-lock.json" "$SRC/deploy/whatsapp-bot/package.json" \
        "$SRC"/deploy/whatsapp-bot/vendor/*.tgz 2>/dev/null | sha256sum | cut -c1-16)
if [ ! -d "$BOT_APP/node_modules" ] || [ "$(cat "$BOT_APP/.deps" 2>/dev/null)" != "$WANT" ]; then
    log "  תלויות הבוט (npm ci)"
    rm -rf "$BOT_APP.new"
    install -d -m 755 "$BOT_APP.new"
    cp "$SRC/deploy/whatsapp-bot/package.json" "$SRC/deploy/whatsapp-bot/package-lock.json" "$BOT_APP.new/"
    # whatsapp-web.js בגרסה קבועה מתוך הפרויקט (vendor/) — בלי תלות ב-GitHub או ב-git בזמן ההתקנה
    [ -d "$SRC/deploy/whatsapp-bot/vendor" ] && cp -r "$SRC/deploy/whatsapp-bot/vendor" "$BOT_APP.new/"
    (cd "$BOT_APP.new" && PUPPETEER_SKIP_DOWNLOAD=1 "$NPM" ci --omit=dev --no-audit --no-fund --loglevel=error >/dev/null)
    echo "$WANT" > "$BOT_APP.new/.deps"
    rm -rf "$BOT_APP.old"
    [ -d "$BOT_APP" ] && mv "$BOT_APP" "$BOT_APP.old"
    mv "$BOT_APP.new" "$BOT_APP"
    rm -rf "$BOT_APP.old"
fi
before=$(cd "$BOT_APP" && cat index.js lib/*.js 2>/dev/null | sha256sum || true)
rm -rf "$BOT_APP/lib"
cp -r "$SRC/deploy/whatsapp-bot/lib" "$BOT_APP/lib"
cp "$SRC/deploy/whatsapp-bot/index.js" "$SRC/deploy/whatsapp-bot/package.json" "$BOT_APP/"
chmod -R a+rX "$BOT_APP"
after=$(cd "$BOT_APP" && cat index.js lib/*.js | sha256sum)

# ------------------------------------------------------------ Chrome יציב (גיבוי) + עצירת עדכונים ברקע
# Chromium מ-snap מתעדכן לבד ברקע — כל עדכון יכול לנתק את הבוט. מקפיאים אותו (snap refresh --hold).
# בנוסף: Chrome for Testing בגרסה קבועה (לא מתעדכן לבד), שהבוט עובר אליו לבד אם Chromium לא מצליח לטעון
# את וואטסאפ ווב כמה פעמים ברצף. מותקן פעם אחת — לא בכל העלאה.
case "$CHROME" in /snap/*|/usr/bin/chromium-browser) snap refresh --hold chromium >/dev/null 2>&1 || true ;; esac
CFT_DIR=$APP/wabot-chrome
CFT=$(ls -d "$CFT_DIR"/chrome/linux*/chrome-linux*/chrome 2>/dev/null | sort -V | tail -1 || true)
if [ -z "$CFT" ] && [ ! -f "$CFT_DIR/.unsupported" ]; then
    log "  Chrome for Testing (גרסה קבועה, גיבוי לבוט)"
    if CFT=$(cd "$BOT_APP" && timeout 900 ./node_modules/.bin/browsers install chrome@stable --path "$CFT_DIR" \
            --install-deps --format '{{path}}' 2>/dev/null | tail -1) && [ -x "$CFT" ]; then
        chmod -R a+rX "$CFT_DIR"
        log "    $CFT"
    else
        warn "Chrome for Testing לא הותקן — הבוט ימשיך עם $CHROME"
        CFT=""
    fi
fi
# חייב לרוץ בפועל על המעבד הזה (בשרת ARM אין Chrome for Testing — מורידים גרסת x64 שלא רצה)
if [ -n "$CFT" ] && ! timeout 30 "$CFT" --headless=new --no-sandbox --version >/dev/null 2>&1; then
    warn "Chrome for Testing לא רץ על השרת הזה ($(uname -m)) — הבוט ימשיך רק עם $CHROME"
    rm -rf "$CFT_DIR"; install -d "$CFT_DIR"; touch "$CFT_DIR/.unsupported"
    CFT=""
fi

# ------------------------------------------------------------ הגדרות לשירות (רק מה שהבוט צריך)
env_new=$(mktemp)
cat > "$env_new" <<EOF
# נוצר ע"י deploy/wabot_install.sh — לא לערוך (נדרס בכל התקנה). ההגדרות של הבוט — בדשבורד.
FP_API=http://127.0.0.1:8090
FP_WA_BOT_TOKEN=$FP_WA_BOT_TOKEN
FP_WABOT_DATA=$BOT_DATA
CHROME_PATH=$CHROME
CHROME_ALT=$CFT
XDG_RUNTIME_DIR=/run/user/$BOT_UID
HOME=$BOT_HOME
NODE_ENV=production
EOF
changed=0
cmp -s "$env_new" "$ETC/wabot.env" 2>/dev/null || changed=1
install -m 600 -o root -g root "$env_new" "$ETC/wabot.env"
rm -f "$env_new"
unit_new=$(mktemp)
sed -e "s#__NODE__#$NODE#" -e "s#__USER__#$BOT_USER#g" "$SRC/deploy/whatsapp-bot/fp-wabot.service" > "$unit_new"
cmp -s "$unit_new" /etc/systemd/system/fp-wabot.service 2>/dev/null || changed=1
install -m 644 "$unit_new" /etc/systemd/system/fp-wabot.service
rm -f "$unit_new"
[ "$before" = "$after" ] || changed=1

# ------------------------------------------------------------ Chromium עולה בתור המשתמש של הבוט?
if ! out=$(timeout 120 runuser -u "$BOT_USER" -- env HOME="$BOT_HOME" XDG_RUNTIME_DIR="/run/user/$BOT_UID" \
        "$CHROME" --headless=new --no-sandbox --disable-gpu --no-first-run --dump-dom about:blank 2>/dev/null) \
        || ! grep -q "<html" <<<"$out"; then
    warn "Chromium ($CHROME) לא עלה בתור $BOT_USER — הבוט ידווח שגיאה בדשבורד. בדיקה: journalctl -u fp-wabot"
fi

# ------------------------------------------------------------ מעבר מהבוט הישן (פעם אחת)
if [ ! -f "$DATA/.wabot-migrated" ]; then
    if [ -d "$LEGACY" ]; then
        log "  מעבר מהבוט הישן ($LEGACY)"
        # עוצרים ומוחקים מ-pm2 כל תהליך שרץ מהתיקייה הישנה (שני בוטים על אותו חיבור — מתנגשים)
        pm2u() { sudo -u ubuntu -H bash -lc "pm2 $*" 2>/dev/null; }     # גם כש-pm2 מותקן דרך nvm
        names=$(pm2u jlist | python3 -c '
import json, sys
raw = sys.stdin.read()
try:
    apps = json.loads(raw[raw.find("["):] if "[" in raw else "[]")   # pm2 לפעמים מדפיס אזהרות לפני ה-JSON
except ValueError:
    apps = []
print(" ".join(a["name"] for a in apps if str(a.get("pm2_env", {}).get("pm_cwd", "")).startswith(sys.argv[1])))
' "$LEGACY" || true)
        for n in $names; do
            if pm2u delete "$n" >/dev/null; then log "    pm2: $n נעצר"; fi
        done
        [ -n "$names" ] && { pm2u save >/dev/null || true; }
        # החיבור לוואטסאפ (כדי לא לסרוק QR שוב). הקבצים הישנים נשארים במקומם.
        moved=0
        if [ -d "$LEGACY/.wwebjs_auth/session-filterphone" ] && [ ! -d "$BOT_DATA/auth/session-filterphone" ]; then
            cp -a "$LEGACY/.wwebjs_auth/session-filterphone" "$BOT_DATA/auth/"
            rm -f "$BOT_DATA"/auth/session-filterphone/Singleton* 2>/dev/null || true
            chown -R "$BOT_USER:$BOT_USER" "$BOT_DATA/auth"
            moved=1
            log "    החיבור לוואטסאפ הועבר"
        fi
        groq=$(sed -n 's/^[[:space:]]*\(export[[:space:]]\+\)\?GROQ_API_KEY[[:space:]]*=[[:space:]]*["'"'"']\?\([^"'"'"'[:space:]]*\).*/\2/p' \
               "$LEGACY/.env" 2>/dev/null | tail -1 || true)
        GROQ="$groq" ENABLE="$moved" "$VENV/bin/python" -m filterphone.api.cli wabot-legacy \
            || warn "ייבוא הגדרות הבוט הישן נכשל — מגדירים בדשבורד"
    fi
    touch "$DATA/.wabot-migrated"
fi

# ------------------------------------------------------------ הפעלה
systemctl daemon-reload
systemctl enable fp-wabot.service >/dev/null
if [ "$changed" = 1 ]; then
    systemctl restart fp-wabot.service
else
    systemctl start fp-wabot.service          # כבר רץ ולא השתנה כלום — לא מנתקים את הוואטסאפ
fi
log "  בוט הוואטסאפ: $(systemctl is-active fp-wabot.service || true) (Node $("$NODE" -v), $CHROME)"
