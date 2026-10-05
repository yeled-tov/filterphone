#!/bin/bash
# FilterPhone disk guard — prevents the disk from filling up again.
# Runs every 15 minutes (fp-disk-guard.timer). Above the threshold it trims logs.
THRESHOLD=85
USE=$(df --output=pcent / | tail -1 | tr -dc 0-9)
[ "$USE" -lt "$THRESHOLD" ] && exit 0
logger -t fp-disk-guard "disk at ${USE}% (>= ${THRESHOLD}%) — trimming logs"
rm -f /opt/AdGuardHome/data/data/querylog.json.1
truncate -s 0 /opt/AdGuardHome/data/data/querylog.json 2>/dev/null || true
journalctl --rotate >/dev/null 2>&1; journalctl --vacuum-size=100M >/dev/null 2>&1
rm -rf /root/.cache/pip /tmp/tmp* 2>/dev/null || true
logger -t fp-disk-guard "after trim: $(df --output=pcent / | tail -1 | tr -d ' ')"
