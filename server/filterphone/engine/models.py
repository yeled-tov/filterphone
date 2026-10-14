"""טעינת המודלים (ONNX Runtime) והרצתם.

ברירת המחדל היא מעבד (CPU). להרצה על GPU (למשל בשרת Modal) קובעים
``FP_ORT_PROVIDERS=CUDAExecutionProvider,CPUExecutionProvider`` — ORT ישתמש ב-GPU ויפול חזרה
למעבד אם ה-GPU לא זמין. ברירת המחדל זהה למה שהיה (CPUExecutionProvider בלבד).
"""
from __future__ import annotations

import os

import numpy as np
import onnxruntime as ort

from .imaging import NudenetInput, nudenet_decode
from .segment import softmax

# אזהרות טעינה (למשל attribute ישן במודל הסגמנטציה) נכתבות ללוגר הגלובלי — לא רלוונטיות
ort.set_default_logger_severity(3)


def _providers() -> list:
    """ספקי ההרצה: ברירת מחדל מעבד; ניתן לדרוס לסביבת GPU דרך משתנה סביבה (השרת המקומי לא משתנה).
    ב-CUDA מכבים TF32 — חישוב FP32 מלא, כדי שהציונים יהיו זהים כמעט לגמרי לאלה של המעבד
    (TF32 מקצר את דיוק הכפלת המטריצות ועלול להזיז ציון שקרוב לסף)."""
    names = [p.strip() for p in os.environ.get("FP_ORT_PROVIDERS", "").split(",") if p.strip()]
    if not names:
        return ["CPUExecutionProvider"]
    return [(n, {"use_tf32": "0"}) if n == "CUDAExecutionProvider" else n for n in names]


_PROVIDERS = _providers()


def _session(path: str, threads: int) -> ort.InferenceSession:
    so = ort.SessionOptions()
    so.intra_op_num_threads = threads
    so.inter_op_num_threads = 1
    so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    so.log_severity_level = 3
    return ort.InferenceSession(path, so, providers=_PROVIDERS)


class Embedder:
    """SigLIP2 vision encoder → embedding מנורמל (768 ממדים)."""

    def __init__(self, path: str, threads: int):
        self.session = _session(path, threads)
        inp = self.session.get_inputs()[0]
        self.input_name = inp.name
        self.size = int(inp.shape[-1]) if isinstance(inp.shape[-1], int) else 256
        outputs = [o.name for o in self.session.get_outputs()]
        self.output_name = "pooler_output" if "pooler_output" in outputs else outputs[-1]

    def __call__(self, batch: np.ndarray) -> np.ndarray:
        out = self.session.run([self.output_name], {self.input_name: batch})[0]
        return out / np.linalg.norm(out, axis=1, keepdims=True)


class NudeDetector:
    """NudeNet 320n — גלאי חלקי גוף. רץ רק כשהמדיניות מבקשת אימות."""

    def __init__(self, path: str, threads: int):
        self.session = _session(path, threads)
        inp = self.session.get_inputs()[0]
        self.input_name = inp.name
        self.size = int(inp.shape[-1]) if isinstance(inp.shape[-1], int) else 320

    def __call__(self, inp: NudenetInput):
        out = self.session.run(None, {self.input_name: inp.tensor[None]})[0]
        return nudenet_decode(out, inp)


class Segmenter:
    """MediaPipe selfie-multiclass — סגמנטציה לשישה סוגים (ראו segment.py). רץ רק ברמה 2."""

    def __init__(self, path: str, threads: int):
        self.session = _session(path, threads)
        inp = self.session.get_inputs()[0]
        self.input_name = inp.name
        self.size = int(inp.shape[1]) if isinstance(inp.shape[1], int) else 256

    def __call__(self, x: np.ndarray) -> np.ndarray:
        """x: (S, S, 3) בטווח [0, 1] → הסתברויות (S, S, 6). המודל מחזיר logits."""
        return softmax(self.session.run(None, {self.input_name: x[None]})[0][0])
