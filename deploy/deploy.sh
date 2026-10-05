#!/usr/bin/env bash
# =====================================================================
# FilterPhone — העלאה לשרת והתקנה. רץ מהמחשב (או מ-GitHub Actions).
#
#   deploy/deploy.sh                       # לשרת ברירת המחדל
#   FP_SERVER=ubuntu@1.2.3.4 deploy/deploy.sh
#
# שולח רק את מה שהשרת צריך (server/ tools/ deploy/) — בלי legacy, בלי האפליקציה.
# =====================================================================
set -euo pipefail

SERVER=${FP_SERVER:-ubuntu@151.145.88.42}
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REV=$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)

echo "מעלה גרסה $REV אל $SERVER ..."
tar -C "$ROOT" -czf - --exclude='__pycache__' --exclude='*.pyc' --exclude='.pytest_cache' \
    server tools deploy \
  | ssh -o BatchMode=yes "$SERVER" "set -e; A=/opt/filterphone
      sudo rm -rf \$A/src.new && sudo mkdir -p \$A/src.new && sudo tar -C \$A/src.new -xzf -
      # הגרסה שרצה עד עכשיו נשמרת ב-src.prev (לחזרה: deploy/rollback_version.sh).
      # העלאה חוזרת של אותה גרסה לא דורסת את הגרסה הקודמת האמיתית.
      if [ -d \$A/src ] && [ \"\$(cat \$A/src/REVISION 2>/dev/null)\" != '$REV' ]; then
          sudo rm -rf \$A/src.prev && sudo mv \$A/src \$A/src.prev
      else
          sudo rm -rf \$A/src
      fi
      sudo mv \$A/src.new \$A/src
      echo $REV | sudo tee \$A/src/REVISION >/dev/null"

ssh -o BatchMode=yes "$SERVER" "sudo bash /opt/filterphone/src/deploy/install.sh"
