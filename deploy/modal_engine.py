"""שרת הניתוח של FilterPhone ב-Modal (GPU).

מריץ את *אותו* קוד מנוע כמו השרת (server/filterphone) על כרטיס גרפי, ומחזיר לשרת רשומה קטנה
(JSON: ציונים, גלאי וסגמנטציה לרמה המבוקשת). ההחלטה והציור נשארים בשרת — לכן איכות הסינון זהה,
וכל תקלה כאן פשוט מחזירה את הניתוח לשרת המקומי.

העלאה (מתיקיית הפרויקט):
    pip install modal && modal setup
    modal deploy deploy/modal_engine.py
או מ-GitHub: Actions → "העלאה ל-Modal" → Run workflow.

הכתובת שמתקבלת: https://<workspace>--filterphone-engine.modal.run — מדביקים אותה בדשבורד
(מערכת → שרת GPU), יחד עם Proxy Auth Token (modal.com → Settings → Proxy Auth Tokens).
מדריך בעברית: docs/MODAL.md
"""
from __future__ import annotations

from pathlib import Path

import modal

ROOT = Path(__file__).resolve().parent.parent
GPU = "T4"              # הזול ביותר; מספיק בהחלט למודלים האלה. ל-A10G: מהיר יותר ויקר פי ~2
IDLE_S = 180            # כמה זמן הכרטיס נשאר דולק בלי תמונות (חיוב רק בזמן שהוא דולק)

image = (
    # CUDA 12 + cuDNN 9 — מה ש-onnxruntime-gpu 1.2x צריך
    modal.Image.from_registry("nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04", add_python="3.12")
    .pip_install("onnxruntime-gpu>=1.22,<2", "numpy>=2.0", "pillow>=11.3", "redis>=5.0", "fastapi[standard]")
    # המודלים — אותו סקריפט ואותן גרסאות מאומתות (SHA-256) כמו בשרת
    .add_local_file(ROOT / "tools" / "fetch_models.py", "/opt/fp/fetch_models.py", copy=True)
    .run_commands("python /opt/fp/fetch_models.py /models")
    .env({
        "FP_MODELS_DIR": "/models",
        "FP_ORT_PROVIDERS": "CUDAExecutionProvider,CPUExecutionProvider",
        "FP_ORT_THREADS": "4",
        "PYTHONPATH": "/root",
    })
    # קוד המנוע עצמו (בדיוק הגרסה שבפרויקט — כולל heads.json והספים)
    .add_local_dir(ROOT / "server" / "filterphone", "/root/filterphone")
)

app = modal.App("filterphone-engine", image=image)

with image.imports():           # קיים רק בתוך Modal (מקומית מספיק `pip install modal`)
    from fastapi import Request


class _NoCache:
    """בלי Redis ב-Modal: הקאש נמצא בשרת (לפני הפנייה לכאן)."""

    async def get(self, key):
        return None

    async def put(self, key, entry):
        pass

    async def get_labels(self, key):
        return None

    async def put_labels(self, key, png):
        pass

    async def close(self):
        pass


@app.cls(gpu=GPU, scaledown_window=IDLE_S, timeout=120, max_containers=2)
@modal.concurrent(max_inputs=32)
class Analyzer:
    @modal.enter()
    async def load(self) -> None:
        import logging

        from filterphone.engine.service import Engine
        from filterphone.settings import get_settings

        logging.basicConfig(level="INFO")
        self.engine = Engine(get_settings())
        self.engine.cache = _NoCache()
        await self.engine.start()
        logging.getLogger("filterphone.modal").info(
            "ready: heads=%s providers=%s", self.engine.heads.version,
            self.engine.embedder.session.get_providers())

    @modal.fastapi_endpoint(method="POST", label="filterphone-engine", requires_proxy_auth=True)
    async def analyze(self, request: Request):
        from fastapi.responses import JSONResponse

        from filterphone.engine.remote import make_payload

        data = await request.body()
        if not data or len(data) > 12_000_000:
            return JSONResponse({"error": "bad image"}, status_code=400)
        try:
            level = min(max(int(request.headers.get("x-fp-level", "1")), 0), 3)
        except ValueError:
            level = 1
        entry, labels = await self.engine.analyze_resolved(data, level)
        return make_payload(entry, labels, self.engine.heads.version)
