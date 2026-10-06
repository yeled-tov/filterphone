"""הגדרת לוגים לכל התהליכים: טקסט רגיל, או JSON שורה-לשורה (FP_LOG_FORMAT=json) —
נוח לחיפוש ב-journalctl ולכלי ניטור. בלי תוכן הודעות, טוקנים או פרטים אישיים: מי שכותב
ללוג אחראי לזה (מספרי טלפון — mask_phone, כתובות — רק שם האתר).
"""
from __future__ import annotations

import json
import logging
import os
import time


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        d = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            d.update({k: v for k, v in fields.items() if k not in d})
        if record.exc_info:
            d["exc"] = self.formatException(record.exc_info)
        return json.dumps(d, ensure_ascii=False, default=str)


def setup(default_level: str = "INFO") -> None:
    """רק אם עוד לא הוגדר (uvicorn מגדיר רק את הלוגרים שלו)."""
    root = logging.getLogger()
    if root.handlers:
        return
    handler = logging.StreamHandler()
    if os.environ.get("FP_LOG_FORMAT", "text").lower() == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
    root.setLevel(os.environ.get("FP_LOG_LEVEL", default_level))
