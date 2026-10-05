"""תוסף mitmproxy של FilterPhone.

עקרונות:
  * לא חוסם את לולאת האירועים — כל הניתוח רץ במנוע הנפרד (await).
  * רק תמונות נאספות לזיכרון; כל שאר התשובות (וידאו, הורדות, דפים) עוברות
    בזרימה (stream) ישר לטלפון, בלי השהיה.
  * אפליקציות שלא סומכות על התעודה שלנו עוברות אוטומטית (ראו hosts.py).

הפעלה: mitmdump --mode transparent -s deploy/mitm_addon.py
"""
from __future__ import annotations

import asyncio
import logging
import time

import redis.asyncio as aioredis
from mitmproxy import http, tls

from ..engine.imaging import PLACEHOLDER_PNG
from ..engine.policy import Action, on_failure
from ..settings import get_settings
from .client import EngineClient
from .hosts import HostPolicy, load_lists
from .levels import LevelTable

log = logging.getLogger("filterphone.proxy")

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".bmp", ".jfif")
NOT_FILTERED_TYPES = ("image/svg+xml", "image/x-icon", "image/vnd.microsoft.icon")
GENERIC_TYPES = ("", "application/octet-stream", "binary/octet-stream")
PASSTHROUGH_KEY = "fp:pt"
EVENTS_KEY = "fp:events"
PLACEHOLDER_HEADERS_DROP = ("etag", "last-modified", "content-md5", "content-range",
                            "accept-ranges", "content-encoding", "digest")


def _client_ip(flow_or_ctx) -> str:
    peer = flow_or_ctx.client_conn.peername if hasattr(flow_or_ctx, "client_conn") \
        else flow_or_ctx.client.peername
    return peer[0] if peer else ""


def _is_image(flow: http.HTTPFlow) -> bool:
    ct = flow.response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if ct.startswith("image/"):
        return ct not in NOT_FILTERED_TYPES
    if ct in GENERIC_TYPES:
        return flow.request.path.split("?", 1)[0].lower().endswith(IMAGE_EXTENSIONS)
    return False


def _is_image_request(req: http.Request) -> bool:
    if req.headers.get("sec-fetch-dest", "").lower() == "image":
        return True
    return req.headers.get("accept", "").lower().startswith("image/")


class FilterPhone:
    def __init__(self) -> None:
        self.cfg = get_settings()
        passthrough, inspect = load_lists(self.cfg.passthrough_file)
        self.hosts = HostPolicy(passthrough, inspect, self.cfg.passthrough_learn_days * 86400)
        self.engine = EngineClient(self.cfg.engine_socket, self.cfg.engine_pool_size,
                                   self.cfg.engine_timeout_s)
        self.redis = aioredis.from_url(self.cfg.redis_url, decode_responses=True,
                                       socket_timeout=1.0, socket_connect_timeout=1.0)
        self.levels = LevelTable(self.redis, self.cfg.default_level)
        self._background: set[asyncio.Task] = set()

    # ------------------------------------------------------------ מחזור חיים

    async def running(self) -> None:
        await self._load_learned()
        self._spawn(self.levels.run())
        log.info("FilterPhone proxy ready (%d learned passthrough hosts)", self.hosts.learned_count())

    async def done(self) -> None:
        for t in list(self._background):
            t.cancel()
        await self.redis.aclose()

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _load_learned(self) -> None:
        try:
            now = time.time()
            for field, exp in (await self.redis.hgetall(PASSTHROUGH_KEY)).items():
                ip, _, host = field.partition("|")
                if float(exp) > now:
                    self.hosts.learn(ip, host, level=0, expires_at=float(exp))
        except Exception as e:
            log.warning("could not load learned passthrough list: %s", e)

    # ------------------------------------------------------------ TLS

    def tls_clienthello(self, data: tls.ClientHelloData) -> None:
        ip = _client_ip(data.context)
        if self.levels.get(ip) <= 0 or self.hosts.should_passthrough(ip, data.client_hello.sni):
            data.ignore_connection = True

    def tls_failed_client(self, data: tls.TlsData) -> None:
        sni = data.conn.sni
        if not sni or not self.hosts.is_distrust_error(data.conn.error):
            return
        ip = _client_ip(data.context)
        expires = time.time() + self.cfg.passthrough_learn_days * 86400
        if self.hosts.learn(ip, sni, self.levels.get(ip), expires):
            self._spawn(self.redis.hset(PASSTHROUGH_KEY, f"{ip}|{sni.lower()}", expires))

    # ------------------------------------------------------------ HTTP

    def requestheaders(self, flow: http.HTTPFlow) -> None:
        # תמונה שהדפדפן שמר לפני שהסינון הופעל תחזור כ-304 ותוצג מהזיכרון שלו.
        # מבטלים בקשות מותנות של תמונות — הקאש שלנו הופך את הבדיקה החוזרת לזולה.
        if _is_image_request(flow.request) and self.levels.get(_client_ip(flow)) > 0:
            flow.request.headers.pop("if-none-match", None)
            flow.request.headers.pop("if-modified-since", None)

    def responseheaders(self, flow: http.HTTPFlow) -> None:
        resp = flow.response
        level = self.levels.get(_client_ip(flow))
        if level <= 0 or flow.request.method == "HEAD" or not _is_image(flow):
            resp.stream = True
            return
        length = resp.headers.get("content-length", "")
        if length.isdigit() and int(length) > self.cfg.max_image_bytes:
            resp.stream = True
            return
        flow.metadata["fp_level"] = level

    async def response(self, flow: http.HTTPFlow) -> None:
        level = flow.metadata.get("fp_level")
        if level is None or flow.response.stream:
            return
        body = flow.response.get_content(strict=False)
        if not body or len(body) < self.cfg.min_image_bytes:
            return

        ip = _client_ip(flow)
        try:
            header, out = await self.engine.classify(
                body, {"level": level, "host": flow.request.pretty_host, "client": ip})
            action, reason = header.get("action", "pass"), header.get("reason", "")
        except Exception as e:
            log.warning("engine unavailable (%s): %s", type(e).__name__, e)
            d = on_failure(level)
            action, reason, out, header = d.action.value, f"engine error: {type(e).__name__}", b"", {}

        if action == Action.PASS:
            return
        if action == Action.BLOCK and not out:
            out, header = PLACEHOLDER_PNG, {"ct": "image/png"}
        self._replace(flow.response, out, header.get("ct") or "image/png", action)
        self._spawn(self._record(ip, flow.request.pretty_host, action, reason))

    @staticmethod
    def _replace(resp: http.Response, body: bytes, ct: str, action: str) -> None:
        for h in PLACEHOLDER_HEADERS_DROP:
            resp.headers.pop(h, None)
        if resp.status_code == 206:
            resp.status_code = 200
        resp.headers["content-type"] = ct
        resp.content = body
        resp.headers["cache-control"] = "no-store"
        resp.headers["x-filterphone"] = action

    async def _record(self, ip: str, host: str, action: str, reason: str) -> None:
        """רישום חסימה: 2000 האחרונות לממשק הניהול + מונה יומי לכל מכשיר."""
        try:
            day = time.strftime("%Y%m%d")
            async with self.redis.pipeline(transaction=False) as p:
                p.xadd(EVENTS_KEY, {"ip": ip, "host": host, "action": action,
                                    "reason": reason, "ts": int(time.time())},
                       maxlen=2000, approximate=True)
                p.hincrby(f"fp:stats:{day}", f"{ip}|{action}", 1)
                p.expire(f"fp:stats:{day}", 40 * 86400)
                await p.execute()
        except Exception:
            pass


addons = [FilterPhone()]
