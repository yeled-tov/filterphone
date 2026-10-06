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
arm64=$(ls "$tmp"/*-arm64.apk | head -1)
universal=$(ls "$tmp"/*-universal.apk | head -1)
echo "מפרסם $(basename "$arm64") + $(basename "$universal") (בנייה $run_id)"

# FilterPhone.apk = arm64 (קל, מתאים כמעט לכל טלפון); FilterPhone-universal.apk = לכל המעבדים;
# FilterPhone.json = הגרסה והטביעות — מהן השרת מציע עדכון לטלפונים (במנהל מכשיר: עדכון שקט)
scp -o BatchMode=yes "$arm64" "$SERVER:/tmp/FilterPhone.apk"
scp -o BatchMode=yes "$universal" "$SERVER:/tmp/FilterPhone-universal.apk"
json=""
if [ -f "$tmp/FilterPhone.json" ]; then
    scp -o BatchMode=yes "$tmp/FilterPhone.json" "$SERVER:/tmp/FilterPhone.json"
    json=/tmp/FilterPhone.json
fi
ssh -o BatchMode=yes "$SERVER" "sudo install -m 644 -o root -g root /tmp/FilterPhone.apk /tmp/FilterPhone-universal.apk $json /var/www/filterphone/ \
    && { [ -n '$json' ] || sudo rm -f /var/www/filterphone/FilterPhone.json; } \
    && rm -f /tmp/FilterPhone.apk /tmp/FilterPhone-universal.apk /tmp/FilterPhone.json && ls -lh /var/www/filterphone/"
