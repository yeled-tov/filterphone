"""שירות מנוע הסינון: מקבל תמונות מהפרוקסי ב-Unix socket ומחזיר החלטה.

זרימה לכל תמונה:
    hash → קאש (Redis) → [פענוח → SigLIP2 → ראשים] → מדיניות → [NudeNet] → [סגמנטציה] → החלטה
בקשות זהות שמגיעות במקביל (אותה תמונה פעמיים בדף) מחושבות פעם אחת.

פעולות (שדה op בכותרת):
    classify (ברירת מחדל) — החלטה לתמונה, מהפרוקסי.
    explain — לממשק הניהול ("בדיקת תמונה"): כל הציונים וההחלטה בכל רמה.

הפעלה:  python -m filterphone.engine.service
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import numpy as np

from .. import protocol
from ..settings import Settings, get_settings
from . import segment
from .batcher import Batcher
from .cache import Entry, ResultCache, image_key
from .heads import SCORE_NAMES, HeadSet
from .imaging import (
    PLACEHOLDER_PNG,
    Decoded,
    decode,
    image_size,
    nudenet_input,
    paint_boxes,
    placeholder,
    siglip_views,
)
from .models import Embedder, NudeDetector, Segmenter
from .policy import Action, Decision, PaintMode, Thresholds, decide, on_failure
from .segment import SegStats

log = logging.getLogger("filterphone.engine")

MIN_SIDE_BY_LEVEL = {1: 40}         # ברמה 1 בודקים גם תמונות ממוזערות קטנות יותר
NO_PERSON = SegStats(0.0, 0.0)      # סגמנטציה שלא רצה/נכשלה — המדיניות חוזרת לגלאי


@dataclass(slots=True)
class Verdict:
    action: str                     # pass / block / replace
    reason: str
    body: bytes = b""
    ct: str = ""
    cached: bool = False
    ms: float = 0.0


@dataclass(slots=True)
class _Work:
    """מה שכבר חושב לתמונה הזו בבקשה הנוכחית (כדי לא לפענח / להריץ פעמיים)."""
    key: str
    data: bytes
    decoded: Decoded | None = None
    labels: np.ndarray | None = None
    seg_failed: bool = False
    steps: list[str] = field(default_factory=list)


class Engine:
    def __init__(self, cfg: Settings):
        self.cfg = cfg
        self.heads = HeadSet.load(cfg.heads_file)
        self.thresholds = Thresholds.from_dict(self.heads.thresholds)
        threads = cfg.ort_threads
        self.embedder = Embedder(os.path.join(cfg.models_dir, cfg.embedder_model), threads)
        self.detector = NudeDetector(os.path.join(cfg.models_dir, cfg.nudenet_model), threads)
        seg_path = os.path.join(cfg.models_dir, cfg.segment_model)
        self.segmenter = Segmenter(seg_path, threads) if os.path.exists(seg_path) else None
        if self.segmenter is None:
            log.warning("segmentation model missing (%s) — level 2 falls back to detector boxes", seg_path)
        # ריצת מודלים — חוט אחד (כל מודל כבר מקביל בפנים); פענוח — חוטים נפרדים
        self.infer_pool = ThreadPoolExecutor(1, thread_name_prefix="infer")
        self.decode_pool = ThreadPoolExecutor(cfg.decode_threads, thread_name_prefix="decode")
        self.batcher = Batcher(self.embedder, self.infer_pool, cfg.batch_max, cfg.batch_wait_ms)
        self.cache = ResultCache(cfg.redis_url, self.heads.version, cfg.cache_ttl_days)
        self.inflight: dict[str, asyncio.Future[Entry]] = {}
        self.stats: Counter[str] = Counter()

    async def start(self) -> None:
        self.batcher.start()
        # חימום: ההרצה הראשונה של ONNX איטית (הקצאות זיכרון)
        dummy = np.zeros((3, self.embedder.size, self.embedder.size), np.float32)
        await self.batcher.submit(dummy)
        log.info("engine ready: heads=%s embedder=%s segmentation=%s", self.heads.version,
                 self.cfg.embedder_model, "on" if self.segmenter else "off")

    # ------------------------------------------------------------ ניתוח

    async def classify(self, data: bytes, level: int) -> Verdict:
        t0 = time.perf_counter()
        self.stats["scanned"] += 1
        work = _Work(image_key(data), data)

        entry = await self.cache.get(work.key)
        cached = entry is not None
        if entry is None:
            entry, work.decoded = await self._analyze_dedup(work.key, data)

        entry, decision = await self._resolve(level, entry, work)
        verdict = await self._render(decision, work, entry)
        verdict.cached = cached
        verdict.ms = round((time.perf_counter() - t0) * 1000, 1)
        self.stats[verdict.action] += 1
        if cached:
            self.stats["cache_hits"] += 1
        return verdict

    async def _resolve(self, level: int, entry: Entry, work: _Work) -> tuple[Entry, Decision]:
        """מריץ את השלבים שהמדיניות מבקשת (גלאי, סגמנטציה) עד להחלטה סופית."""
        decision = self._decide(level, entry, work)
        for _ in range(3):
            if decision.action is Action.NEED_DETECTOR:
                entry = await self._add_detections(work, entry)
            elif decision.action is Action.NEED_SEGMENT:
                entry = await self._add_segmentation(work, entry)
            else:
                break
            decision = self._decide(level, entry, work)
        if decision.action in (Action.NEED_DETECTOR, Action.NEED_SEGMENT):     # לא אמור לקרות
            log.error("policy did not settle: %s", decision)
            decision = on_failure(level)
        return entry, decision

    def _decide(self, level: int, entry: Entry, work: _Work) -> Decision:
        if entry.scores is None:
            if entry.note == "tiny":
                return Decision(Action.PASS, "tiny")
            return on_failure(level)
        if entry.note == "tiny" and level not in MIN_SIDE_BY_LEVEL:
            return Decision(Action.PASS, "tiny")
        scores = dict(zip(SCORE_NAMES, entry.scores, strict=True))
        seg = SegStats.load(entry.seg) if entry.seg else None
        if seg is None and (work.seg_failed or self.segmenter is None):
            seg = NO_PERSON
        dets = [tuple(d) for d in entry.dets] if entry.dets is not None else None
        if dets is None and entry.note == "detector_failed":
            # NudeNet נכשל — לא מנסים שוב לנצח, ומחמירים (ברמות שבהן כשל = חסימה)
            decision = decide(level, scores, self.thresholds, [], seg)
            if decision.action is Action.PASS and on_failure(level).action is Action.BLOCK:
                return on_failure(level)
            return decision
        return decide(level, scores, self.thresholds, dets, seg)

    async def _decode(self, work: _Work) -> Decoded | None:
        if work.decoded is None:
            loop = asyncio.get_running_loop()
            work.decoded = await loop.run_in_executor(self.decode_pool, decode, work.data,
                                                      self.cfg.max_decode_side)
        return work.decoded

    async def _analyze_dedup(self, key: str, data: bytes) -> tuple[Entry, Decoded | None]:
        fut = self.inflight.get(key)
        if fut is not None:
            return await asyncio.shield(fut), None
        fut = asyncio.get_running_loop().create_future()
        self.inflight[key] = fut
        try:
            entry, decoded = await self._analyze(data)
            fut.set_result(entry)
            await self.cache.put(key, entry)
            return entry, decoded
        except BaseException as e:
            fut.set_exception(e)
            fut.exception()             # מסמן כ"נקרא" כדי שלא יודפס אזהרה
            raise
        finally:
            self.inflight.pop(key, None)

    async def _analyze(self, data: bytes) -> tuple[Entry, Decoded | None]:
        loop = asyncio.get_running_loop()
        decoded = await loop.run_in_executor(self.decode_pool, decode, data, self.cfg.max_decode_side)
        if decoded is None:
            self.stats["undecodable"] += 1
            return Entry(None, note="undecodable"), None
        short = min(decoded.width, decoded.height)
        wh = [decoded.width, decoded.height]
        if short < min(MIN_SIDE_BY_LEVEL.values(), default=self.cfg.min_side_px):
            return Entry(None, note="tiny", wh=wh), decoded
        note = "tiny" if short < self.cfg.min_side_px else ""

        size = self.embedder.size
        views: list[np.ndarray] = []
        for frame in decoded.frames:
            views.extend(await loop.run_in_executor(self.decode_pool, siglip_views, frame, size))
        embs = np.stack(await asyncio.gather(*(self.batcher.submit(v) for v in views)))
        scores = self.heads.scores(embs).max(axis=0)       # הציון המחמיר מבין כל התצוגות
        return Entry([round(float(x), 4) for x in scores], note=note, wh=wh), decoded

    async def _add_detections(self, work: _Work, entry: Entry) -> Entry:
        loop = asyncio.get_running_loop()
        try:
            decoded = await self._decode(work)
            if decoded is None:
                raise ValueError("undecodable")
            dets: list = []
            for frame in decoded.frames:
                inp = await loop.run_in_executor(self.decode_pool, nudenet_input, frame,
                                                 self.detector.size)
                dets.extend(await loop.run_in_executor(self.infer_pool, self.detector, inp))
            self.stats["detector_runs"] += 1
            work.steps.append("detector")
            entry = entry.with_(dets=[list(d) for d in dets])
        except Exception as e:
            log.warning("detector failed: %s", e)
            entry = entry.with_(dets=None, note="detector_failed")
        await self.cache.put(work.key, entry)
        return entry

    async def _segment(self, work: _Work) -> SegStats | None:
        """מריץ את הסגמנטציה ושומר את מפת התוויות (בבקשה ובקאש). None = נכשל / אין מודל."""
        if self.segmenter is None:
            work.seg_failed = True
            return None
        loop = asyncio.get_running_loop()
        try:
            decoded = await self._decode(work)
            if decoded is None:
                raise ValueError("undecodable")
            labels, st = await loop.run_in_executor(self.infer_pool, segment.analyze, self.segmenter,
                                                    decoded.primary)
            png = await loop.run_in_executor(self.decode_pool, segment.encode_labels, labels)
        except Exception as e:
            log.warning("segmentation failed: %s", e)
            work.seg_failed = True      # לא נשמר בקאש — בפעם הבאה ננסה שוב
            return None
        work.labels = labels
        work.steps.append("segment")
        self.stats["segment_runs"] += 1
        await self.cache.put_labels(work.key, png)
        return st

    async def _add_segmentation(self, work: _Work, entry: Entry) -> Entry:
        st = await self._segment(work)
        if st is None:
            return entry
        entry = entry.with_(seg=st.dump())
        await self.cache.put(work.key, entry)
        return entry

    async def _labels(self, work: _Work) -> np.ndarray | None:
        """מפת הסגמנטציה: מהבקשה הזו, מהקאש, או חישוב מחדש (אם פגה בקאש לפני הרשומה)."""
        if work.labels is not None:
            return work.labels
        png = await self.cache.get_labels(work.key)
        labels = segment.decode_labels(png) if png else None
        if labels is not None:
            work.labels = labels
            return labels
        await self._segment(work)
        return work.labels

    async def _render(self, decision: Decision, work: _Work, entry: Entry) -> Verdict:
        if decision.action is Action.PASS:
            return Verdict("pass", decision.reason)
        if decision.action is Action.PAINT:
            decoded = await self._decode(work)
            if decoded is not None and not decoded.animated:
                loop = asyncio.get_running_loop()
                labels = None if decision.mode is PaintMode.BOXES else await self._labels(work)
                if labels is not None:
                    body = await loop.run_in_executor(
                        self.decode_pool, lambda: segment.paint_regions(
                            decoded.primary, labels, decision.mode.value, decision.boxes, decision.keep))
                    return Verdict("replace", decision.reason, body, "image/jpeg")
                if decision.boxes:
                    body = await loop.run_in_executor(self.decode_pool, paint_boxes,
                                                      decoded.primary, list(decision.boxes))
                    return Verdict("replace", decision.reason, body, "image/jpeg")
        # BLOCK, או PAINT שאי אפשר לבצע (תמונה מונפשת / אין מסכה) → חסימה, באותן מידות
        wh = entry.wh or ([work.decoded.width, work.decoded.height] if work.decoded else None) \
            or image_size(work.data)
        return Verdict("block", decision.reason, placeholder(*wh) if wh else PLACEHOLDER_PNG, "image/png")

    # ------------------------------------------------------------ ממשק הניהול

    async def explain(self, data: bytes, level: int) -> tuple[dict, Verdict]:
        """כל מה שהמנוע יודע על תמונה, וההחלטה בכל רמה. בלי קאש — חישוב טרי."""
        t0 = time.perf_counter()
        work = _Work(image_key(data), data)
        entry, work.decoded = await self._analyze(data)
        decisions: dict[int, dict] = {}
        verdict = Verdict("pass", "")
        for lvl in (1, 2, 3):
            entry, d = await self._resolve(lvl, entry, work)
            decisions[lvl] = {"action": d.action.value, "reason": d.reason,
                              "mode": d.mode.value if d.action is Action.PAINT else ""}
            if lvl == level:
                verdict = await self._render(d, work, entry)
        dec = work.decoded
        info = {
            "scores": dict(zip(SCORE_NAMES, entry.scores, strict=True)) if entry.scores else None,
            "note": entry.note,
            "detections": [d[:2] for d in entry.dets] if entry.dets else [],
            "segmentation": entry.seg,
            "decisions": decisions,
            "steps": work.steps,
            "image": {"width": dec.width, "height": dec.height, "format": dec.format,
                      "frames": len(dec.frames)} if dec else None,
            "thresholds": {k: getattr(self.thresholds, k) for k in self.thresholds.__dataclass_fields__},
            "heads": self.heads.version,
            "ms": round((time.perf_counter() - t0) * 1000, 1),
        }
        return info, verdict

    async def close(self) -> None:
        await self.batcher.stop()
        await self.cache.close()
        self.infer_pool.shutdown(wait=False, cancel_futures=True)
        self.decode_pool.shutdown(wait=False, cancel_futures=True)


# ---------------------------------------------------------------- שרת ה-socket

class Server:
    def __init__(self, engine: Engine, path: str):
        self.engine = engine
        self.path = path

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                try:
                    header, body = await protocol.read_message(reader)
                except (asyncio.IncompleteReadError, ConnectionError):
                    return
                level = int(header.get("level", 1))
                if header.get("op") == "explain":
                    await self._explain(writer, body, level)
                    continue
                try:
                    v = await self.engine.classify(body, level)
                except Exception as e:
                    log.exception("classify failed")
                    d = on_failure(level)
                    v = Verdict(d.action.value, f"error: {e}",
                                PLACEHOLDER_PNG if d.action is Action.BLOCK else b"",
                                "image/png" if d.action is Action.BLOCK else "")
                writer.write(protocol.encode(
                    {"action": v.action, "reason": v.reason, "ct": v.ct,
                     "cached": v.cached, "ms": v.ms},
                    v.body))
                await writer.drain()
        except protocol.ProtocolError as e:
            log.warning("protocol error: %s", e)
        finally:
            writer.close()

    async def _explain(self, writer: asyncio.StreamWriter, body: bytes, level: int) -> None:
        try:
            info, v = await self.engine.explain(body, level)
            header = {"ok": True, "info": info, "action": v.action, "ct": v.ct}
            out = v.body
        except Exception as e:
            log.exception("explain failed")
            header, out = {"ok": False, "error": str(e)}, b""
        writer.write(protocol.encode(header, out))
        await writer.drain()

    async def serve(self) -> None:
        if os.path.exists(self.path):
            os.unlink(self.path)
        server = await asyncio.start_unix_server(self.handle, path=self.path)
        os.chmod(self.path, 0o660)
        log.info("listening on %s", self.path)
        async with server:
            await server.serve_forever()


async def _report_stats(engine: Engine, every_s: float = 300.0) -> None:
    """סיכום כל 5 דקות ללוג — במקום שורה לכל תמונה (חוסך דיסק)."""
    while True:
        await asyncio.sleep(every_s)
        s = engine.stats
        if s["scanned"]:
            log.info("stats: scanned=%d cache_hits=%d block=%d replace=%d pass=%d detector=%d segment=%d"
                     " undecodable=%d", s["scanned"], s["cache_hits"], s["block"], s["replace"], s["pass"],
                     s["detector_runs"], s["segment_runs"], s["undecodable"])
        engine.stats.clear()


async def main() -> None:
    logging.basicConfig(level=os.environ.get("FP_LOG_LEVEL", "INFO"),
                        format="%(levelname)s %(name)s: %(message)s")
    cfg = get_settings()
    os.makedirs(cfg.run_dir, exist_ok=True)
    engine = Engine(cfg)
    await engine.start()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    tasks = [asyncio.create_task(Server(engine, cfg.engine_socket).serve()),
             asyncio.create_task(_report_stats(engine))]
    await stop.wait()
    for t in tasks:
        t.cancel()
    await engine.close()


if __name__ == "__main__":
    asyncio.run(main())
