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
    infer_threads: int = 1              # כמה הרצות מודל במקביל (כל אחת עם ort_threads חוטים)
    ort_providers: str = ""             # ריק = מעבד (CPU). ל-GPU (Modal): "CUDAExecutionProvider,CPUExecutionProvider"
    batch_max: int = 8
    batch_wait_ms: float = 6.0
    decode_threads: int = 2
    cache_ttl_days: int = 30
    min_image_bytes: int = 300          # מתחת לזה: פיקסלי מעקב ואייקונים זעירים (תמונה ממוזערת של 32px כבר ~600B)
    min_side_px: int = 64               # רמה 3 בלבד: תמונה שהצלע הקצרה שלה קטנה מזה לא נבדקת לעירום
                                        # (ברמות 1–2 נבדקת כל תמונה מ-24px — engine/service.py MIN_SIDE)
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

    # --- Modal (שרת GPU חיצוני לסינון תמונות). ריק = מה שנשמר בדשבורד (מערכת → שרת GPU); בלי שניהם — מקומי. ---
    # מוגדר → כל תמונה חדשה מנותחת ב-Modal (SigLIP2 + NudeNet + סגמנטציה על GPU), והחלטת הסינון
    # מתקבלת מקומית מאותם ציונים (אותו קוד, אותה החלטה). כשל / מכסה שנגמרה / timeout → נפילה
    # מיידית לניתוח המקומי. הקאש (לפי hash) חוסם ניתוח חוזר — מקומי ו-Modal כאחד.
    modal_url: str = ""                 # https://<workspace>--fp-engine-analyze.modal.run
    modal_key: str = ""                 # Modal-Key (מזהה טוקן proxy-auth, wk-...)
    modal_secret: str = ""              # Modal-Secret (סוד הטוקן, ws-...)
    modal_timeout_s: float = 8.0        # תקרת זמן לבקשה ל-Modal; אחריה — נפילה למקומי
    modal_cooldown_s: float = 30.0      # אחרי כשל/מכסה — לא פונים ל-Modal למשך הזמן הזה (ואז מנסים שוב)
    modal_mode: str = "always"          # always = כל תמונה חדשה / overflow = רק כשהשרת המקומי עמוס / off
    modal_hedge_s: float = 0.4          # Modal לא ענה תוך זה (הפעלה קרה / רשת איטית) — מנתחים מקומית בלי לחכות;
                                        # עד שהבקשה האיטית חוזרת, שאר התמונות לא נשלחות ל-Modal (0 = תמיד מחכים)

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
    wa_bot_token: str = ""
    # רמות גלישה: רשימת האתרים למבוגרים (נחסמת בחיפוש ובגלישה לכולם; הבוט משתמש בה לסיווג)
    web_adult_list_url: str = "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/wildcard/nsfw-onlydomains.txt"
    web_check_workers: int = 3              # כמה אתרים הבוט בודק במקביל
    web_check_images: int = 12              # כמה תמונות מדף הבית של אתר נבדקות במנוע

    @property
    def engine_socket(self) -> str:
        return os.path.join(self.run_dir, "engine.sock")

    @property
    def db_path(self) -> str:
        return os.path.join(self.data_dir, "filterphone.db")

    @property
    def adult_list_file(self) -> str:
        return os.path.join(self.data_dir, "lists", "nsfw.txt")


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
