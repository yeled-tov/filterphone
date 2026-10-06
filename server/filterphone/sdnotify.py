"""הודעות ל-systemd (sd_notify) בלי תלות חיצונית: READY, WATCHDOG, STOPPING, STATUS.

השירות מוגדר Type=notify + WatchdogSec: systemd מחכה ל-READY=1 לפני שהוא מסמן את השירות
כפעיל, ומפעיל אותו מחדש אם WATCHDOG=1 לא הגיע בזמן (התהליך חי אבל תקוע).
מחוץ ל-systemd (בדיקות, הרצה ידנית) — אין NOTIFY_SOCKET והכול שקט.
"""
from __future__ import annotations

import os
import socket


def notify(state: str) -> bool:
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return False
    if addr[0] == "@":                  # abstract namespace
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM | socket.SOCK_CLOEXEC) as s:
            s.connect(addr)
            s.sendall(state.encode())
        return True
    except OSError:
        return False


def watchdog_interval_s() -> float | None:
    """חצי מ-WatchdogSec (כמו שמומלץ), או None אם ה-watchdog לא מופעל לתהליך הזה."""
    usec = os.environ.get("WATCHDOG_USEC")
    pid = os.environ.get("WATCHDOG_PID")
    if not usec or (pid and pid != str(os.getpid())):
        return None
    try:
        return max(1.0, int(usec) / 1_000_000 / 2)
    except ValueError:
        return None
