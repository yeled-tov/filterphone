"""שרת ה-GPU (Modal) מהדשבורד: מצב, שמירה לקובץ שהמנוע קורא, ובדיקת חיבור אמיתית.

המנוע (fp-engine) קולט שינוי תוך כ-15 שניות, בלי הפעלה מחדש (ראו Engine._reload_remote).
משתני סביבה FP_MODAL_* (אם הוגדרו בשרת) קודמים להגדרות מהדשבורד.
"""
from __future__ import annotations

import io
import time
import urllib.error
from functools import lru_cache

from PIL import Image

from ..engine import remote
from ..engine.heads import HeadSet
from ..settings import Settings

_HINTS = {
    401: "הטוקן שגוי (Token ID / Secret)",
    403: "הטוקן שגוי או ללא הרשאה",
    404: "הכתובת שגויה, או שהשרת עוד לא הועלה ל-Modal (modal deploy)",
    402: "נגמר הקרדיט ב-Modal",
    429: "מכסה / קרדיט נגמר ב-Modal (המנוע ממשיך מקומית)",
}


@lru_cache(maxsize=4)
def heads_version(path: str) -> str:
    return HeadSet.load(path).version


def _from_env(cfg: Settings) -> bool:
    return bool(cfg.modal_url or cfg.modal_key or cfg.modal_secret)


def _effective(cfg: Settings) -> dict:
    if _from_env(cfg):
        return {"url": cfg.modal_url, "key": cfg.modal_key, "secret": cfg.modal_secret, "mode": cfg.modal_mode}
    saved = remote.read_config(remote.config_path(cfg))
    return {"url": saved.get("url", ""), "key": saved.get("key", ""), "secret": saved.get("secret", ""),
            "mode": saved.get("mode", "always")}


def status(cfg: Settings) -> dict:
    eff = _effective(cfg)
    configured = bool(eff["url"] and eff["key"] and eff["secret"])
    return {
        "source": "env" if _from_env(cfg) else ("dashboard" if configured else "none"),
        "url": eff["url"], "key": eff["key"], "secret_set": bool(eff["secret"]),
        "mode": eff["mode"], "configured": configured, "active": configured and eff["mode"] != "off",
        "heads": heads_version(cfg.heads_file),
    }


def save(cfg: Settings, changes: dict) -> dict:
    """זורק ValueError בערך לא תקין, או כשההגדרות נקבעו במשתני סביבה בשרת."""
    if _from_env(cfg):
        raise ValueError("ההגדרות נקבעו בשרת (FP_MODAL_* ב-filterphone.env) — שנו אותן שם")
    path = remote.config_path(cfg)
    remote.write_config(path, remote.merge_config(remote.read_config(path), changes))
    return status(cfg)


def _probe_image() -> bytes:
    """תמונה קטנה וניטרלית לבדיקה (מעל הסף של "קטנה מדי" — כדי שתעבור במודל באמת)."""
    im = Image.new("RGB", (96, 96), (200, 210, 220))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def test(cfg: Settings) -> dict:
    """בקשה אמיתית ל-Modal. ההפעלה הראשונה (cold start) יכולה לקחת עד דקה — טעינת המודלים ל-GPU."""
    eff = _effective(cfg)
    if not (eff["url"] and eff["key"] and eff["secret"]):
        return {"ok": False, "error": "לא מוגדר — הזינו כתובת, Token ID ו-Token Secret"}
    r = remote.RemoteEngine(eff["url"].rstrip("/"), eff["key"], eff["secret"], heads_version(cfg.heads_file),
                            timeout_s=90.0)
    t0 = time.monotonic()
    try:
        entry, _ = r._parse(r._post(_probe_image(), 1))
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}: {_HINTS.get(e.code, 'שגיאה בשרת Modal')}"}
    except ValueError as e:
        msg = str(e)
        if "heads mismatch" in msg:
            msg = "גרסת המודלים ב-Modal שונה מהשרת — צריך להעלות מחדש: modal deploy deploy/modal_engine.py"
        return {"ok": False, "error": msg}
    except Exception as e:  # רשת / timeout / תשובה שאינה JSON
        return {"ok": False, "error": f"אין חיבור: {type(e).__name__}: {e}"[:300]}
    return {"ok": True, "ms": round((time.monotonic() - t0) * 1000), "scores": entry.scores}
