"""תוסף mitmproxy של FilterPhone.

עקרונות:
  * לא חוסם את לולאת האירועים — כל הניתוח רץ במנוע הנפרד (await).
  * רק תמונות (ודפים עם תמונות מוטמעות) נאספים לזיכרון; כל שאר התשובות (וידאו,
    הורדות, קבצים) עוברות בזרימה (stream) ישר לטלפון, בלי השהיה.
  * אפליקציות שלא סומכות על התעודה שלנו עוברות אוטומטית (ראו hosts.py) — חוץ
    משרתי תמונות במכשירים במצב מחמיר, ומאתרים שהמנהל חסם.
  * לכל מכשיר מדיניות משלו (policy.py): רמה, אתרים/שירותים חסומים, "בלי תמונות",
    "בלי וידאו". חסימת אתר נאכפת כאן (לפי SNI / Host) וגם ב-DNS (AdGuard).

הפעלה: mitmdump --mode transparent -s deploy/mitm_addon.py
"""
from __future__ import annotations

import asyncio
import logging
import time

import redis.asyncio as aioredis
from mitmproxy import http, tls
from mitmproxy.proxy import server_hooks

from .. import metrics
from ..engine.imaging import PLACEHOLDER_PNG
from ..engine.policy import Action, on_failure
from ..settings import get_settings
from . import inline, rewrite
from .blockpage import block_page
from .bodies import bounded_content
from .client import EngineClient, EngineUnavailable
from .hosts import HostPolicy, load_lists
from .policy import DevicePolicy, PolicyTable

log = logging.getLogger("filterphone.proxy")

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".bmp", ".jfif", ".heic", ".heif")
NOT_FILTERED_TYPES = ("image/svg+xml", "image/x-icon", "image/vnd.microsoft.icon")
GENERIC_TYPES = ("", "application/octet-stream", "binary/octet-stream")
VIDEO_TYPES = ("application/vnd.apple.mpegurl", "application/x-mpegurl", "application/dash+xml",
               "application/vnd.yt-ump", "application/vnd.ms-sstr+xml")
MAX_INLINE_BYTES = 4_000_000
MAX_INLINE_DECODED = 16_000_000     # דף אחרי פריסת gzip/br — מעבר לזה לא סורקים (פצצת דחיסה)
PASSTHROUGH_KEY = "fp:pt"
EVENTS_KEY = "fp:events"
METRICS_KEY = "fp:metrics:proxy"
METRICS_EVERY_S = 15
HOST_EVENT_EVERY_S = 60             # חסימת אתר נרשמת פעם בדקה לכל (מכשיר, אתר) — לא על כל בקשה
PLACEHOLDER_HEADERS_DROP = ("etag", "last-modified", "content-md5", "content-range",
                            "accept-ranges", "content-encoding", "digest")


def _client_ip(flow_or_ctx) -> str:
    peer = flow_or_ctx.client_conn.peername if hasattr(flow_or_ctx, "client_conn") \
        else flow_or_ctx.client.peername
    return peer[0] if peer else ""


def _ctype(msg: http.Message) -> str:
    return msg.headers.get("content-type", "").split(";", 1)[0].strip().lower()


def _is_image(flow: http.HTTPFlow) -> bool:
    ct = _ctype(flow.response)
    if ct.startswith("image/"):
        return ct not in NOT_FILTERED_TYPES
    if ct in GENERIC_TYPES:
        return flow.request.path.split("?", 1)[0].lower().endswith(IMAGE_EXTENSIONS)
    return False


def _is_image_request(req: http.Request) -> bool:
    if req.headers.get("sec-fetch-dest", "").lower() == "image":
        return True
    return req.headers.get("accept", "").lower().startswith("image/")


def _is_document_request(req: http.Request) -> bool:
    dest = req.headers.get("sec-fetch-dest", "").lower()
    if dest in ("document", "iframe", "frame"):
        return True
    return not dest and "text/html" in req.headers.get("accept", "").lower()


def _is_video(flow: http.HTTPFlow) -> bool:
    ct = _ctype(flow.response)
    if ct.startswith("video/") or ct in VIDEO_TYPES:
        return True
    return flow.request.headers.get("sec-fetch-dest", "").lower() == "video"


def _short_path(req: http.Request) -> str:
    return req.path.split("?", 1)[0][:160]


class FilterPhone:
    def __init__(self) -> None:
        self.cfg = get_settings()
        passthrough, inspect = load_lists(self.cfg.passthrough_file)
        self.hosts = HostPolicy(passthrough, inspect, self.cfg.passthrough_learn_days * 86400)
        self.engine = EngineClient(self.cfg.engine_socket, self.cfg.engine_pool_size,
                                   self.cfg.engine_timeout_s)
        self.redis = aioredis.from_url(self.cfg.redis_url, decode_responses=True,
                                       socket_timeout=1.0, socket_connect_timeout=1.0,
                                       max_connections=64, health_check_interval=30)
        self.policies = PolicyTable(self.redis, self.cfg.default_level)
        self._background: set[asyncio.Task] = set()
        self._host_events: dict[tuple[str, str], float] = {}
        m = self.metrics = metrics.Registry("fp_proxy")
        self.m_images = m.counter("images_total", "תמונות שנבדקו, לפי תוצאה")
        self.m_engine_errors = m.counter("engine_errors_total", "כשלים בפנייה למנוע, לפי סוג")
        self.m_engine_latency = m.histogram("engine_seconds", "זמן תשובה של המנוע (כולל המתנה לחיבור)")
        self.m_sites = m.counter("blocked_sites_total", "בקשות לאתרים חסומים")
        self.m_inline = m.counter("inline_pages_total", "דפים שנסרקו לתמונות מוטמעות, לפי תוצאה")
        self.m_oversize = m.counter("oversize_bodies_total", "גופים שלא נפרסו (גדולים מדי / קידוד לא מוכר)")
        self.m_video = m.counter("blocked_video_total", "בקשות וידאו שנחסמו")
        self.m_circuit = m.gauge("engine_circuit_open", "1 = המפסק למנוע פתוח")
        self.m_devices = m.gauge("policy_devices", "מכשירים בטבלת המדיניות")

    # ------------------------------------------------------------ מחזור חיים

    async def running(self) -> None:
        await self._load_learned()
        try:
            await self.policies.refresh()
        except Exception as e:
            log.warning("initial policy load failed: %s", e)
        self._spawn(self.policies.run())
        self._spawn(self._reload_learned_forever())
        self._spawn(self._publish_metrics_forever())
        log.info("FilterPhone proxy ready (%d learned passthrough hosts)", self.hosts.learned_count())

    async def done(self) -> None:
        for t in list(self._background):
            t.cancel()
        self.engine.close_idle()
        await self.redis.aclose()

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _load_learned(self) -> None:
        """טוען מ-Redis את רשימת (מכשיר, שרת) שעוברים בלי בדיקה. Redis הוא מקור האמת:
        איפוס מממשק הניהול מוחק משם, והטעינה הבאה מעדכנת את הזיכרון."""
        try:
            now = time.time()
            learned, expired = {}, []
            for field, exp in (await self.redis.hgetall(PASSTHROUGH_KEY)).items():
                ip, _, host = field.partition("|")
                if float(exp) > now:
                    learned[(ip, host)] = float(exp)
                else:
                    expired.append(field)
            self.hosts.replace_learned(learned)
            if expired:
                await self.redis.hdel(PASSTHROUGH_KEY, *expired)
        except Exception as e:
            log.warning("could not load learned passthrough list: %s", e)

    async def _reload_learned_forever(self) -> None:
        while True:
            await asyncio.sleep(60)
            await self._load_learned()

    async def _publish_metrics_forever(self) -> None:
        """המדדים של הפרוקסי ל-Redis — ה-API מציג אותם ב-/metrics ובממשק הניהול."""
        while True:
            await asyncio.sleep(METRICS_EVERY_S)
            await self.publish_metrics()

    async def publish_metrics(self) -> None:
        self.m_circuit.set(1 if self.engine.breaker.is_open else 0)
        self.m_devices.set(len(self.policies))
        snap = {k: str(v) for k, v in self.metrics.snapshot().items()}
        snap["_ts"] = str(int(time.time()))
        snap["fp_proxy_memory_bytes"] = str(metrics.process_memory_bytes())
        snap["fp_proxy_learned_hosts"] = str(self.hosts.learned_count())
        try:
            async with self.redis.pipeline(transaction=True) as p:
                p.delete(METRICS_KEY)
                p.hset(METRICS_KEY, mapping=snap)
                p.expire(METRICS_KEY, 300)
                await p.execute()
        except Exception as e:
            log.debug("metrics publish failed: %s", e)

    # ------------------------------------------------------------ TLS

    def tls_clienthello(self, data: tls.ClientHelloData) -> None:
        ip = _client_ip(data.context)
        pol = self.policies.get(ip)
        sni = data.client_hello.sni
        if pol.blocks(sni):
            return          # אתר חסום: לא עוקפים — יירוט, ואז דף חסימה / כשל TLS באפליקציה
        if not pol.filtering or self.hosts.should_passthrough(ip, sni):
            data.ignore_connection = True

    def server_connect(self, data: server_hooks.ServerConnectionHookData) -> None:
        """אתר חסום — לא פותחים אליו חיבור בכלל (הטלפון מקבל דף חסימה או כשל)."""
        sni = data.server.sni
        if sni and data.client.peername and self.policies.get(data.client.peername[0]).blocks(sni):
            data.server.error = "blocked by FilterPhone"

    def tls_failed_client(self, data: tls.TlsData) -> None:
        sni = data.conn.sni
        if not sni or not self.hosts.is_distrust_error(data.conn.error):
            return
        ip = _client_ip(data.context)
        pol = self.policies.get(ip)
        if pol.blocks(sni):
            return          # חסום בכוונה — לא "לומדים" לעקוף אותו
        expires = time.time() + self.cfg.passthrough_learn_days * 86400
        if self.hosts.learn(ip, sni, pol.app_images == "block", expires):
            self._spawn(self.redis.hset(PASSTHROUGH_KEY, f"{ip}|{sni.lower()}", expires))

    # ------------------------------------------------------------ HTTP

    def requestheaders(self, flow: http.HTTPFlow) -> None:
        req = flow.request
        ip = _client_ip(flow)
        pol = self.policies.get(ip)
        host = req.pretty_host
        if pol.blocks(host) or (pol.paths and pol.blocks_path(host, req.path)):
            flow.response = self._blocked_host_response(req, host)
            flow.metadata["fp_handled"] = True
            self.m_sites.inc()
            self._record_host_block(ip, host, req)
            return
        if not pol.filtering or not _is_image_request(req):
            return
        if pol.images == "block":
            flow.response = self._image_response(PLACEHOLDER_PNG, "image/png", "images-off")
            flow.metadata["fp_handled"] = True
            return
        # תמונה שהדפדפן שמר לפני שהסינון הופעל תחזור כ-304 ותוצג מהזיכרון שלו.
        # מבטלים בקשות מותנות של תמונות — הקאש שלנו הופך את הבדיקה החוזרת לזולה.
        req.headers.pop("if-none-match", None)
        req.headers.pop("if-modified-since", None)
        # מבקשים מהאתר פורמט שהמנוע מפענח היטב (לא AVIF) — ראו rewrite.py
        accept = rewrite.image_accept(req.headers.get("accept", ""))
        if accept:
            req.headers["accept"] = accept
        path = rewrite.image_path(host, req.path)
        if path:
            req.path = path

    def responseheaders(self, flow: http.HTTPFlow) -> None:
        if flow.metadata.get("fp_handled"):
            return
        resp = flow.response
        ip = _client_ip(flow)
        pol = self.policies.get(ip)
        if not pol.filtering or flow.request.method == "HEAD":
            resp.stream = True
            return
        if pol.video and _is_video(flow):
            self.m_video.inc()
            self._spawn(self._record(ip, flow.request.pretty_host, "video", "video blocked", "video",
                                     _short_path(flow.request)))
            flow.kill()
            return
        length = resp.headers.get("content-length", "")
        size = int(length) if length.isdigit() else None
        if _is_image(flow):
            if (pol.level <= 0 and pol.images != "block") or \
                    (size is not None and size > self.cfg.max_image_bytes):
                resp.stream = True
                return
            flow.metadata["fp_kind"] = "image"
            return
        if (pol.level > 0 or pol.images == "block") \
                and inline.should_scan(_ctype(resp), flow.request.pretty_host) \
                and (size is None or size <= MAX_INLINE_BYTES):
            flow.metadata["fp_kind"] = "inline"
            return
        resp.stream = True

    async def response(self, flow: http.HTTPFlow) -> None:
        kind = flow.metadata.get("fp_kind")
        if kind is None or flow.metadata.get("fp_handled") or flow.response.stream:
            return
        if kind == "image":
            await self._filter_image(flow)
        elif kind == "inline":
            await self._filter_inline(flow)

    # ------------------------------------------------------------ תמונות

    async def _judge(self, body: bytes, pol: DevicePolicy, host: str, ip: str) -> tuple[str, str, bytes, str]:
        """→ (פעולה, סיבה, גוף חדש, סוג תוכן). פעולה pass = להשאיר כמו שזה."""
        if pol.images == "block":
            return "block", "images off", PLACEHOLDER_PNG, "image/png"
        t0 = time.perf_counter()
        try:
            header, out = await self.engine.classify(body, {"level": pol.level, "host": host, "client": ip})
            action, reason = header.get("action", "pass"), header.get("reason", "")
            ct = header.get("ct") or "image/png"
            self.m_engine_latency.observe(time.perf_counter() - t0)
        except Exception as e:
            kind = "circuit_open" if isinstance(e, EngineUnavailable) else type(e).__name__
            self.m_engine_errors.inc(kind=kind)
            if not isinstance(e, EngineUnavailable):
                log.warning("engine unavailable (%s): %s", kind, e)
            d = on_failure(pol.level)
            action, reason, out, ct = d.action.value, f"engine error: {kind}", b"", "image/png"
        if action == Action.BLOCK and not out:
            out, ct = PLACEHOLDER_PNG, "image/png"
        self.m_images.inc(action=str(action))
        return action, reason, out, ct

    async def _filter_image(self, flow: http.HTTPFlow) -> None:
        ip = _client_ip(flow)
        pol = self.policies.get(ip)
        host = flow.request.pretty_host
        body = bounded_content(flow.response, self.cfg.max_image_bytes)
        if body is None:
            # דחוס מעבר לתקרה / קידוד לא מוכר — אי אפשר לבדוק: מדיניות הכשל של הרמה
            self.m_oversize.inc(kind="image")
            d = on_failure(pol.level)
            if d.action is Action.BLOCK or pol.images == "block":
                self._replace(flow.response, PLACEHOLDER_PNG, "image/png", "block")
                self._spawn(self._record(ip, host, "block", "unverifiable: body", "image", _short_path(flow.request)))
            return
        if len(body) < self.cfg.min_image_bytes:
            return
        action, reason, out, ct = await self._judge(body, pol, host, ip)
        self._spawn(self._count(ip, "scanned"))
        if action == Action.PASS:
            return
        self._replace(flow.response, out, ct, action)
        if pol.images != "block":
            self._spawn(self._record(ip, host, action, reason, "image", _short_path(flow.request)))

    async def _filter_inline(self, flow: http.HTTPFlow) -> None:
        body = bounded_content(flow.response, MAX_INLINE_DECODED)
        if body is None:
            self.m_oversize.inc(kind="inline")
            return
        if not body or b"data:image" not in body:
            return
        ip = _client_ip(flow)
        pol = self.policies.get(ip)
        host = flow.request.pretty_host
        actions: list[tuple[str, str]] = []

        async def judge(img: bytes) -> tuple[bytes, str] | None:
            action, reason, out, ct = await self._judge(img, pol, host, ip)
            if action == Action.PASS:
                return None
            actions.append((action, reason))
            return out, ct

        def overflow(_: bytes) -> tuple[bytes, str] | None:
            return (PLACEHOLDER_PNG, "image/png") if on_failure(pol.level).action is Action.BLOCK else None

        result = await inline.rewrite(body, judge, self.cfg.min_image_bytes, overflow=overflow)
        self.m_inline.inc(result="replaced" if result.replaced else "clean")
        if not result.replaced:
            return
        resp = flow.response
        for h in ("etag", "last-modified", "content-md5", "digest"):
            resp.headers.pop(h, None)
        resp.content = result.body          # mitmproxy דוחס מחדש לפי content-encoding
        resp.headers["x-filterphone"] = f"inline-{result.replaced}"
        if pol.images != "block":
            for action, reason in actions:
                self._spawn(self._record(ip, host, action, reason, "inline", _short_path(flow.request)))

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

    @staticmethod
    def _image_response(body: bytes, ct: str, tag: str) -> http.Response:
        return http.Response.make(200, body, {"content-type": ct, "cache-control": "no-store",
                                              "x-filterphone": tag})

    def _blocked_host_response(self, req: http.Request, host: str) -> http.Response:
        if _is_document_request(req):
            return http.Response.make(403, block_page(host), {
                "content-type": "text/html; charset=utf-8", "cache-control": "no-store",
                "x-filterphone": "blocked-host"})
        if _is_image_request(req):
            return self._image_response(PLACEHOLDER_PNG, "image/png", "blocked-host")
        return http.Response.make(403, b"", {"cache-control": "no-store", "x-filterphone": "blocked-host"})

    # ------------------------------------------------------------ רישום

    def _record_host_block(self, ip: str, host: str, req: http.Request) -> None:
        now = time.monotonic()      # שניות מאז שהשרת עלה — יכול להיות קטן מ-60 מיד אחרי אתחול
        last = self._host_events.get((ip, host))
        if last is not None and now - last < HOST_EVENT_EVERY_S:
            return
        if len(self._host_events) > 5000:
            self._host_events.clear()
        self._host_events[(ip, host)] = now
        self._spawn(self._record(ip, host, "site", "site blocked", "host", _short_path(req)))

    async def _count(self, ip: str, what: str) -> None:
        try:
            day = time.strftime("%Y%m%d")
            async with self.redis.pipeline(transaction=False) as p:
                p.hincrby(f"fp:stats:{day}", f"{ip}|{what}", 1)
                p.expire(f"fp:stats:{day}", 40 * 86400)
                await p.execute()
        except Exception:
            pass

    async def _record(self, ip: str, host: str, action: str, reason: str, kind: str = "image",
                      path: str = "") -> None:
        """רישום חסימה: 3000 האחרונות לממשק הניהול + מונה יומי לכל מכשיר.

        action: block / replace (תמונות), site (אתר חסום), video.
        """
        try:
            day = time.strftime("%Y%m%d")
            async with self.redis.pipeline(transaction=False) as p:
                p.xadd(EVENTS_KEY, {"ip": ip, "host": host, "action": action, "reason": reason,
                                    "kind": kind, "path": path, "ts": int(time.time())},
                       maxlen=3000, approximate=True)
                p.hincrby(f"fp:stats:{day}", f"{ip}|{action}", 1)
                p.expire(f"fp:stats:{day}", 40 * 86400)
                await p.execute()
        except Exception:
            pass


addons = [FilterPhone()]
