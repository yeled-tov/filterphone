"""ה-API הציבורי (מאחורי Caddy, https://api.filterphone.com).

אפליקציה:
    GET  /v1/catalog            — רמות ואפשרויות חסימה (לטופס ההרשמה)
    POST /v1/register           — בקשת הצטרפות (שם, טלפון, מייל, רמה מבוקשת, חסימות, הערות)
    POST /v1/enroll             — הפעלה עם קוד (הדרך הקודמת — עדיין נתמכת)
    GET  /v1/device             — מצב המכשיר: ממתין / פעיל / מושבת, חסימות פעילות (טוקן מכשיר)
    DELETE /v1/device           — ביטול הרשמה שעוד לא אושרה
    GET  /v1/device/vpn         — הגדרות ה-VPN + התעודה (רק למכשיר פעיל)
    POST /v1/device/requests    — בקשת שינוי (רמה / חסימות) — רק המנהל מבצע
    POST /v1/device/report      — דיווח: אפליקציות מותקנות + מצב ההגנה בטלפון
    GET  /v1/ca.crt             — תעודת הסינון להתקנה בטלפון
ניהול (טוקן מנהל): /v1/admin/...      ממשק הניהול: /admin
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

import redis
from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..settings import get_settings
from . import db
from .catalog import admin_catalog, public_catalog
from .fetch import FetchError, fetch_image
from .security import RateLimiter
from .service import LEVEL_NAMES, ApiError, Services
from .wg import WireGuard, WireGuardError

log = logging.getLogger("filterphone.api")
STATIC = Path(__file__).parent / "static"

FIELD_NAMES = {"name": "שם", "phone": "טלפון", "email": "מייל", "level": "רמת סינון", "note": "הערות",
               "options": "אפשרויות", "code": "קוד הפעלה", "public_key": "מפתח", "username": "שם משתמש",
               "password": "סיסמה"}

enroll_limiter = RateLimiter(limit=10, window_s=600)
register_limiter = RateLimiter(limit=5, window_s=3600)
login_limiter = RateLimiter(limit=8, window_s=600)
device_limiter = RateLimiter(limit=30, window_s=600)       # בקשות שינוי / דיווחים מהטלפון


def build_services() -> Services:
    cfg = get_settings()
    db.init(cfg.db_path)
    return Services(cfg, WireGuard(cfg.wg_interface),
                    redis.Redis.from_url(cfg.redis_url, decode_responses=True, socket_timeout=2))


RECONCILE_EVERY_S = 60
ADGUARD_EVERY_S = 300


async def _keep_in_sync(svc: Services) -> None:
    """בעלייה: סנכרון עם ניסיונות חוזרים (בזמן אתחול השרת שירותים עוד עולים).
    אחר כך: יישור WireGuard כל דקה, ו-AdGuard מיד כשחסימות משתנות (ולפחות כל 5 דקות)."""
    for attempt in range(1, 11):
        try:
            await asyncio.to_thread(svc.sync_all)
            log.info("initial sync ok: %d active devices", len(await asyncio.to_thread(svc._active_devices)))
            break
        except Exception as e:
            log.warning("initial sync attempt %d failed: %s", attempt, e)
            await asyncio.sleep(3)
    last_wg = last_dns = time.monotonic()
    while True:
        dirty = await asyncio.to_thread(svc.dirty.wait, 2.0)
        now = time.monotonic()
        if now - last_wg >= RECONCILE_EVERY_S:
            last_wg = now
            try:
                added, removed = await asyncio.to_thread(svc.sync_wireguard)
                if added or removed:
                    log.warning("wireguard drift repaired: +%d -%d", added, removed)
            except Exception as e:
                log.error("wireguard reconcile failed: %s", e)
        if dirty or now - last_dns >= ADGUARD_EVERY_S:
            svc.dirty.clear()
            last_dns = now
            try:
                await asyncio.to_thread(svc.sync_adguard, not dirty)
            except Exception as e:
                log.error("adguard sync failed: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_keep_in_sync(app.state.svc))
    yield
    task.cancel()
    app.state.svc.dirty.set()           # מעיר את החוט שמחכה, כדי שהכיבוי לא יתעכב


def create_app(svc: Services | None = None) -> FastAPI:
    if not logging.getLogger().handlers:      # uvicorn מגדיר רק את הלוגרים שלו
        logging.basicConfig(level=os.environ.get("FP_LOG_LEVEL", "INFO"),
                            format="%(levelname)s %(name)s: %(message)s")
    app = FastAPI(title="FilterPhone API", version="2.1", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.svc = svc or build_services()

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, e: ApiError):
        return JSONResponse({"error": e.message}, status_code=e.status)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, e: RequestValidationError):
        fields = {str(err.get("loc", ["", ""])[-1]) for err in e.errors()}
        names = [FIELD_NAMES.get(f, f) for f in sorted(fields)]
        return JSONResponse({"error": f"נתונים לא תקינים: {', '.join(names)}"}, status_code=400)

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


def _limit(limiter: RateLimiter, key: str) -> None:
    if not limiter.allow(key):
        raise ApiError(429, "יותר מדי ניסיונות. נסו שוב בעוד כמה דקות")


# ---------------------------------------------------------------- מודלים

class EnrollIn(BaseModel):
    code: str = Field(min_length=6, max_length=32)
    public_key: str = Field(min_length=44, max_length=44)
    name: str = Field(default="", max_length=60)
    platform: str = Field(default="", max_length=20)
    app_version: str = Field(default="", max_length=20)


class RegisterIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    phone: str = Field(min_length=1, max_length=30)
    email: str = Field(default="", max_length=200)
    level: int = Field(ge=1, le=3)
    options: list[str] = Field(default_factory=list, max_length=100)
    note: str = Field(default="", max_length=3000)
    public_key: str = Field(min_length=44, max_length=44)
    device_name: str = Field(default="", max_length=60)
    platform: str = Field(default="", max_length=20)
    app_version: str = Field(default="", max_length=20)


class ChangeIn(BaseModel):
    level: int | None = None
    options: list[str] | None = Field(default=None, max_length=100)
    note: str = Field(default="", max_length=3000)


class AppInfo(BaseModel):
    package: str = Field(max_length=200)
    label: str = Field(default="", max_length=200)
    system: bool = False


class ReportIn(BaseModel):
    apps: list[AppInfo] | None = Field(default=None, max_length=1500)
    guard: dict[str, bool | str | int] | None = None


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=60)
    password: str = Field(min_length=1, max_length=200)


class DevicePatch(BaseModel):
    name: str | None = Field(default=None, max_length=60)
    label: str | None = Field(default=None, max_length=80)
    level: int | None = None
    status: str | None = None
    options: list[str] | None = Field(default=None, max_length=200)
    custom_hosts: list[str] | None = Field(default=None, max_length=500)
    allow_hosts: list[str] | None = Field(default=None, max_length=500)
    custom_apps: list[str] | None = Field(default=None, max_length=500)
    app_images: str | None = None


class Decision(BaseModel):
    level: int | None = None
    options: list[str] | None = Field(default=None, max_length=200)
    custom_hosts: list[str] | None = Field(default=None, max_length=500)
    allow_hosts: list[str] | None = Field(default=None, max_length=500)
    custom_apps: list[str] | None = Field(default=None, max_length=500)
    app_images: str | None = None
    payment: str | None = None
    paid_until: int | None = None
    admin_note: str = Field(default="", max_length=1000)


class RejectIn(BaseModel):
    admin_note: str = Field(default="", max_length=1000)


class CustomerPatch(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    phone: str | None = Field(default=None, max_length=30)
    email: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=4000)
    payment: str | None = None
    paid_until: int | None = None


class CodeIn(BaseModel):
    label: str = Field(default="", max_length=80)
    level: int = 1
    uses: int = 1
    expires_days: int | None = 30


class TestImageIn(BaseModel):
    url: str | None = Field(default=None, max_length=4000)
    data: str | None = Field(default=None, max_length=17_000_000)      # base64 (העלאת קובץ)
    level: int = 2


# ---------------------------------------------------------------- נתיבים

def _routes(app: FastAPI) -> None:
    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/")
    def root():
        return RedirectResponse("/admin")

    # --- אפליקציה ---

    @app.get("/v1/catalog")
    def catalog():
        return public_catalog()

    @app.post("/v1/register", status_code=201)
    def register(body: RegisterIn, request: Request, svc: SvcDep):
        _limit(register_limiter, client_ip(request))
        return svc.register(name=body.name, phone=body.phone, email=body.email, level=body.level,
                            options=body.options, note=body.note, public_key=body.public_key,
                            device_name=body.device_name, platform=body.platform, app_version=body.app_version)

    @app.post("/v1/enroll", status_code=201)
    def enroll(body: EnrollIn, request: Request, svc: SvcDep):
        _limit(enroll_limiter, client_ip(request))
        return svc.enroll(body.code, body.public_key, body.name, body.platform, body.app_version)

    @app.get("/v1/device")
    def device(request: Request, svc: SvcDep, row: DeviceRow):
        return svc.device_status(row, client_ip(request))

    @app.delete("/v1/device", status_code=204)
    def device_cancel(svc: SvcDep, row: DeviceRow):
        svc.device_cancel(row)

    @app.get("/v1/device/vpn")
    def device_vpn(svc: SvcDep, row: DeviceRow):
        return svc.device_vpn(row)

    @app.post("/v1/device/requests", status_code=201)
    def device_request(body: ChangeIn, svc: SvcDep, row: DeviceRow):
        _limit(device_limiter, f"req:{row['id']}")
        return svc.device_request_change(row, body.level, body.options, body.note)

    @app.post("/v1/device/report")
    def device_report(body: ReportIn, svc: SvcDep, row: DeviceRow):
        _limit(device_limiter, f"rep:{row['id']}")
        apps = [a.model_dump() for a in body.apps] if body.apps is not None else None
        return svc.report_device(row, apps, body.guard)

    @app.get("/v1/ca.crt")
    def ca(svc: SvcDep):
        return PlainTextResponse(svc.ca_pem(), media_type="application/x-x509-ca-cert",
                                 headers={"Content-Disposition": 'attachment; filename="FilterPhone-CA.crt"'})

    # --- ניהול: כניסה ---

    @app.post("/v1/admin/login")
    def login(body: LoginIn, request: Request, svc: SvcDep):
        _limit(login_limiter, client_ip(request))
        return svc.admin_login(body.username, body.password)

    @app.post("/v1/admin/logout", status_code=204)
    def logout(svc: SvcDep, _: AdminRow, authorization: AuthHeader = None):
        svc.admin_logout(_bearer(authorization))

    @app.get("/v1/admin/overview")
    def overview(svc: SvcDep, admin: AdminRow):
        return {**svc.overview(), "admin": admin["username"]}

    @app.get("/v1/admin/catalog")
    def catalog_admin(_: AdminRow):
        return admin_catalog()

    # --- ניהול: בקשות ---

    @app.get("/v1/admin/requests")
    def requests_(svc: SvcDep, _: AdminRow, status: str = "pending"):
        return svc.list_requests(None if status == "all" else status)

    @app.post("/v1/admin/requests/{request_id}/approve")
    def approve(request_id: int, body: Decision, svc: SvcDep, admin: AdminRow):
        return svc.approve_request(request_id, admin["username"], body.model_dump(exclude_unset=True))

    @app.post("/v1/admin/requests/{request_id}/reject", status_code=204)
    def reject(request_id: int, body: RejectIn, svc: SvcDep, admin: AdminRow):
        svc.reject_request(request_id, admin["username"], body.admin_note)

    # --- ניהול: מכשירים ---

    @app.get("/v1/admin/devices")
    def devices(svc: SvcDep, _: AdminRow):
        return svc.list_devices()

    @app.get("/v1/admin/devices/{device_id}")
    def device_detail(device_id: int, svc: SvcDep, _: AdminRow):
        d = svc.device_detail(device_id)
        if d["vpn_ip"]:
            d["learned"] = svc.learned_passthrough(d["vpn_ip"])
        return d

    @app.patch("/v1/admin/devices/{device_id}")
    def patch_device(device_id: int, body: DevicePatch, svc: SvcDep, admin: AdminRow):
        svc.update_device(device_id, body.model_dump(exclude_unset=True), admin["username"])
        return svc.device_detail(device_id)

    @app.delete("/v1/admin/devices/{device_id}", status_code=204)
    def delete_device(device_id: int, svc: SvcDep, admin: AdminRow):
        svc.delete_device(device_id, admin["username"])

    @app.post("/v1/admin/devices/{device_id}/reset-apps")
    def reset_apps(device_id: int, svc: SvcDep, admin: AdminRow):
        d = svc.device_row(device_id)
        if not d["vpn_ip"]:
            raise ApiError(409, "למכשיר אין עדיין כתובת VPN")
        n = svc.clear_passthrough(d["vpn_ip"])
        svc.audit(admin["username"], "reset_apps", f"device:{device_id}", str(n))
        return {"cleared": n}

    # --- ניהול: לקוחות ---

    @app.get("/v1/admin/customers")
    def customers(svc: SvcDep, _: AdminRow):
        return svc.list_customers()

    @app.patch("/v1/admin/customers/{customer_id}")
    def patch_customer(customer_id: int, body: CustomerPatch, svc: SvcDep, admin: AdminRow):
        return svc.update_customer(customer_id, body.model_dump(exclude_unset=True), admin["username"])

    # --- ניהול: קודים ---

    @app.get("/v1/admin/codes")
    def codes(svc: SvcDep, _: AdminRow):
        return svc.list_codes()

    @app.post("/v1/admin/codes", status_code=201)
    def create_code(body: CodeIn, svc: SvcDep, admin: AdminRow):
        r = svc.create_code(body.label, body.level, body.uses, body.expires_days)
        svc.audit(admin["username"], "code", f"code:{r['id']}", f"{body.label} level:{body.level}")
        return r

    @app.delete("/v1/admin/codes/{code_id}", status_code=204)
    def delete_code(code_id: int, svc: SvcDep, admin: AdminRow):
        svc.delete_code(code_id)
        svc.audit(admin["username"], "code_delete", f"code:{code_id}")

    # --- ניהול: אירועים, סטטיסטיקה, יומן ---

    @app.get("/v1/admin/events")
    def events(svc: SvcDep, _: AdminRow, limit: int = 100, device: int | None = None, kind: str | None = None,
               action: str | None = None, q: str | None = None):
        ip = None
        if device is not None:
            ip = svc.device_row(device)["vpn_ip"] or "-"
        return svc.recent_events(limit, ip=ip, kind=kind, action=action, q=q)

    @app.get("/v1/admin/stats")
    def stats(svc: SvcDep, _: AdminRow, days: int = 14):
        return svc.stats_range(days)

    @app.get("/v1/admin/audit")
    def audit(svc: SvcDep, _: AdminRow, limit: int = 100):
        return svc.list_audit(limit)

    # --- ניהול: כלים ---

    @app.post("/v1/admin/test-image")
    def test_image(body: TestImageIn, svc: SvcDep, admin: AdminRow):
        if body.data:
            try:
                data = base64.b64decode(body.data.split(",", 1)[-1], validate=False)
            except (binascii.Error, ValueError) as e:
                raise ApiError(400, "הקובץ לא תקין") from e
        elif body.url:
            try:
                data, _ = fetch_image(body.url, svc.cfg.max_image_bytes)
            except FetchError as e:
                raise ApiError(400, str(e)) from e
        else:
            raise ApiError(400, "צריך כתובת תמונה או קובץ")
        return svc.test_image(data, body.level)

    @app.post("/v1/admin/adguard/sync")
    def adguard_sync(svc: SvcDep, admin: AdminRow):
        changed = svc.sync_adguard(force=True)
        assert svc.adguard is not None
        return {**svc.adguard.state(), "changed": changed}

    @app.get("/v1/admin/levels")
    def levels(_: AdminRow):
        return LEVEL_NAMES

    # --- ממשק הניהול ---

    @app.get("/admin")
    def admin_page():
        return FileResponse(STATIC / "admin.html", headers={"Cache-Control": "no-cache"})
