"""ה-API הציבורי (מאחורי Caddy, https://api.filterphone.com).

    /health                     — בדיקת חיות
    POST /v1/enroll             — הרשמת מכשיר עם קוד הפעלה (מהאפליקציה)
    GET  /v1/device             — מצב המכשיר (טוקן מכשיר)
    GET  /v1/device/vpn         — הגדרות ה-VPN מחדש (למשל אחרי שחזור)
    GET  /v1/ca.crt             — תעודת הסינון להתקנה בטלפון
    /v1/admin/...               — ניהול (טוקן מנהל)
    /admin                      — ממשק הניהול
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

import redis
from fastapi import Depends, FastAPI, Header, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..settings import get_settings
from . import db
from .security import RateLimiter
from .service import LEVEL_NAMES, ApiError, Services
from .wg import WireGuard, WireGuardError

log = logging.getLogger("filterphone.api")
STATIC = Path(__file__).parent / "static"

enroll_limiter = RateLimiter(limit=10, window_s=600)
login_limiter = RateLimiter(limit=8, window_s=600)


def build_services() -> Services:
    cfg = get_settings()
    db.init(cfg.db_path)
    return Services(cfg, WireGuard(cfg.wg_interface),
                    redis.Redis.from_url(cfg.redis_url, decode_responses=True, socket_timeout=2))


@asynccontextmanager
async def lifespan(app: FastAPI):
    svc: Services = app.state.svc
    try:
        svc.sync_all()
    except (WireGuardError, redis.RedisError) as e:
        log.error("initial sync failed: %s", e)
    yield


def create_app(svc: Services | None = None) -> FastAPI:
    app = FastAPI(title="FilterPhone API", version="2.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.svc = svc or build_services()

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, e: ApiError):
        return JSONResponse({"error": e.message}, status_code=e.status)

    @app.exception_handler(WireGuardError)
    async def _wg_error(_: Request, e: WireGuardError):
        log.error("wireguard: %s", e)
        return JSONResponse({"error": "שגיאת VPN בשרת"}, status_code=500)

    @app.middleware("http")
    async def _security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers.setdefault("Cache-Control", "no-store")
        if request.url.path.startswith("/admin"):
            resp.headers["Content-Security-Policy"] = (
                "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self';"
                " frame-ancestors 'none'")
        return resp

    _routes(app)
    app.mount("/admin/static", StaticFiles(directory=STATIC), name="static")
    return app


# ---------------------------------------------------------------- תלויות

def svc_dep(request: Request) -> Services:
    return request.app.state.svc


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise ApiError(401, "נדרשת הזדהות")
    return authorization[7:].strip()


def device_dep(svc: Annotated[Services, Depends(svc_dep)],
               authorization: Annotated[str | None, Header()] = None):
    return svc.device_by_token(_bearer(authorization))


def admin_dep(svc: Annotated[Services, Depends(svc_dep)],
              authorization: Annotated[str | None, Header()] = None):
    return svc.admin_by_token(_bearer(authorization))


SvcDep = Annotated[Services, Depends(svc_dep)]
DeviceRow = Annotated[Any, Depends(device_dep)]
AdminRow = Annotated[Any, Depends(admin_dep)]
AuthHeader = Annotated[str | None, Header()]


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


# ---------------------------------------------------------------- מודלים

class EnrollIn(BaseModel):
    code: str = Field(min_length=6, max_length=32)
    public_key: str = Field(min_length=44, max_length=44)
    name: str = Field(default="", max_length=60)
    platform: str = Field(default="", max_length=20)
    app_version: str = Field(default="", max_length=20)


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=60)
    password: str = Field(min_length=1, max_length=200)


class DevicePatch(BaseModel):
    name: str | None = Field(default=None, max_length=60)
    label: str | None = Field(default=None, max_length=80)
    level: int | None = None
    status: str | None = None


class CodeIn(BaseModel):
    label: str = Field(default="", max_length=80)
    level: int = 1
    uses: int = 1
    expires_days: int | None = 30


# ---------------------------------------------------------------- נתיבים

def _routes(app: FastAPI) -> None:
    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/")
    def root():
        return RedirectResponse("/admin")

    # --- אפליקציה ---

    @app.post("/v1/enroll", status_code=201)
    def enroll(body: EnrollIn, request: Request, svc: SvcDep):
        if not enroll_limiter.allow(client_ip(request)):
            raise ApiError(429, "יותר מדי ניסיונות. נסו שוב בעוד כמה דקות")
        return svc.enroll(body.code, body.public_key, body.name, body.platform, body.app_version)

    @app.get("/v1/device")
    def device(request: Request, svc: SvcDep, row: DeviceRow):
        return svc.device_status(row, client_ip(request))

    @app.get("/v1/device/vpn")
    def device_vpn(svc: SvcDep, row: DeviceRow):
        if row["status"] != "active":
            raise ApiError(403, "המכשיר מושבת")
        return svc.device_vpn(row)

    @app.get("/v1/ca.crt")
    def ca(svc: SvcDep):
        return PlainTextResponse(svc.ca_pem(), media_type="application/x-x509-ca-cert",
                                 headers={"Content-Disposition": 'attachment; filename="FilterPhone-CA.crt"'})

    # --- ניהול ---

    @app.post("/v1/admin/login")
    def login(body: LoginIn, request: Request, svc: SvcDep):
        if not login_limiter.allow(client_ip(request)):
            raise ApiError(429, "יותר מדי ניסיונות. נסו שוב בעוד כמה דקות")
        return svc.admin_login(body.username, body.password)

    @app.post("/v1/admin/logout", status_code=204)
    def logout(svc: SvcDep, _: AdminRow, authorization: AuthHeader = None):
        svc.admin_logout(_bearer(authorization))

    @app.get("/v1/admin/overview")
    def overview(svc: SvcDep, _: AdminRow):
        devices = svc.list_devices()
        totals: dict[str, int] = {}
        for d in devices:
            for k, v in d["today"].items():
                totals[k] = totals.get(k, 0) + v
        return {"devices": len(devices), "online": sum(d["online"] for d in devices),
                "active": sum(d["status"] == "active" for d in devices),
                "today": totals, "levels": LEVEL_NAMES}

    @app.get("/v1/admin/devices")
    def devices(svc: SvcDep, _: AdminRow):
        return svc.list_devices()

    @app.patch("/v1/admin/devices/{device_id}")
    def patch_device(device_id: int, body: DevicePatch, svc: SvcDep, _: AdminRow):
        svc.update_device(device_id, body.model_dump())
        return {"ok": True}

    @app.delete("/v1/admin/devices/{device_id}", status_code=204)
    def delete_device(device_id: int, svc: SvcDep, _: AdminRow):
        svc.delete_device(device_id)

    @app.post("/v1/admin/devices/{device_id}/reset-apps")
    def reset_apps(device_id: int, svc: SvcDep, _: AdminRow):
        row = next((d for d in svc.list_devices() if d["id"] == device_id), None)
        if row is None:
            raise ApiError(404, "מכשיר לא נמצא")
        return {"cleared": svc.clear_passthrough(row["vpn_ip"])}

    @app.get("/v1/admin/codes")
    def codes(svc: SvcDep, _: AdminRow):
        return svc.list_codes()

    @app.post("/v1/admin/codes", status_code=201)
    def create_code(body: CodeIn, svc: SvcDep, _: AdminRow):
        return svc.create_code(body.label, body.level, body.uses, body.expires_days)

    @app.delete("/v1/admin/codes/{code_id}", status_code=204)
    def delete_code(code_id: int, svc: SvcDep, _: AdminRow):
        svc.delete_code(code_id)

    @app.get("/v1/admin/events")
    def events(svc: SvcDep, _: AdminRow, limit: int = 100):
        return svc.recent_events(limit)

    # --- ממשק הניהול ---

    @app.get("/admin")
    def admin_page():
        return FileResponse(STATIC / "admin.html", headers={"Cache-Control": "no-cache"})

