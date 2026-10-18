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
    POST /v1/device/rekey       — מפתח VPN חדש (הטלפון איבד את הקודם) — אותה כתובת ומדיניות
    POST /v1/device/heartbeat   — דיווח תקופתי (גם מהרקע): VPN, נגישות, מנהל מכשיר, סוללה,
                                  גרסת המדיניות שהוחלה. התשובה: המדיניות העדכנית אם הטלפון מאחור
    GET  /v1/ca.crt             — תעודת הסינון להתקנה בטלפון
ניהול (טוקן מנהל): /v1/admin/...      ממשק הניהול: /admin
ניטור: /health (התהליך חי), /ready (מסד, Redis, מנוע, WireGuard, AdGuard), /metrics (Prometheus —
       חסום ב-Caddy, רק מתוך השרת).
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

import redis
from fastapi import BackgroundTasks, Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import logs, metrics
from ..settings import get_settings
from . import db, gpu, observe, security
from .bot import BotDesk
from .catalog import DEFAULT_OPTIONS, admin_catalog, public_catalog
from .fetch import FetchError, fetch_image
from .playinfo import PlayInfo
from .security import RateLimiter
from .service import LEVEL_NAMES, ApiError, Services, store_seed
from .support import SupportDesk
from .wabot import WaBot
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
heartbeat_limiter = RateLimiter(limit=40, window_s=600)    # דיווח תקופתי: כל 15 דקות + רענון ידני
rekey_limiter = RateLimiter(limit=3, window_s=86400)       # החלפת מפתח VPN — נדיר מאוד
restore_limiter = RateLimiter(limit=8, window_s=3600)      # שחזור לפי מספר טלפון — לכל כתובת ולכל מספר
support_limiter = RateLimiter(limit=20, window_s=600)     # הודעות לתמיכה מטלפון אחד
media_limiter = RateLimiter(limit=600, window_s=600)      # תמונות החנות (אייקונים / צילומי מסך) לכל כתובת
updates_limiter = RateLimiter(limit=12, window_s=600)     # בדיקת עדכונים מול Google Play (בפתיחת הטאב)
MAX_APK_UPLOAD = 300 * 1024 * 1024
_APK_FILE = re.compile(r"^[A-Za-z][A-Za-z0-9_.]{2,199}-\d{1,10}\.apk$")


def build_services() -> Services:
    cfg = get_settings()
    db.init(cfg.db_path)
    # מאגר חיבורים משותף לכל החוטים; ניסיון חוזר עם השהיה הולכת וגדלה בתקלה רגעית
    from redis.backoff import ExponentialBackoff
    from redis.retry import Retry
    r = redis.Redis.from_url(cfg.redis_url, decode_responses=True, socket_timeout=2, socket_connect_timeout=2,
                             max_connections=64, health_check_interval=30,
                             retry=Retry(ExponentialBackoff(cap=1.0, base=0.05), 3),
                             retry_on_error=[redis.ConnectionError, redis.TimeoutError])
    return Services(cfg, WireGuard(cfg.wg_interface), r)


RECONCILE_EVERY_S = 60
ADGUARD_EVERY_S = 300
PRUNE_EVERY_S = 86400


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
    last_prune = time.monotonic() - PRUNE_EVERY_S       # ניקוי הודעות תמיכה ישנות — גם מיד בעלייה
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
            try:
                await asyncio.to_thread(svc.sync_redis_if_needed)
            except Exception as e:
                log.error("redis policy resync failed: %s", e)
        try:
            # פתיחות זמניות שפג תוקפן — נחסמות שוב (זול כשאין: בודק רק כשהגיע הזמן של הקרובה)
            await asyncio.to_thread(svc.expire_temp_opens)
        except Exception as e:
            log.error("temp-open expiry failed: %s", e)
        if now - last_prune >= PRUNE_EVERY_S:
            last_prune = now
            try:
                await asyncio.to_thread(SupportDesk(svc).prune)
            except Exception as e:
                log.error("support prune failed: %s", e)
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
    app.state.svc.pool.close()


def create_app(svc: Services | None = None) -> FastAPI:
    logs.setup(os.environ.get("FP_LOG_LEVEL", "INFO"))
    app = FastAPI(title="FilterPhone API", version="2.2", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    # Allow the first-party FilterSmart CRM to call this API from the browser.
    # Keep the origin allowlist explicit; no wildcard origins for authenticated admin endpoints.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["https://www.filterphone.com", "https://filterphone.com"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )
    app.state.svc = svc or build_services()
    app.state.support = SupportDesk(app.state.svc)
    app.state.playinfo = PlayInfo(app.state.svc.redis)
    cfg = app.state.svc.cfg

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, e: ApiError):
        return JSONResponse({"error": e.message}, status_code=e.status)

    @app.exception_handler(observe.BodyTooLarge)
    async def _too_large(_: Request, e: observe.BodyTooLarge):
        return JSONResponse({"error": "הבקשה גדולה מדי"}, status_code=413)

    @app.exception_handler(db.PoolTimeout)
    async def _busy(_: Request, e: db.PoolTimeout):
        return JSONResponse({"error": "השרת עמוס כרגע. נסו שוב בעוד רגע"}, status_code=503,
                            headers={"Retry-After": "5"})

    @app.exception_handler(redis.RedisError)
    async def _redis_down(_: Request, e: redis.RedisError):
        log.error("redis error: %s", e)
        return JSONResponse({"error": "השרת לא זמין כרגע. נסו שוב בעוד רגע"}, status_code=503,
                            headers={"Retry-After": "10"})

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
    # הסדר: המדדים עוטפים הכול (גם 413), ומגבלת הגוף לפני קריאת ה-JSON
    app.add_middleware(observe.BodyLimit, default=cfg.max_body_bytes,
                       overrides={"/v1/admin/test-image": int(cfg.max_image_bytes * 1.4) + 4096,
                                  "/v1/device/report": 2_000_000})
    app.add_middleware(observe.RequestMetrics)
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


def support_dep(request: Request) -> SupportDesk:
    return request.app.state.support


def bot_dep(request: Request, authorization: Annotated[str | None, Header()] = None) -> BotDesk:
    desk = BotDesk(request.app.state.svc)
    if not desk.authorized(_bearer(authorization)):
        raise ApiError(401, "בוט לא מזוהה")
    return desk


SvcDep = Annotated[Services, Depends(svc_dep)]
DeskDep = Annotated[SupportDesk, Depends(support_dep)]
BotDep = Annotated[BotDesk, Depends(bot_dep)]
DeviceRow = Annotated[Any, Depends(device_dep)]
AdminRow = Annotated[Any, Depends(admin_dep)]
AuthHeader = Annotated[str | None, Header()]


def _state_matches(d: dict, state: str) -> bool:
    c = d["compliance"]
    return {
        "offline": d["status"] == "active" and not c["online"],
        "vpn_inactive": d["status"] == "active" and c["vpn"] != "up",
        "accessibility_off": c["accessibility"] == "off",
        "unmanaged": d["status"] == "active" and c["management"] != "device_owner",
    }.get(state, c["state"] == state)


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
    device_uid: str = Field(default="", max_length=64)


class RestoreIn(BaseModel):
    phone: str = Field(min_length=1, max_length=30)
    device_uid: str = Field(min_length=16, max_length=64)
    public_key: str = Field(min_length=44, max_length=44)
    name: str = Field(default="", max_length=60)
    platform: str = Field(default="", max_length=20)
    app_version: str = Field(default="", max_length=20)


class RestoreRequestIn(RestoreIn):
    mode: str = Field(pattern="^(swap|additional)$")
    old_device_id: int | None = None


class SwapDecision(BaseModel):
    approve: bool
    as_additional: bool = False


class ProfileIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    level: int
    settings: dict = Field(default_factory=dict)


class ApplyProfileIn(BaseModel):
    profile_id: int


class ManagedAppIn(BaseModel):
    package: str = Field(min_length=3, max_length=200)
    label: str = Field(default="", max_length=60)
    category: str = Field(default="", max_length=40)
    note: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=2000)
    screenshots: list[str] = Field(default_factory=list, max_length=8)
    icon_url: str = Field(default="", max_length=500)
    play_url: str = Field(default="", max_length=500)
    apk_url: str = Field(default="", max_length=500)
    apk_sha256: str = Field(default="", max_length=64)
    version_code: int | None = None
    required: bool = False
    enabled: bool = True
    sort: int = 0


class TransferIn(BaseModel):
    token: str = Field(min_length=10, max_length=80)
    public_key: str = Field(min_length=44, max_length=44)
    name: str = Field(default="", max_length=60)
    platform: str = Field(default="", max_length=20)
    app_version: str = Field(default="", max_length=20)
    device_uid: str = Field(default="", max_length=64)


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
    device_uid: str = Field(default="", max_length=64)


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


class RekeyIn(BaseModel):
    public_key: str = Field(min_length=44, max_length=44)


class HeartbeatIn(BaseModel):
    """שדות לא מוכרים נזרקים; סוגים לא נכונים — מתעלמים מהם (sanitize_heartbeat)."""
    model_config = {"extra": "allow"}
    policy_version: int | None = Field(default=None, ge=0, le=10**9)
    want_policy: bool = False


class SupportIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    diag: dict[str, bool | int | float | str] | None = None


class SupportReplyIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class BotReplyIn(BaseModel):
    device_id: int
    text: str = Field(min_length=1, max_length=4000)


class BotForwardedIn(BaseModel):
    ids: list[int] = Field(max_length=100)
    wa_ids: dict[int, str] | None = None


class BotBlockIn(BaseModel):
    device_id: int
    options: list[str] = Field(min_length=1, max_length=100)


class BotTempIn(BaseModel):
    device_id: int
    item: str = Field(min_length=1, max_length=80)
    minutes: int | None = Field(default=None, ge=1, le=60)


class BotEscalateIn(BaseModel):
    device_id: int
    note: str = Field(default="", max_length=1500)
    level: int | None = None
    options: list[str] | None = Field(default=None, max_length=100)


class WhatsAppIn(BaseModel):
    token: str | None = Field(default=None, max_length=1024)
    phone_number_id: str | None = Field(default=None, max_length=40)
    admin_number: str | None = Field(default=None, max_length=40)
    verify_token: str | None = Field(default=None, max_length=200)
    app_secret: str | None = Field(default=None, max_length=200)
    template: str | None = Field(default=None, max_length=512)
    template_lang: str | None = Field(default=None, max_length=10)
    public_number: str | None = Field(default=None, max_length=40)
    graph_version: str | None = Field(default=None, max_length=10)
    bot_token: str | None = Field(default=None, max_length=200)
    clear_secrets: bool = False


class WaBotIn(BaseModel):
    enabled: bool | None = None
    ai_provider: str | None = Field(default=None, max_length=20)
    ai_key: str | None = Field(default=None, max_length=300)
    ai_model: str | None = Field(default=None, max_length=80)
    business_info: str | None = Field(default=None, max_length=6000)
    hours: str | None = Field(default=None, max_length=120)
    friday_until: str | None = Field(default=None, max_length=5)
    handoff_minutes: int | None = None
    bot_name: str | None = Field(default=None, max_length=30)
    label_name: str | None = Field(default=None, max_length=30)
    auto_new: bool | None = None
    include_contacts: bool | None = None
    mark_ai: bool | None = None
    ai_tag: str | None = Field(default=None, max_length=60)
    learn_from_replies: bool | None = None
    ask_when_unsure: bool | None = None
    clear_key: bool = False


class RecognizeIn(BaseModel):
    device_uid: str = Field(min_length=16, max_length=64)


class BotStatusIn(BaseModel):
    state: str = Field(max_length=20)
    qr: str = Field(default="", max_length=2000)
    me: str = Field(default="", max_length=40)
    error: str = Field(default="", max_length=300)
    ai: str = Field(default="", max_length=80)
    version: str = Field(default="", max_length=20)
    labels: str = Field(default="", max_length=20)
    label: str = Field(default="", max_length=40)
    progress: int | None = None
    log: list[str] = Field(default_factory=list, max_length=60)
    stats: dict[str, int] = Field(default_factory=dict)


class KnowledgeIn(BaseModel):
    kind: str = Field(default="", max_length=10)
    question: str = Field(default="", max_length=2000)
    answer: str = Field(default="", max_length=6000)
    phone: str = Field(default="", max_length=40)
    source: str = Field(default="reply", max_length=20)


class KnowledgeUsedIn(BaseModel):
    ids: list[int] = Field(default_factory=list, max_length=50)


class BrandingIn(BaseModel):
    org_name: str = Field(min_length=1, max_length=60)


class InstalledAppIn(BaseModel):
    package: str = Field(max_length=200)
    version_name: str = Field(default="", max_length=80)
    updated_ms: int = Field(default=0, ge=0)


class AppUpdatesIn(BaseModel):
    apps: list[InstalledAppIn] = Field(default_factory=list, max_length=250)


class GpuIn(BaseModel):
    url: str | None = Field(default=None, max_length=300)
    key: str | None = Field(default=None, max_length=200)
    secret: str | None = Field(default=None, max_length=200)
    mode: str | None = Field(default=None, max_length=20)
    clear: bool = False


class ProvisionIn(BaseModel):
    level: int = 1
    label: str = Field(default="", max_length=80)
    wifi_ssid: str = Field(default="", max_length=32)
    wifi_password: str = Field(default="", max_length=63)
    wifi_security: str = Field(default="WPA", max_length=8)


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
    app_rules: list[dict] | None = Field(default=None, max_length=500)
    app_images: str | None = None
    dpc: dict | None = None
    keywords: dict | None = None
    vpn_bypass: dict | None = None


class PauseIn(BaseModel):
    minutes: int = Field(default=60, ge=1, le=1440)
    reason: str = Field(default="", max_length=200)


class TempOpenIn(BaseModel):
    item: str = Field(min_length=1, max_length=80)
    minutes: int = Field(default=10, ge=1, le=60)
    reason: str = Field(default="", max_length=200)


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
    options: list[str] | None = Field(default=None, max_length=100)   # None = ברירת המחדל ללקוח חדש


class TestImageIn(BaseModel):
    url: str | None = Field(default=None, max_length=4000)
    data: str | None = Field(default=None, max_length=17_000_000)      # base64 (העלאת קובץ)
    level: int = 2


# ---------------------------------------------------------------- נתיבים

def _routes(app: FastAPI) -> None:
    @app.get("/health")
    def health():
        """liveness: התהליך חי ועונה. בלי בדיקת תלויות (כדי שתקלה ב-Redis לא תגרום להפעלה מחדש)."""
        return {"ok": True}

    @app.get("/ready")
    def ready(svc: SvcDep):
        """readiness: מסד ו-Redis חיוניים; מנוע / WireGuard / AdGuard — מדווחים, לא חוסמים."""
        checks = svc.readiness()
        ok = checks["db"] and checks["redis"]
        return JSONResponse({"ready": ok, "checks": checks}, status_code=200 if ok else 503)

    @app.get("/metrics")
    def metrics_(svc: SvcDep):
        """Prometheus. Caddy חוסם את הנתיב מבחוץ — נגיש רק מתוך השרת."""
        parts = [observe.REGISTRY.render(), svc.device_metrics_text()]
        engine = svc.engine_metrics()
        parts.append(f"fp_engine_up {1 if engine else 0}\n")
        if engine:
            parts.append(metrics.render_snapshot(engine["metrics"], "engine"))
        proxy = svc.proxy_metrics()
        if proxy:
            parts.append(metrics.render_snapshot({k: v for k, v in proxy.items() if not k.startswith("_")},
                                                 "proxy"))
        return Response("".join(parts), media_type="text/plain; version=0.0.4")

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
                            device_name=body.device_name, platform=body.platform, app_version=body.app_version,
                            device_uid=body.device_uid)

    @app.post("/v1/enroll", status_code=201)
    def enroll(body: EnrollIn, request: Request, svc: SvcDep):
        _limit(enroll_limiter, client_ip(request))
        return svc.enroll(body.code, body.public_key, body.name, body.platform, body.app_version, body.device_uid)

    @app.post("/v1/transfer", status_code=201)
    def transfer(body: TransferIn, request: Request, svc: SvcDep):
        _limit(enroll_limiter, client_ip(request))
        return svc.transfer_enroll(body.token, body.public_key, body.name, body.platform, body.app_version,
                                   body.device_uid)

    @app.post("/v1/restore")
    def restore(body: RestoreIn, request: Request, svc: SvcDep):
        """שחזור לפי מספר טלפון: אותו טלפון — חוזר מיד; טלפון אחר — רשימת המכשירים לבחירה."""
        _limit(restore_limiter, client_ip(request))
        _limit(restore_limiter, "phone:" + security.normalize_phone(body.phone))
        return svc.restore_lookup(phone=body.phone, device_uid=body.device_uid, public_key=body.public_key,
                                  name=body.name, platform=body.platform, app_version=body.app_version)

    @app.post("/v1/recognize")
    def recognize(body: RecognizeIn, request: Request, svc: SvcDep):
        """פתיחה ראשונה אחרי התקנה מחדש: האם הטלפון הזה (לפי המזהה של אנדרואיד) כבר מוכר — כדי להציע
        מיד שחזור. בלי מספר טלפון ובלי פרטים אישיים בתשובה."""
        _limit(restore_limiter, client_ip(request))
        return svc.recognize(body.device_uid)

    @app.post("/v1/restore/request", status_code=201)
    def restore_request(body: RestoreRequestIn, request: Request, svc: SvcDep):
        """"העברת ההגנה לטלפון הזה" / "מכשיר נוסף" — לאישור המנהל."""
        _limit(register_limiter, client_ip(request))
        return svc.restore_request(phone=body.phone, device_uid=body.device_uid, public_key=body.public_key,
                                   name=body.name, platform=body.platform, app_version=body.app_version,
                                   mode=body.mode, old_device_id=body.old_device_id)

    @app.get("/v1/device")
    def device(request: Request, svc: SvcDep, desk: DeskDep, row: DeviceRow):
        out = svc.device_status(row, client_ip(request))
        out["support"] = desk.device_unread(row["id"]) | {"whatsapp": desk.wa_config().public_number}
        if desk.is_admin_device(row):
            # מכשיר המנהל: רואה כמה פניות ממתינות, וכך האפליקציה מציגה את כפתור "ניהול תמיכה"
            out["admin"] = {"support_unread": desk.admin_unread()}
        return out

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

    @app.post("/v1/device/rekey")
    def device_rekey(body: RekeyIn, svc: SvcDep, row: DeviceRow):
        _limit(rekey_limiter, f"rk:{row['id']}")
        return svc.rekey(row, body.public_key)

    @app.post("/v1/device/heartbeat")
    def device_heartbeat(body: HeartbeatIn, request: Request, svc: SvcDep, row: DeviceRow):
        _limit(heartbeat_limiter, f"hb:{row['id']}")
        raw = body.model_dump()
        if len(str(raw)) > 20_000:
            raise ApiError(413, "הדיווח גדול מדי")
        out = svc.heartbeat(row, raw, client_ip(request))
        out["org_name"] = svc.org_name()     # מנהל מכשיר: "המכשיר הזה שייך ל-…" (מסך נעילה / פס ההתראות)
        unread = request.app.state.support.device_unread(row["id"])
        if unread["unread"]:
            out["support"] = unread          # הטלפון מציג התראה "יש תשובה מהתמיכה"
        return out

    @app.post("/v1/device/app-updates")
    def device_app_updates(body: AppUpdatesIn, request: Request, row: DeviceRow):
        """טאב העדכונים: לכל אפליקציה בטלפון — האם יש ב-Google Play גרסה חדשה (ראו playinfo.py)."""
        _limit(updates_limiter, f"au:{row['id']}")
        return {"apps": request.app.state.playinfo.check([a.model_dump() for a in body.apps])}

    @app.get("/v1/store/media/{app_id}/{slot}")
    def store_media(app_id: int, slot: str, request: Request, svc: SvcDep):
        """אייקון / צילום מסך לחנות — אחרי מסנן התמונות ברמה המחמירה (ציבורי: אין בו מידע אישי)."""
        _limit(media_limiter, f"sm:{client_ip(request)}")
        body, ct = svc.store_media(app_id, slot)
        return Response(content=body, media_type=ct, headers={"Cache-Control": "public, max-age=604800"})

    @app.post("/v1/device/support", status_code=201)
    def device_support(body: SupportIn, tasks: BackgroundTasks, desk: DeskDep, row: DeviceRow):
        """הודעה מהלקוח לתמיכה (צ'אט באפליקציה). נשמרת, ונשלחת לוואטסאפ של המנהל ברקע."""
        _limit(support_limiter, f"sup:{row['id']}")
        msg = desk.device_post(row, body.text, body.diag)
        tasks.add_task(desk.notify_admin, msg["id"])
        return msg

    @app.get("/v1/device/support")
    def device_support_list(desk: DeskDep, row: DeviceRow, after: int = 0):
        return desk.device_messages(row, after)

    # --- ניהול תמיכה מתוך האפליקציה (מכשיר המנהל בלבד) ---

    def _require_admin_device(desk: SupportDesk, row) -> None:
        if not desk.is_admin_device(row):
            raise ApiError(403, "אין הרשאת ניהול למכשיר הזה")

    @app.get("/v1/device/admin/support")
    def device_admin_support(desk: DeskDep, row: DeviceRow):
        _require_admin_device(desk, row)
        return {"threads": desk.threads(), "unread": desk.admin_unread()}

    @app.get("/v1/device/admin/support/{device_id}")
    def device_admin_thread(device_id: int, desk: DeskDep, row: DeviceRow):
        _require_admin_device(desk, row)
        return desk.thread(device_id)

    @app.post("/v1/device/admin/support/{device_id}", status_code=201)
    def device_admin_reply(device_id: int, body: SupportReplyIn, desk: DeskDep, row: DeviceRow):
        _require_admin_device(desk, row)
        _limit(support_limiter, f"adm:{row['id']}")
        return desk.reply(device_id, "מנהל", body.text, via="app-admin")

    @app.post("/v1/device/resume")
    def device_resume(svc: SvcDep, row: DeviceRow):
        """"החזר הגנה" מהטלפון בזמן השהיה — ההשהיה מסתיימת גם בשרת."""
        _limit(device_limiter, f"res:{row['id']}")
        return svc.device_resume(row)

    @app.post("/v1/device/uninstalled")
    def device_uninstalled(svc: SvcDep, row: DeviceRow):
        """"הסרת הכול" מהטלפון — רק בזמן השהיה שהמנהל אישר."""
        _limit(device_limiter, f"uni:{row['id']}")
        return svc.device_uninstalled(row)

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
    def overview(svc: SvcDep, desk: DeskDep, admin: AdminRow):
        return {**svc.overview(), "admin": admin["username"], "support_unread": desk.admin_unread()}

    # --- תמיכה ---

    @app.get("/v1/admin/support")
    def support_threads(desk: DeskDep, _: AdminRow):
        return {"threads": desk.threads(), "unread": desk.admin_unread(), "whatsapp": desk.wa_status()["can_send"]}

    @app.get("/v1/admin/support/{device_id}")
    def support_thread(device_id: int, desk: DeskDep, _: AdminRow):
        return desk.thread(device_id)

    @app.post("/v1/admin/support/{device_id}", status_code=201)
    def support_reply(device_id: int, body: SupportReplyIn, desk: DeskDep, admin: AdminRow):
        return desk.reply(device_id, admin["username"], body.text)

    @app.get("/v1/admin/whatsapp")
    def whatsapp_settings(desk: DeskDep, _: AdminRow):
        return desk.wa_status()

    @app.put("/v1/admin/whatsapp")
    def whatsapp_save(body: WhatsAppIn, desk: DeskDep, admin: AdminRow):
        return desk.save_wa_config(admin["username"], body.model_dump())

    @app.post("/v1/admin/whatsapp/test")
    def whatsapp_test(desk: DeskDep, _: AdminRow):
        return desk.send_test()

    # --- שם הארגון שמוצג בטלפונים (מסך נעילה / פס ההתראות) ---

    @app.get("/v1/admin/branding")
    def branding(svc: SvcDep, _: AdminRow):
        return {"org_name": svc.org_name()}

    @app.put("/v1/admin/branding")
    def branding_save(body: BrandingIn, svc: SvcDep, admin: AdminRow):
        return {"org_name": svc.set_org_name(admin["username"], body.org_name)}

    # --- שרת GPU (Modal) לניתוח תמונות ---

    @app.get("/v1/admin/gpu")
    def gpu_settings(svc: SvcDep, _: AdminRow):
        return gpu.status(svc.cfg)

    @app.put("/v1/admin/gpu")
    def gpu_save(body: GpuIn, svc: SvcDep, admin: AdminRow):
        try:
            st = gpu.save(svc.cfg, body.model_dump())
        except ValueError as e:
            raise ApiError(400, str(e)) from None
        svc.audit(admin["username"], "gpu_config", "modal", st["mode"] if st["configured"] else "removed")
        return st

    @app.post("/v1/admin/gpu/test")
    def gpu_test(svc: SvcDep, _: AdminRow):
        return gpu.test(svc.cfg)

    # --- בוט וואטסאפ: הגדרות ומצב בדשבורד (חיבור ב-QR, מוח ה-AI, מידע העסק) ---

    @app.get("/v1/admin/wabot")
    def wabot_view(svc: SvcDep, _: AdminRow):
        return WaBot(svc).admin_view()

    @app.put("/v1/admin/wabot")
    def wabot_save(body: WaBotIn, svc: SvcDep, admin: AdminRow):
        return WaBot(svc).save(admin["username"], body.model_dump())

    @app.get("/v1/admin/wabot/knowledge")
    def wabot_knowledge(svc: SvcDep, _: AdminRow):
        return {"items": WaBot(svc).knowledge_list()}

    @app.post("/v1/admin/wabot/knowledge", status_code=201)
    def wabot_knowledge_add(body: KnowledgeIn, svc: SvcDep, admin: AdminRow):
        return WaBot(svc).knowledge_add(body.model_dump(), "dashboard", admin["username"])

    @app.put("/v1/admin/wabot/knowledge/{kid}")
    def wabot_knowledge_edit(kid: int, body: KnowledgeIn, svc: SvcDep, admin: AdminRow):
        return WaBot(svc).knowledge_update(kid, body.model_dump(exclude_unset=True), admin["username"])

    @app.delete("/v1/admin/wabot/knowledge/{kid}")
    def wabot_knowledge_delete(kid: int, svc: SvcDep, admin: AdminRow):
        return WaBot(svc).knowledge_delete(kid, admin["username"])

    @app.post("/v1/admin/wabot/test-ai")
    def wabot_test_ai(svc: SvcDep, _: AdminRow):
        return WaBot(svc).test_ai()

    # --- בוט וואטסאפ לא-רשמי (שירות נפרד, מאומת בטוקן בוט) ---

    @app.get("/v1/bot/config")
    def bot_config(bot: BotDep):
        return WaBot(bot.svc).bot_config()

    @app.post("/v1/bot/status")
    def bot_status(body: BotStatusIn, bot: BotDep):
        return WaBot(bot.svc).report(body.model_dump())

    @app.get("/v1/bot/knowledge")
    def bot_knowledge(bot: BotDep):
        return {"items": WaBot(bot.svc).knowledge_list()}

    @app.post("/v1/bot/knowledge", status_code=201)
    def bot_knowledge_add(body: KnowledgeIn, bot: BotDep):
        src = body.source if body.source in ("reply", "ask", "teach") else "reply"
        return WaBot(bot.svc).knowledge_add(body.model_dump(), src)

    @app.delete("/v1/bot/knowledge/{kid}")
    def bot_knowledge_delete(kid: int, bot: BotDep):
        return WaBot(bot.svc).knowledge_delete(kid, "whatsapp")

    @app.post("/v1/bot/knowledge/used")
    def bot_knowledge_used(body: KnowledgeUsedIn, bot: BotDep):
        WaBot(bot.svc).knowledge_used(body.ids)
        return {"ok": True}

    @app.get("/v1/bot/customer")
    def bot_customer(phone: str, bot: BotDep, device: int | None = None, aid: str = ""):
        return bot.customer(phone, device=device, aid=aid)

    @app.get("/v1/bot/outbox")
    def bot_outbox(bot: BotDep, after: int = 0):
        return {"messages": bot.outbox(after)}

    @app.post("/v1/bot/forwarded")
    def bot_forwarded(body: BotForwardedIn, bot: BotDep):
        return {"marked": bot.mark_forwarded(body.ids, body.wa_ids)}

    @app.post("/v1/bot/reply")
    def bot_reply(body: BotReplyIn, bot: BotDep):
        return bot.reply(body.device_id, body.text)

    @app.post("/v1/bot/block")
    def bot_block(body: BotBlockIn, bot: BotDep):
        return bot.block(body.device_id, body.options)

    @app.post("/v1/bot/open")
    def bot_open(body: BotTempIn, bot: BotDep):
        return bot.open(body.device_id, body.item, body.minutes)

    @app.post("/v1/bot/close")
    def bot_close(body: BotTempIn, bot: BotDep):
        return bot.close(body.device_id, body.item)

    @app.post("/v1/bot/escalate", status_code=201)
    def bot_escalate(body: BotEscalateIn, bot: BotDep):
        return bot.escalate(body.device_id, body.note, body.level, body.options)

    @app.get("/v1/whatsapp/webhook")
    def whatsapp_verify(request: Request, desk: DeskDep):
        """אימות ה-webhook מול Meta (פעם אחת, בהגדרה)."""
        q = request.query_params
        return PlainTextResponse(desk.verify_webhook(q.get("hub.mode"), q.get("hub.verify_token"),
                                                     q.get("hub.challenge")))

    @app.post("/v1/whatsapp/webhook")
    async def whatsapp_incoming(request: Request, tasks: BackgroundTasks, desk: DeskDep):
        """תשובות המנהל מוואטסאפ → ללקוח באפליקציה. רק עם חתימה תקינה ורק מהמספר של המנהל."""
        body = await request.body()
        follow = await asyncio.to_thread(desk.handle_webhook, body, request.headers.get("x-hub-signature-256"))
        tasks.add_task(desk.run_followups, follow)
        return {"ok": True}

    @app.get("/v1/admin/catalog")
    def catalog_admin(_: AdminRow):
        return admin_catalog()

    # --- ניהול: בקשות ---

    @app.get("/v1/admin/requests")
    def requests_(svc: SvcDep, _: AdminRow, status: str = "pending"):
        return svc.list_requests(None if status == "all" else status)

    @app.get("/v1/admin/swaps")
    def swaps(svc: SvcDep, _: AdminRow, status: str = "pending"):
        return {"swaps": svc.list_swaps(status if status in ("pending", "approved", "rejected") else "pending")}

    @app.post("/v1/admin/swaps/{swap_id}")
    def resolve_swap(swap_id: int, body: SwapDecision, svc: SvcDep, admin: AdminRow):
        return svc.resolve_swap(swap_id, body.approve, admin["username"], body.as_additional)

    @app.post("/v1/admin/requests/{request_id}/approve")
    def approve(request_id: int, body: Decision, svc: SvcDep, admin: AdminRow):
        return svc.approve_request(request_id, admin["username"], body.model_dump(exclude_unset=True))

    @app.post("/v1/admin/requests/{request_id}/reject", status_code=204)
    def reject(request_id: int, body: RejectIn, svc: SvcDep, admin: AdminRow):
        svc.reject_request(request_id, admin["username"], body.admin_note)

    # --- ניהול: מכשירים ---

    @app.get("/v1/admin/devices")
    def devices(svc: SvcDep, _: AdminRow, state: str | None = None):
        """state: green / yellow / red / inactive / offline / vpn_inactive / accessibility_off / unmanaged."""
        out = svc.list_devices()
        if state:
            out = [d for d in out if _state_matches(d, state)]
        return out

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

    @app.post("/v1/admin/devices/{device_id}/pause")
    def pause_device(device_id: int, body: PauseIn, svc: SvcDep, admin: AdminRow):
        return svc.pause_device(device_id, body.minutes, body.reason, admin["username"])

    @app.post("/v1/admin/devices/{device_id}/resume")
    def resume_device(device_id: int, svc: SvcDep, admin: AdminRow):
        return svc.resume_device(device_id, admin["username"])

    @app.post("/v1/admin/devices/{device_id}/unmanage")
    def unmanage_device(device_id: int, svc: SvcDep, admin: AdminRow):
        return svc.request_unmanage(device_id, admin["username"])

    @app.post("/v1/admin/devices/{device_id}/transfer")
    def transfer_device(device_id: int, svc: SvcDep, admin: AdminRow):
        return svc.create_transfer(device_id, admin["username"])

    @app.post("/v1/admin/devices/{device_id}/apply-profile")
    def apply_profile(device_id: int, body: ApplyProfileIn, svc: SvcDep, admin: AdminRow):
        return svc.apply_profile(device_id, body.profile_id, admin["username"])

    # --- ניהול: פרופילים מוכנים ---

    @app.get("/v1/admin/profiles")
    def list_profiles(svc: SvcDep, _: AdminRow):
        return {"profiles": svc.list_profiles()}

    @app.post("/v1/admin/profiles", status_code=201)
    def create_profile(body: ProfileIn, svc: SvcDep, admin: AdminRow):
        return svc.save_profile(admin["username"], body.name, body.level, body.settings)

    @app.patch("/v1/admin/profiles/{profile_id}")
    def update_profile(profile_id: int, body: ProfileIn, svc: SvcDep, admin: AdminRow):
        return svc.save_profile(admin["username"], body.name, body.level, body.settings, profile_id)

    @app.delete("/v1/admin/profiles/{profile_id}", status_code=204)
    def delete_profile(profile_id: int, svc: SvcDep, admin: AdminRow):
        svc.delete_profile(profile_id, admin["username"])

    # --- ניהול: קטלוג אפליקציות מאושרות ---

    @app.get("/v1/admin/apps")
    def list_apps(svc: SvcDep, _: AdminRow):
        return {"apps": svc.managed_apps(), "categories": store_seed()["categories"]}

    @app.post("/v1/admin/apps", status_code=201)
    def create_app(body: ManagedAppIn, svc: SvcDep, admin: AdminRow):
        return svc.save_managed_app(admin["username"], body.model_dump())

    @app.put("/v1/admin/apps/upload", status_code=201)
    async def upload_apk(request: Request, svc: SvcDep, admin: AdminRow):
        """העלאת APK לחנות (גוף הבקשה = הקובץ). השרת קורא ממנו שם חבילה וגרסה, מחשב SHA-256,
        ושומר — ואז טלפונים במנהל מכשיר מתקינים/מעדכנים ממנו (גם כשההתקנות חסומות בטלפון)."""
        d = svc.apps_dir()
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f".upload-{os.getpid()}-{time.time_ns()}"
        digest = hashlib.sha256()
        total = 0
        try:
            with open(tmp, "wb") as f:
                async for chunk in request.stream():
                    total += len(chunk)
                    if total > MAX_APK_UPLOAD:
                        raise ApiError(413, "הקובץ גדול מדי (עד 300MB)")
                    digest.update(chunk)
                    f.write(chunk)
            if total == 0:
                raise ApiError(400, "לא התקבל קובץ")
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return await asyncio.to_thread(svc.store_apk, admin["username"], tmp, digest.hexdigest())

    @app.get("/v1/apps/{name}")
    def apk_file(name: str, svc: SvcDep):
        """קובצי ה-APK של החנות (ציבורי — האפליקציות שהמנהל אישר; הטלפון בודק SHA-256 לפני התקנה)."""
        path = svc.apps_dir() / name
        if not _APK_FILE.match(name) or not path.is_file():
            raise ApiError(404, "הקובץ לא נמצא")
        return FileResponse(path, media_type="application/vnd.android.package-archive", filename=name)

    @app.patch("/v1/admin/apps/{app_id}")
    def update_app(app_id: int, body: ManagedAppIn, svc: SvcDep, admin: AdminRow):
        return svc.save_managed_app(admin["username"], body.model_dump(), app_id)

    @app.delete("/v1/admin/apps/{app_id}", status_code=204)
    def delete_app(app_id: int, svc: SvcDep, admin: AdminRow):
        svc.delete_managed_app(app_id, admin["username"])

    @app.post("/v1/admin/devices/{device_id}/reset-apps")
    def reset_apps(device_id: int, svc: SvcDep, admin: AdminRow):
        d = svc.device_row(device_id)
        if not d["vpn_ip"]:
            raise ApiError(409, "למכשיר אין עדיין כתובת VPN")
        n = svc.clear_passthrough(d["vpn_ip"])
        svc.audit(admin["username"], "reset_apps", f"device:{device_id}", str(n))
        return {"cleared": n}

    @app.post("/v1/admin/devices/{device_id}/temp-open")
    def admin_temp_open(device_id: int, body: TempOpenIn, svc: SvcDep, admin: AdminRow):
        """פתיחה זמנית (עד שעה) של משהו חסום — נחסם שוב לבד בסוף התוקף."""
        return svc.temp_open(device_id, body.item, body.minutes, admin["username"], body.reason)

    @app.delete("/v1/admin/devices/{device_id}/temp-open/{item}")
    def admin_temp_close(device_id: int, item: str, svc: SvcDep, admin: AdminRow):
        return svc.temp_close(device_id, item, admin["username"])

    @app.post("/v1/admin/devices/{device_id}/account-window")
    def account_window(device_id: int, svc: SvcDep, admin: AdminRow):
        """10 דקות שבהן מסך "הוספת חשבון Google" עובד במכשיר הזה (אחרי צריבה מוחקים את החשבונות)."""
        d = svc.device_row(device_id)
        if not d["vpn_ip"]:
            raise ApiError(409, "למכשיר אין עדיין כתובת VPN")
        n = svc.open_account_window(d["vpn_ip"])
        svc.audit(admin["username"], "account_window", f"device:{device_id}", str(n))
        return {"hosts": n, "minutes": 10}

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
        # options לא נשלח = ברירת המחדל ללקוח חדש (מחמיר + רשתות + בלי דפדפן + חנויות חסומות)
        opts = list(DEFAULT_OPTIONS) if body.options is None else body.options
        r = svc.create_code(body.label, body.level, body.uses, body.expires_days, opts)
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

    @app.post("/v1/admin/provisioning")
    def provisioning(body: ProvisionIn, svc: SvcDep, admin: AdminRow):
        r = svc.provisioning(body.level, body.label, body.wifi_ssid, body.wifi_password, body.wifi_security)
        svc.audit(admin["username"], "provisioning_qr", f"code:{r['code']['id']}", f"level:{body.level}")
        return r

    @app.get("/v1/admin/fleet")
    def fleet(svc: SvcDep, _: AdminRow):
        return svc.fleet_summary(max_age_s=5)

    @app.get("/v1/admin/system")
    def system(svc: SvcDep, _: AdminRow):
        """מצב השרת לממשק הניהול: רכיבים, מדדי המנוע והפרוקסי, ומצב המכשירים."""
        return svc.system_status()

    # --- ממשק הניהול ---

    @app.get("/admin")
    def admin_page():
        return FileResponse(STATIC / "admin.html", headers={"Cache-Control": "no-cache"})
