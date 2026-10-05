"""הורדת תמונה מכתובת שהמנהל הדביק ("בדיקת תמונה" בממשק הניהול).

השרת מוריד בשם המנהל — לכן רק כתובות ציבוריות: לא localhost, לא הרשת הפנימית
ולא כתובות ה-VPN (הגנה מ-SSRF), גם אחרי הפניות (redirect). גודל וזמן מוגבלים.
"""
from __future__ import annotations

import ipaddress
import socket
import urllib.error
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Mobile Safari/537.36"
ACCEPT = "image/webp,image/apng,image/*,*/*;q=0.8"


class FetchError(Exception):
    pass


def _check_url(url: str) -> None:
    u = urllib.parse.urlsplit(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise FetchError("רק כתובות http/https")
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80),
                                   type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise FetchError(f"השם {u.hostname} לא נמצא") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise FetchError("כתובת פנימית — לא מורשה")


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_image(url: str, max_bytes: int, timeout: float = 12.0) -> tuple[bytes, str]:
    """→ (בייטים, סוג תוכן)."""
    url = url.strip()
    _check_url(url)
    handlers: list = [_SafeRedirect()]
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": ACCEPT})
    try:
        with opener.open(req, timeout=timeout) as r:
            data = r.read(max_bytes + 1)
            ct = r.headers.get("content-type", "")
    except urllib.error.HTTPError as e:
        raise FetchError(f"האתר החזיר {e.code}") from e
    except (urllib.error.URLError, OSError) as e:
        raise FetchError(f"ההורדה נכשלה: {getattr(e, 'reason', e)}") from e
    if len(data) > max_bytes:
        raise FetchError("התמונה גדולה מדי")
    return data, ct
