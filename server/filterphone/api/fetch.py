"""הורדת תמונה מכתובת שהמנהל הדביק ("בדיקת תמונה" בממשק הניהול).

השרת מוריד בשם המנהל — לכן רק כתובות ציבוריות: לא localhost, לא הרשת הפנימית
ולא כתובות ה-VPN (הגנה מ-SSRF), גם אחרי הפניות (redirect).
* החיבור נעשה לכתובת ה-IP שנבדקה — לא מפענחים את השם פעם שנייה (DNS rebinding).
* גודל מוגבל, ומגבלת זמן כוללת להורדה (לא רק לכל קריאה) — שרת שמטפטף לא מחזיק אותנו.
"""
from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import time
import urllib.parse

UA = "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Mobile Safari/537.36"
ACCEPT = "image/webp,image/apng,image/*,*/*;q=0.8"
MAX_REDIRECTS = 4


class FetchError(Exception):
    pass


def _public_ip(host: str, port: int) -> str:
    """מפענח את השם ומחזיר כתובת ציבורית אחת. כל כתובת לא-ציבורית בתשובה = סירוב."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as e:
        raise FetchError(f"השם {host} לא נמצא") from e
    ips = []
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        mapped = getattr(ip, "ipv4_mapped", None)
        if not ip.is_global or ip.is_multicast or (mapped is not None and not mapped.is_global):
            raise FetchError("כתובת פנימית — לא מורשה")
        ips.append(str(ip))
    if not ips:
        raise FetchError(f"השם {host} לא נמצא")
    return ips[0]


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, ip: str, host: str, port: int, timeout: float):
        super().__init__(host, port, timeout=timeout)
        self._ip = ip

    def connect(self) -> None:
        self.sock = socket.create_connection((self._ip, self.port), self.timeout)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, ip: str, host: str, port: int, timeout: float):
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._ip = ip

    def connect(self) -> None:
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        # SNI ובדיקת התעודה — לפי השם המקורי
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _check_url(url: str) -> tuple[urllib.parse.SplitResult, int, str]:
    """→ (הכתובת המפורקת, פורט, כתובת IP ציבורית לחיבור). זורק FetchError אם אסור."""
    try:
        u = urllib.parse.urlsplit(url)
        port = u.port or (443 if u.scheme == "https" else 80)
    except ValueError as e:
        raise FetchError("כתובת לא תקינה") from e
    if u.scheme not in ("http", "https") or not u.hostname:
        raise FetchError("רק כתובות http/https")
    if u.username or u.password:
        raise FetchError("כתובת עם שם משתמש — לא נתמכת")
    return u, port, _public_ip(u.hostname, port)


def _open(url: str, timeout: float):
    u, port, ip = _check_url(url)
    cls = _PinnedHTTPSConnection if u.scheme == "https" else _PinnedHTTPConnection
    conn = cls(ip, u.hostname, port, timeout)
    path = urllib.parse.urlunsplit(("", "", u.path or "/", u.query, ""))
    conn.request("GET", path, headers={"User-Agent": UA, "Accept": ACCEPT, "Accept-Encoding": "identity"})
    return conn, conn.getresponse()


def fetch_image(url: str, max_bytes: int, timeout: float = 12.0, total_s: float = 20.0) -> tuple[bytes, str]:
    """→ (בייטים, סוג תוכן)."""
    deadline = time.monotonic() + total_s
    url = url.strip()
    for _ in range(MAX_REDIRECTS + 1):
        try:
            conn, resp = _open(url, timeout)
        except FetchError:
            raise
        except (OSError, http.client.HTTPException, ValueError) as e:
            raise FetchError(f"ההורדה נכשלה: {type(e).__name__}") from e
        try:
            if resp.status in (301, 302, 303, 307, 308) and resp.getheader("location"):
                url = urllib.parse.urljoin(url, resp.getheader("location"))
                continue
            if resp.status != 200:
                raise FetchError(f"האתר החזיר {resp.status}")
            length = resp.getheader("content-length")
            if length and length.isdigit() and int(length) > max_bytes:
                raise FetchError("התמונה גדולה מדי")
            data = bytearray()
            while len(data) <= max_bytes:
                if time.monotonic() > deadline:
                    raise FetchError("ההורדה איטית מדי")
                chunk = resp.read1(min(65536, max_bytes + 1 - len(data)))
                if not chunk:
                    break
                data += chunk
            if len(data) > max_bytes:
                raise FetchError("התמונה גדולה מדי")
            return bytes(data), resp.getheader("content-type", "")
        except (OSError, http.client.HTTPException) as e:
            raise FetchError(f"ההורדה נכשלה: {type(e).__name__}") from e
        finally:
            conn.close()
    raise FetchError("יותר מדי הפניות")
