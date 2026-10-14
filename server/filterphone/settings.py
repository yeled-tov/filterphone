"""הגדרות מרכזיות. כל ערך ניתן לדריסה במשתנה סביבה FP_<NAME>.

בשרת, systemd טוען את המשתנים מ-/etc/filterphone/filterphone.env.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, fields
from functools import lru_cache
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True, slots=True)
class Settings:
    # --- נתיבים ---
    data_dir: str = "/var/lib/filterphone"
    models_dir: str = "/var/lib/filterphone/models"
    run_dir: str = "/run/filterphone"
    heads_file: str = str(PACKAGE_DIR / "engine" / "heads.json")
    passthrough_file: str = str(PACKAGE_DIR / "proxy" / "passthrough.txt")

    # --- Redis ---
    redis_url: str = "redis://127.0.0.1:6379/0"

    # --- מנוע ---
    embedder_model: str = "siglip2-b32-256.onnx"
    nudenet_model: str = "nudenet-320n.onnx"
    segment_model: str = "selfie-multiclass-256.onnx"   # רמה 2: מסכת עור/בגד/פנים
    ort_threads: int = 3                # ליבה אחת נשארת פנויה לפרוקסי
    batch_max: int = 8
    batch_wait_ms: float = 6.0
    decode_threads: int = 2
    cache_ttl_days: int = 30
    min_image_bytes: int = 1200         # מתחת לזה: אייקונים ופיקסלים
    min_side_px: int = 64               # תמונה שהצלע הקצרה שלה קטנה מזה לא נבדקת
    max_image_bytes: int = 12_000_000
    max_decode_side: int = 1024
    engine_max_inflight: int = 16       # תמונות בעיבוד בו-זמנית (מעבר לזה — ממתינות בתור)
    engine_max_queue: int = 64          # תור מלא = דחייה מיידית לפי מדיניות הכשל של הרמה
    engine_job_timeout_s: float = 10.0  # תקרה לתמונה אחת, גם אם הפונה מוכן לחכות יותר

    # --- פרוקסי ---
    engine_timeout_s: float = 6.0
    engine_pool_size: int = 8
    default_level: int = 1              # מכשיר לא מוכר — הרמה המחמירה
    passthrough_learn_days: int = 3

    # --- API ---
    api_host: str = "127.0.0.1"
    api_port: int = 8090
    public_host: str = "api.filterphone.com"
    vpn_endpoint: str = "vpn.filterphone.com:51820"
    public_ip: str = ""                 # כתובת השרת — גיבוי בטלפון כשה-DNS לא זמין (מיד אחרי הפעלה)
    wg_interface: str = "wg0"
    vpn_network: str = "10.8.0.0/24"
    vpn_server_ip: str = "10.8.0.1"
    vpn_first_client: int = 10          # 10.8.0.2-9 שמורים ללקוחות הישנים
    wg_keepalive_s: int = 25            # keepalive של WireGuard בטלפון: 15–50 (ראו docs/VPN_BATTERY.md)
    mitm_ca_cert: str = "/var/lib/filterphone/mitmproxy/mitmproxy-ca-cert.pem"
    register_enabled: bool = True       # הרשמה עצמית מהאפליקציה (בקשת הצטרפות)
    max_pending: int = 300              # תקרת בקשות ממתינות — הגנה מהצפה
    db_pool_size: int = 8
    max_body_bytes: int = 1_000_000     # גוף בקשה רגילה; בדיקת תמונה בממשק — עד max_image_bytes × 1.4
    heartbeat_interval_s: int = 900     # כל כמה זמן הטלפון מדווח (JobScheduler: מינימום 15 דקות)
    offline_after_s: int = 1800         # בלי דיווח ובלי handshake יותר מזה — "לא מחובר"
    lost_after_s: int = 172_800         # בלי שום קשר יומיים — אדום ("אין קשר")
    sync_grace_s: int = 1500            # מדיניות שלא אושרה ע"י הטלפון יותר מזה — "סנכרון מתעכב"
    apk_dir: str = "/var/www/filterphone"   # דף ההורדה — שם נמצא ה-APK להקמת Device Owner ב-QR
    apk_signature_sha256: str = ""      # טביעת תעודת החתימה (hex) — אופציונלי, מחזק את בדיקת ה-QR

    # --- AdGuard (שכבת ה-DNS של החסימות לפי מכשיר). בלי משתמש/סיסמה — רק הפרוקסי אוכף ---
    adguard_url: str = "http://127.0.0.1:3002"
    adguard_user: str = ""
    adguard_password: str = ""

    # --- תמיכה בוואטסאפ (WhatsApp Cloud API). ריק = מה שהמנהל שמר בממשק (מערכת → וואטסאפ) ---
    wa_token: str = ""
    wa_phone_number_id: str = ""
    wa_admin_number: str = ""
    wa_verify_token: str = ""
    wa_app_secret: str = ""
    wa_template: str = ""
    wa_template_lang: str = ""
    wa_public_number: str = ""
    wa_graph_version: str = ""

    @property
    def engine_socket(self) -> str:
        return os.path.join(self.run_dir, "engine.sock")

    @property
    def db_path(self) -> str:
        return os.path.join(self.data_dir, "filterphone.db")


def _coerce(raw: str, typ: type):
    if typ is bool:
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return typ(raw)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    overrides = {}
    for f in fields(Settings):
        raw = os.environ.get(f"FP_{f.name.upper()}")
        if raw is not None:
            typ = type(f.default)
            overrides[f.name] = _coerce(raw, typ)
    return Settings(**overrides)
