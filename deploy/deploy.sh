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
  | ssh -o BatchMode=yes "$SERVER" "sudo rm -rf /opt/filterphone/src.new && sudo mkdir -p /opt/filterphone/src.new \
      && sudo tar -C /opt/filterphone/src.new -xzf - \
      && sudo rm -rf /opt/filterphone/src && sudo mv /opt/filterphone/src.new /opt/filterphone/src \
      && echo $REV | sudo tee /opt/filterphone/src/REVISION >/dev/null"

ssh -o BatchMode=yes "$SERVER" "sudo bash /opt/filterphone/src/deploy/install.sh"
