#!/usr/bin/env bash
# =====================================================================
# FilterPhone — פרסום ה-APK האחרון שנבנה ב-GitHub לדף ההורדה בשרת.
#   deploy/publish_apk.sh
# דורש gh מחובר (או GH_TOKEN) וגישת SSH לשרת.
# =====================================================================
set -euo pipefail

SERVER=${FP_SERVER:-ubuntu@151.145.88.42}
REPO=${FP_REPO:-yeled-tov/filterphone}
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

run_id=$(gh run list -R "$REPO" --workflow app.yml --branch main --status success --limit 1 --json databaseId -q '.[0].databaseId')
[ -n "$run_id" ] || { echo "אין בנייה מוצלחת של האפליקציה"; exit 1; }
gh run download "$run_id" -R "$REPO" -n FilterPhone-APK -D "$tmp"
apk=$(ls "$tmp"/*.apk | head -1)
echo "מפרסם $(basename "$apk") (בנייה $run_id)"

scp -o BatchMode=yes "$apk" "$SERVER:/tmp/FilterPhone.apk"
ssh -o BatchMode=yes "$SERVER" "sudo install -m 644 -o root -g root /tmp/FilterPhone.apk /var/www/filterphone/FilterPhone.apk \
    && rm -f /tmp/FilterPhone.apk && sha256sum /var/www/filterphone/FilterPhone.apk"
