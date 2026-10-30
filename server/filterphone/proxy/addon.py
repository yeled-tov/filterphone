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
from urllib.parse import parse_qs

import redis.asyncio as aioredis
from mitmproxy import http, tls
from mitmproxy.proxy import server_hooks

from .. import metrics
from ..api.catalog import resolve_keywords
from ..engine.imaging import PLACEHOLDER_PNG
from ..engine.policy import Action, on_failure
from ..settings import get_settings
from ..websites import BLOCKED_VERDICTS, WEB_LEVELS
from . import inline, rewrite, serp
from .blockpage import REVIEW_PATH, block_page, checking_page, review_sent_page
from .bodies import bounded_content
from .client import EngineClient, EngineUnavailable
from .hosts import HostPolicy, is_chromium_hello, load_lists
from .policy import DevicePolicy, PolicyTable
from .web import CHECK_WAIT_S, WebState

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
MAX_SERP_BYTES = 6_000_000
# לשוניות בחיפוש Google שסגורות ברמות הרשימה הלבנה (תמונות, חדשות, סרטונים, קניות, מצב AI)
_GOOGLE_TABS = {"isch": "images", "nws": "news", "vid": "video", "shop": "shopping", "bks": "books"}
_GOOGLE_UDM = {"2": "images", "12": "news", "7": "video", "28": "shopping", "36": "books", "50": "ai"}


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
        # שרת שלא מסר סוג תוכן (או "בינארי"): לפי הסיומת, או לפי מה שהדפדפן ביקש (<img>) —
        # כך תמונה בכתובת בלי סיומת (‎/kf/S1a2b3) עדיין נבדקת
        return flow.request.path.split("?", 1)[0].lower().endswith(IMAGE_EXTENSIONS) \
            or _is_image_request(flow.request)
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


def _is_navigation(req: http.Request) -> bool:
    """ניווט לדף (המסמך הראשי בלשונית) — לא iframe, לא משאב של דף."""
    dest = req.headers.get("sec-fetch-dest", "").lower()
    if dest:
        return dest == "document"
    return req.method == "GET" and "text/html" in req.headers.get("accept", "").lower()


def _google_tab(path: str) -> str | None:
    """הלשונית בחיפוש Google (images / news / video / shopping / books / ai), או None לחיפוש הרגיל."""
    q = parse_qs(path.partition("?")[2])
    tab = _GOOGLE_TABS.get((q.get("tbm") or [""])[0])
    return tab or _GOOGLE_UDM.get((q.get("udm") or [""])[0])


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
        self.web = WebState(self.redis, self.cfg.adult_list_file)
        self.terms = serp.TermMatcher(resolve_keywords({})["terms"])
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
        self.m_learned_inspected = m.counter("learned_chrome_inspected_total",
                                             "חיבורי כרום לשרת תוכן שנלמד (בגלל אפליקציה אחרת) — נבדקו בכל זאת")
        self.m_web = m.counter("web_pages_total", "ניווט לדפים ברמות גלישה, לפי תוצאה (allow/check/block)")
        self.m_search = m.counter("search_total", "חיפושים: מילה מפורשת (query) / לשונית סגורה (tab)")
        self.m_serp = m.counter("serp_results_hidden_total", "תוצאות חיפוש שהוסתרו")

    # ------------------------------------------------------------ מחזור חיים

    async def running(self) -> None:
        await self._load_learned()
        try:
            await self.policies.refresh()
        except Exception as e:
            log.warning("initial policy load failed: %s", e)
        try:
            await self.web.load()
        except Exception as e:
            log.warning("initial web verdicts load failed: %s", e)
        await asyncio.to_thread(self.web.reload_list_if_changed)
        self._spawn(self.policies.run())
        self._spawn(self.web.run())
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
        if not pol.filtering:
            data.ignore_connection = True
            return
        if self.hosts.should_passthrough(ip, sni):
            # נלמד בגלל אפליקציה שלא סומכת על התעודה — אבל זה כרום, והשרת מגיש תוכן (תמונות / חיפוש /
            # חנות): כרום סומך עלינו, ולכן נבדק כרגיל. כך הלמידה לא פותחת "חור" בדפדפן.
            if self.hosts.is_learned(ip, sni) and self.hosts.is_content_host(sni) \
                    and is_chromium_hello(data.client_hello):
                self.m_learned_inspected.inc()
                return
            # רמת גלישה: כרום לאתר שלא פתוח ללקוח — נבדק (דף חסימה), גם אם אפליקציה "לימדה" אותו
            if pol.web.active and sni and is_chromium_hello(data.client_hello) \
                    and pol.web.decide(sni, self.web.verdict(sni)) != "allow":
                return
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
        if req.path.startswith(REVIEW_PATH):
            # "שליחה לבדיקה" מדף החסימה — נענה כאן (request), לא יוצא לאתר
            flow.metadata["fp_review"] = True
            return
        if pol.blocks(host) or (pol.paths and pol.blocks_path(host, req.path)):
            flow.response = self._blocked_host_response(req, host)
            flow.metadata["fp_handled"] = True
            self.m_sites.inc()
            self._record_host_block(ip, host, req)
            return
        if pol.filtering and self._web_gate(flow, pol, ip, host):
            flow.metadata["fp_handled"] = True
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

    # ------------------------------------------------------------ רמות גלישה וחיפוש

    def _web_gate(self, flow: http.HTTPFlow, pol: DevicePolicy, ip: str, host: str) -> bool:
        """חיפוש (מילים מפורשות, לשוניות סגורות), אתרים למבוגרים ורמת הגלישה. True = נענה כאן."""
        req = flow.request
        nav = _is_navigation(req)
        sq = serp.search_query(host, req.path)
        if sq is not None:
            engine, query, suggest = sq
            if query and self.terms.matches(query):
                self.m_search.inc(kind="query")
                flow.response = self._no_results(nav and not suggest)
                self._spawn(self._record(ip, host, "search", "explicit search", "search", ""))
                return True
            if pol.web.active and engine == "google" and not suggest:
                tab = _google_tab(req.path)
                if tab and not (tab == "shopping" and "shopping_general" in pol.web.groups):
                    self.m_search.inc(kind="tab")
                    flow.response = self._no_results(nav)
                    return True
        if not nav:
            return False
        if self.web.is_adult(host):
            flow.response = self._page(block_page(host, "תוכן למבוגרים — חסום תמיד."))
            self._record_host_block(ip, host, req)
            return True
        if not pol.web.active:
            return False
        verdict = self.web.verdict(host)
        decision = pol.web.decide(host, verdict)
        if decision == "check" and self.web.waited(host) > CHECK_WAIT_S:
            decision, verdict = "block", "error"
        self.m_web.inc(result=decision)
        if decision == "allow":
            return False
        if decision == "check":
            if self.web.should_queue(host):
                self._spawn(self.web.queue_check(host))
            flow.response = self._page(checking_page(host))
            return True
        level = WEB_LEVELS.get(pol.web.level, {}).get("title", "")
        reason = f"האתר לא נכלל ברמת הגלישה שלך ({level})."
        if verdict in BLOCKED_VERDICTS:
            reason = f"{BLOCKED_VERDICTS[verdict]} — לא נכלל ברמת הגלישה שלך ({level})."
        flow.response = self._page(block_page(host, reason, review=True, url=req.pretty_url))
        self._record_host_block(ip, host, req)
        return True

    @staticmethod
    def _page(body: bytes, status: int = 200) -> http.Response:
        return http.Response.make(status, body, {"content-type": "text/html; charset=utf-8",
                                                 "cache-control": "no-store", "x-filterphone": "web"})

    def _no_results(self, nav: bool) -> http.Response:
        if nav:
            return self._page(serp.no_results_page())
        return http.Response.make(204, b"", {"cache-control": "no-store", "x-filterphone": "search"})

    async def request(self, flow: http.HTTPFlow) -> None:
        if not flow.metadata.get("fp_review"):
            return
        req = flow.request
        ip = _client_ip(flow)
        host = req.pretty_host
        if req.method == "POST":
            form = parse_qs(req.get_text(strict=False) or "")
            url = (form.get("url") or [f"https://{host}/"])[0]
            note = (form.get("note") or [""])[0]
            try:
                await self.web.review(ip, host, url, note)
            except Exception as e:
                log.warning("review request not recorded: %s", e)
            flow.response = self._page(review_sent_page(host))
        else:
            flow.response = self._page(block_page(host, "שליחה לבדיקה — מדף החסימה בלבד."))
        flow.metadata["fp_handled"] = True

    async def _filter_serp(self, flow: http.HTTPFlow) -> None:
        """דף תוצאות של Google / Bing: מוחקים תוצאות לאתרים שחסומים ללקוח."""
        ip = _client_ip(flow)
        pol = self.policies.get(ip)
        resp = flow.response
        body = bounded_content(resp, MAX_SERP_BYTES)
        if not body:
            return
        charset = "utf-8"
        for part in resp.headers.get("content-type", "").split(";")[1:]:
            k, _, v = part.strip().partition("=")
            if k.lower() == "charset" and v:
                charset = v.strip('"')
        try:
            text = body.decode(charset, "surrogateescape")
        except LookupError:
            return

        def hidden(h: str) -> bool:
            return self.web.is_adult(h) or pol.blocks(h) or \
                (pol.web.active and not pol.web.visible(h, self.web.verdict(h)))
        try:
            new, removed = await asyncio.to_thread(serp.filter_results, text, hidden)
        except Exception as e:          # דף שלא הצלחנו לפענח — עובר כמו שהוא (האתר עצמו עדיין נחסם בלחיצה)
            log.warning("serp filter failed: %s", e)
            return
        if not removed:
            return
        self.m_serp.inc(removed)
        for h in ("etag", "last-modified", "content-md5", "digest"):
            resp.headers.pop(h, None)
        resp.content = new.encode(charset, "surrogateescape")

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
            if pol.level <= 0 and pol.images != "block":
                resp.stream = True
                return
            if size is not None and size > self.cfg.max_image_bytes:
                # גדולה מכדי לבדוק: ברמות 1–2 לא עוברת בלי בדיקה (כמו כל תמונה שאי אפשר לבדוק)
                if on_failure(pol.level).action is Action.BLOCK or pol.images == "block":
                    self.m_oversize.inc(kind="image_large")
                    flow.kill()
                else:
                    resp.stream = True
                return
            flow.metadata["fp_kind"] = "image"
            return
        if _ctype(resp) == "text/html" and resp.status_code == 200 \
                and serp.search_query(flow.request.pretty_host, flow.request.path) is not None \
                and (size is None or size <= MAX_SERP_BYTES):
            flow.metadata["fp_kind"] = "serp"
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
        elif kind == "serp":
            await self._filter_serp(flow)
            pol = self.policies.get(_client_ip(flow))
            if pol.level > 0 or pol.images == "block":
                await self._filter_inline(flow)     # התמונות המוטמעות בדף התוצאות

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
