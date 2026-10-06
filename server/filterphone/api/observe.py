"""ניטור ה-API: מדדי בקשות, מגבלת גודל לגוף הבקשה, ולוג לבקשות איטיות / שגיאות.

שני middleware של ASGI (בלי לקרוא את הגוף לזיכרון מראש):
  BodyLimit      — Content-Length גדול מהמותר → 413 מיד; גוף בזרימה (chunked) — נספר תוך כדי.
  RequestMetrics — מונה בקשות לפי נתיב (התבנית, לא הכתובת עם המספרים), סטטוס ומשך.
"""
from __future__ import annotations

import logging
import time
import uuid

from .. import metrics

log = logging.getLogger("filterphone.api")

REGISTRY = metrics.Registry("fp_api")
REQUESTS = REGISTRY.counter("requests_total", "בקשות ל-API לפי שיטה, נתיב ומחלקת סטטוס")
LATENCY = REGISTRY.histogram("request_seconds", "משך בקשה לפי נתיב")
ACTIVE = REGISTRY.gauge("active_requests", "בקשות בטיפול עכשיו")
ERRORS = REGISTRY.counter("errors_total", "שגיאות לפי סוג (4xx/5xx/exception)")
SLOW_S = 2.0


class BodyTooLarge(Exception):
    pass


def _route(scope) -> str:
    route = scope.get("route")
    path = getattr(route, "path", None)
    if path:
        return path
    p = scope.get("path", "")
    return "/admin/static" if p.startswith("/admin/static/") else "unmatched"


class BodyLimit:
    def __init__(self, app, default: int, overrides: dict[str, int] | None = None):
        self.app = app
        self.default = default
        self.overrides = overrides or {}

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        limit = self.overrides.get(scope.get("path", ""), self.default)
        for name, value in scope.get("headers", ()):
            if name == b"content-length":
                try:
                    too_big = int(value) > limit
                except ValueError:
                    too_big = True
                if too_big:
                    return await _reject(send)
        received = 0
        exceeded = started = False

        async def limited():
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    exceeded = True
                    raise BodyTooLarge()
            return message

        async def guarded_send(message):
            nonlocal started
            if exceeded:
                return                  # התשובה של האפליקציה (למשל 400 "גוף לא תקין") — מוחלפת ב-413
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited, guarded_send)
        except BodyTooLarge:
            pass
        if exceeded and not started:
            await _reject(send)


async def _reject(send) -> None:
    body = '{"error":"הבקשה גדולה מדי"}'.encode()
    await send({"type": "http.response.start", "status": 413,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


class RequestMetrics:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        t0 = time.perf_counter()
        status = 500
        rid = uuid.uuid4().hex[:12]

        async def tracked_send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message.setdefault("headers", [])
                message["headers"] = [*message["headers"], (b"x-request-id", rid.encode())]
            await send(message)

        ACTIVE.inc()
        try:
            await self.app(scope, receive, tracked_send)
        except Exception:
            ERRORS.inc(kind="exception")
            log.exception("unhandled error", extra={"fields": {"request_id": rid, "route": _route(scope)}})
            raise
        finally:
            ACTIVE.dec()
            dt = time.perf_counter() - t0
            route = _route(scope)
            REQUESTS.inc(method=scope.get("method", ""), route=route, status=f"{status // 100}xx")
            LATENCY.observe(dt, route=route)
            if status >= 400:
                ERRORS.inc(kind=f"{status // 100}xx")
            if status >= 500 or dt > SLOW_S:
                # בלי query string ובלי גוף — רק התבנית של הנתיב
                log.warning("request %s %s → %d in %.2fs", scope.get("method"), route, status, dt,
                            extra={"fields": {"request_id": rid, "route": route, "status": status,
                                              "duration_ms": round(dt * 1000)}})
