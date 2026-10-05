#!/usr/bin/env bash
# =====================================================================
# FilterPhone — חזרה לגרסה הקודמת של FilterPhone (אחרי עדכון שלא הצליח). רץ כ-root בשרת.
#
#   sudo bash /opt/filterphone/src/deploy/rollback_version.sh /root/fp-backups/<תאריך>
#
# <תאריך> = תיקיית הגיבוי שה-install.sh של העדכון הכושל יצר (מודפסת בסוף ההתקנה: "גיבויים: ...").
# מה קורה:
#   1. הקוד הקודם (/opt/filterphone/src.prev) חוזר להיות /opt/filterphone/src
#   2. מסד הנתונים וקובץ ההגדרות חוזרים מהגיבוי שנלקח רגע לפני העדכון
#   3. ההתקנה של הגרסה הקודמת רצה מחדש (שירותים, מודלים, Caddy...)
# שימו לב: מכשירים / בקשות / שינויים שנעשו *אחרי* העדכון לא נמצאים בגיבוי — הם יימחקו.
# (לחזרה למערכת הישנה של לפני 5.10.2026 — deploy/rollback.sh, זה סקריפט אחר.)
# =====================================================================
set -euo pipefail

APP=/opt/filterphone
DATA=/var/lib/filterphone
ETC=/etc/filterphone
B=${1:?"צריך לציין את תיקיית הגיבוי של העדכון, למשל /root/fp-backups/20261005-120000"}

die() { printf '\033[1;31m[שגיאה]\033[0m %s\n' "$*"; exit 1; }
[ "$(id -u)" = 0 ] || die "צריך להריץ כ-root (sudo)"
[ -d "$B" ] || die "אין תיקייה $B"
[ -d "$APP/src.prev" ] || die "אין גרסה קודמת ב-$APP/src.prev"
[ -f "$B/filterphone.db" ] || die "אין גיבוי של מסד הנתונים ב-$B — לא ממשיכים (אחרת הגרסה הקודמת תעלה על מסד חדש מדי)"

echo "חוזר לגרסה $(cat "$APP/src.prev/REVISION" 2>/dev/null || echo '?') (עכשיו: $(cat "$APP/src/REVISION" 2>/dev/null || echo '?'))"
systemctl stop fp-api.service fp-proxy.service 2>/dev/null || true

# מסד הנתונים מהגיבוי — את המסד הנוכחי שומרים בצד, ליתר ביטחון
keep="$DATA/filterphone.db.rolled-back-$(date +%Y%m%d-%H%M%S)"
[ -f "$DATA/filterphone.db" ] && cp -a "$DATA/filterphone.db" "$keep"
install -m 600 -o filterphone -g filterphone "$B/filterphone.db" "$DATA/filterphone.db"
rm -f "$DATA/filterphone.db-wal" "$DATA/filterphone.db-shm"
[ -f "$B/filterphone.env" ] && install -m 640 -o root -g filterphone "$B/filterphone.env" "$ETC/filterphone.env"

# הקוד הקודם חוזר למקומו; הכושל נשמר ב-src.failed לבדיקה
rm -rf "$APP/src.failed"
mv "$APP/src" "$APP/src.failed"
mv "$APP/src.prev" "$APP/src"

bash "$APP/src/deploy/install.sh"
echo "חזרנו לגרסה הקודמת. המסד שהיה לפני החזרה: $keep · הקוד הכושל: $APP/src.failed"
