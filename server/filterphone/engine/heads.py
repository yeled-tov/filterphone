"""ראשי סיווג לינאריים מעל ה-embedding של SigLIP2 (שיטת "מושגח").

כל ראש הוא רגרסיה לוגיסטית: p = sigmoid(e · w + b), כאשר e הוא embedding מנורמל.
המשקלים נבנים מראש (tools/build_heads.py, ב-GitHub Actions) ונשמרים ב-heads.json.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

# שמות הציונים שהמנוע מחשב. הסדר קבוע — הוא גם הסדר שנשמר בקאש.
SCORE_NAMES = ("person", "female", "immodest", "nsfw")


@dataclass(frozen=True, slots=True)
class HeadSet:
    version: str
    embedder: str
    W: np.ndarray               # (dim, len(SCORE_NAMES))
    b: np.ndarray               # (len(SCORE_NAMES),)
    thresholds: dict[str, float]

    @classmethod
    def load(cls, path: str | Path) -> HeadSet:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        dim = int(doc["dim"])
        cols, bias = [], []
        for name in SCORE_NAMES:
            head = doc["heads"][name]
            w = np.frombuffer(base64.b64decode(head["w"]), dtype="<f4")
            if w.shape != (dim,):
                raise ValueError(f"head {name}: expected {dim} weights, got {w.shape}")
            cols.append(w)
            bias.append(float(head["b"]))
        return cls(
            version=str(doc["version"]),
            embedder=str(doc["embedder"]),
            W=np.stack(cols, axis=1).astype(np.float32),
            b=np.asarray(bias, dtype=np.float32),
            thresholds={k: float(v) for k, v in doc["thresholds"].items()},
        )

    def scores(self, emb: np.ndarray) -> np.ndarray:
        """emb: (N, dim) מנורמל → (N, len(SCORE_NAMES)) הסתברויות."""
        z = emb @ self.W + self.b
        return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


def encode_weights(w: np.ndarray) -> str:
    return base64.b64encode(np.asarray(w, dtype="<f4").tobytes()).decode()
