"""מדדים לניטור (בפורמט הטקסט של Prometheus), בלי תלות חיצונית.

כל תהליך (API, מנוע, פרוקסי) מחזיק Registry משלו. ה-API מציג ב-/metrics את המדדים שלו
ושל שני האחרים: המנוע עונה על {"op": "metrics"} ב-socket שלו, והפרוקסי כותב ל-Redis
(fp:metrics:proxy) כל כמה שניות.

שמות ותוויות בלבד — בלי כתובות, טוקנים או פרטים אישיים.
"""
from __future__ import annotations

import bisect
import math
import os
import threading
import time
from collections.abc import Iterable

DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

Labels = tuple[tuple[str, str], ...]


def _labels(labels: dict[str, str] | None) -> Labels:
    return tuple(sorted((k, str(v)) for k, v in (labels or {}).items()))


def _fmt_labels(labels: Labels, extra: Iterable[tuple[str, str]] = ()) -> str:
    items = [*labels, *extra]
    if not items:
        return ""
    esc = (v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") for _, v in items)
    return "{" + ",".join(f'{k}="{v}"' for (k, _), v in zip(items, esc, strict=True)) + "}"


def _num(v: float) -> str:
    if v == math.inf:
        return "+Inf"
    return repr(float(v)) if isinstance(v, float) and not v.is_integer() else str(int(v))


class _Metric:
    kind = ""

    def __init__(self, name: str, help_: str):
        self.name = name
        self.help = help_
        self._lock = threading.Lock()


class Counter(_Metric):
    kind = "counter"

    def __init__(self, name: str, help_: str):
        super().__init__(name, help_)
        self._values: dict[Labels, float] = {}

    def inc(self, n: float = 1.0, **labels: str) -> None:
        key = _labels(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + n

    def value(self, **labels: str) -> float:
        return self._values.get(_labels(labels), 0.0)

    def samples(self):
        with self._lock:
            return [(self.name, k, v) for k, v in self._values.items()]


class Gauge(_Metric):
    kind = "gauge"

    def __init__(self, name: str, help_: str):
        super().__init__(name, help_)
        self._values: dict[Labels, float] = {}

    def set(self, v: float, **labels: str) -> None:
        with self._lock:
            self._values[_labels(labels)] = float(v)

    def inc(self, n: float = 1.0, **labels: str) -> None:
        key = _labels(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + n

    def dec(self, n: float = 1.0, **labels: str) -> None:
        self.inc(-n, **labels)

    def value(self, **labels: str) -> float:
        return self._values.get(_labels(labels), 0.0)

    def samples(self):
        with self._lock:
            return [(self.name, k, v) for k, v in self._values.items()]


class Histogram(_Metric):
    kind = "histogram"

    def __init__(self, name: str, help_: str, buckets: tuple[float, ...] = DEFAULT_BUCKETS):
        super().__init__(name, help_)
        self.buckets = tuple(sorted(buckets))
        self._data: dict[Labels, list] = {}      # labels → [counts per bucket..., +Inf count, sum]

    def observe(self, v: float, **labels: str) -> None:
        key = _labels(labels)
        i = bisect.bisect_left(self.buckets, v)
        with self._lock:
            d = self._data.get(key)
            if d is None:
                d = self._data[key] = [0] * (len(self.buckets) + 1) + [0.0]
            d[i] += 1
            d[-1] += v

    def count(self, **labels: str) -> int:
        d = self._data.get(_labels(labels))
        return sum(d[:-1]) if d else 0

    def samples(self):
        out = []
        with self._lock:
            items = [(k, list(d)) for k, d in self._data.items()]
        for key, d in items:
            running = 0
            for b, c in zip((*self.buckets, math.inf), d[:-1], strict=True):
                running += c
                out.append((self.name + "_bucket", key + (("le", _num(b)),), running))
            out.append((self.name + "_count", key, running))
            out.append((self.name + "_sum", key, d[-1]))
        return out


class Registry:
    def __init__(self, prefix: str = "fp"):
        self.prefix = prefix
        self._metrics: dict[str, _Metric] = {}
        self._lock = threading.Lock()

    def _get(self, cls, name: str, help_: str, **kw):
        full = f"{self.prefix}_{name}"
        with self._lock:
            m = self._metrics.get(full)
            if m is None:
                m = self._metrics[full] = cls(full, help_, **kw)
            elif not isinstance(m, cls):
                raise TypeError(f"metric {full} already registered as {m.kind}")
            return m

    def counter(self, name: str, help_: str) -> Counter:
        return self._get(Counter, name, help_)

    def gauge(self, name: str, help_: str) -> Gauge:
        return self._get(Gauge, name, help_)

    def histogram(self, name: str, help_: str, buckets: tuple[float, ...] = DEFAULT_BUCKETS) -> Histogram:
        return self._get(Histogram, name, help_, buckets=buckets)

    def render(self) -> str:
        lines = []
        for m in sorted(self._metrics.values(), key=lambda m: m.name):
            lines.append(f"# HELP {m.name} {m.help}")
            lines.append(f"# TYPE {m.name} {m.kind}")
            for name, labels, value in m.samples():
                lines.append(f"{name}{_fmt_labels(labels)} {_num(value)}")
        return "\n".join(lines) + "\n"

    def snapshot(self) -> dict[str, float]:
        """מדדים שטוחים {שם{תוויות}: ערך} — להעברה בין תהליכים (JSON) ולממשק הניהול."""
        out = {}
        for m in self._metrics.values():
            for name, labels, value in m.samples():
                out[name + _fmt_labels(labels)] = value
        return out


def render_snapshot(snapshot: dict[str, float], source: str) -> str:
    """מדדים שהגיעו מתהליך אחר (snapshot) → טקסט Prometheus, עם תווית source."""
    lines = []
    for key in sorted(snapshot):
        name, brace, rest = key.partition("{")
        labels = f'{{source="{source}",{rest}' if brace else f'{{source="{source}"}}'
        lines.append(f"{name}{labels} {_num(snapshot[key])}")
    return "\n".join(lines) + ("\n" if lines else "")


def process_memory_bytes() -> int:
    """זיכרון התהליך (RSS) — מ-/proc, בלי psutil. 0 אם לא זמין."""
    try:
        with open("/proc/self/statm", encoding="ascii") as f:
            rss_pages = int(f.read().split()[1])
        return rss_pages * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError):
        return 0


class Timer:
    """with Timer() as t: ... ; t.seconds"""

    def __enter__(self) -> Timer:
        self.start = time.perf_counter()
        self.seconds = 0.0
        return self

    def __exit__(self, *exc) -> None:
        self.seconds = time.perf_counter() - self.start
