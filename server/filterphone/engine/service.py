"""שירות מנוע הסינון: מקבל תמונות מהפרוקסי ב-Unix socket ומחזיר החלטה.

זרימה לכל תמונה:
    hash → קאש (Redis) → [פענוח → SigLIP2 → ראשים] → מדיניות → [NudeNet] → החלטה
בקשות זהות שמגיעות במקביל (אותה תמונה פעמיים בדף) מחושבות פעם אחת.

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
from dataclasses import dataclass

import numpy as np

from .. import protocol
from ..settings import Settings, get_settings
from .batcher import Batcher
from .cache import Entry, ResultCache, image_key
from .heads import SCORE_NAMES, HeadSet
from .imaging import PLACEHOLDER_PNG, Decoded, decode, nudenet_input, paint_boxes, siglip_views
from .models import Embedder, NudeDetector
from .policy import Action, Decision, Thresholds, decide, on_failure

log = logging.getLogger("filterphone.engine")

MIN_SIDE_BY_LEVEL = {1: 40}         # ברמה 1 בודקים גם תמונות ממוזערות קטנות יותר


@dataclass(slots=True)
class Verdict:
    action: str                     # pass / block / replace
    reason: str
    body: bytes = b""
    ct: str = ""
    cached: bool = False
    ms: float = 0.0


class Engine:
    def __init__(self, cfg: Settings):
        self.cfg = cfg
        self.heads = HeadSet.load(cfg.heads_file)
        self.thresholds = Thresholds.from_dict(self.heads.thresholds)
        threads = cfg.ort_threads
        self.embedder = Embedder(os.path.join(cfg.models_dir, cfg.embedder_model), threads)
        self.detector = NudeDetector(os.path.join(cfg.models_dir, cfg.nudenet_model), threads)
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
        log.info("engine ready: heads=%s embedder=%s", self.heads.version, self.cfg.embedder_model)

    # ------------------------------------------------------------ ניתוח

    async def classify(self, data: bytes, level: int) -> Verdict:
        t0 = time.perf_counter()
        self.stats["scanned"] += 1
        key = image_key(data)

        entry = await self.cache.get(key)
        cached = entry is not None
        decoded: Decoded | None = None
        if entry is None:
            entry, decoded = await self._analyze_dedup(key, data)

        decision = self._decide(level, entry)
        if decision.action is Action.NEED_DETECTOR:
            entry, decoded = await self._add_detections(key, data, entry, decoded)
            decision = self._decide(level, entry)

        verdict = await self._render(decision, data, decoded)
        verdict.cached = cached
        verdict.ms = round((time.perf_counter() - t0) * 1000, 1)
        self.stats[verdict.action] += 1
        if cached:
            self.stats["cache_hits"] += 1
        return verdict

    def _decide(self, level: int, entry: Entry) -> Decision:
        if entry.scores is None:
            if entry.note == "tiny":
                return Decision(Action.PASS, "tiny")
            return on_failure(level)
        if entry.note == "tiny" and level not in MIN_SIDE_BY_LEVEL:
            return Decision(Action.PASS, "tiny")
        scores = dict(zip(SCORE_NAMES, entry.scores, strict=True))
        dets = [tuple(d) for d in entry.dets] if entry.dets is not None else None
        if dets is None and entry.note == "detector_failed":
            # NudeNet נכשל — לא מנסים שוב לנצח, ומחמירים
            decision = decide(level, scores, self.thresholds, [])
            if decision.action is Action.PASS:
                return on_failure(level) if level == 1 else decision
            return decision
        return decide(level, scores, self.thresholds, dets)

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
        if short < min(MIN_SIDE_BY_LEVEL.values(), default=self.cfg.min_side_px):
            return Entry(None, note="tiny"), decoded
        note = "tiny" if short < self.cfg.min_side_px else ""

        size = self.embedder.size
        views: list[np.ndarray] = []
        for frame in decoded.frames:
            views.extend(await loop.run_in_executor(self.decode_pool, siglip_views, frame, size))
        embs = np.stack(await asyncio.gather(*(self.batcher.submit(v) for v in views)))
        scores = self.heads.scores(embs).max(axis=0)       # הציון המחמיר מבין כל התצוגות
        return Entry([round(float(x), 4) for x in scores], note=note), decoded

    async def _add_detections(self, key: str, data: bytes, entry: Entry,
                              decoded: Decoded | None) -> tuple[Entry, Decoded | None]:
        loop = asyncio.get_running_loop()
        try:
            if decoded is None:
                decoded = await loop.run_in_executor(self.decode_pool, decode, data,
                                                     self.cfg.max_decode_side)
            if decoded is None:
                raise ValueError("undecodable")
            dets: list = []
            for frame in decoded.frames:
                inp = await loop.run_in_executor(self.decode_pool, nudenet_input, frame,
                                                 self.detector.size)
                dets.extend(await loop.run_in_executor(self.infer_pool, self.detector, inp))
            self.stats["detector_runs"] += 1
            entry = Entry(entry.scores, [list(d) for d in dets], entry.note)
        except Exception as e:
            log.warning("detector failed: %s", e)
            entry = Entry(entry.scores, None, "detector_failed")
        await self.cache.put(key, entry)
        return entry, decoded

    async def _render(self, decision: Decision, data: bytes, decoded: Decoded | None) -> Verdict:
        if decision.action is Action.PASS:
            return Verdict("pass", decision.reason)
        if decision.action is Action.PAINT and decoded is not None and not decoded.animated:
            loop = asyncio.get_running_loop()
            body = await loop.run_in_executor(self.decode_pool, paint_boxes,
                                              decoded.primary, list(decision.boxes))
            return Verdict("replace", decision.reason, body, "image/jpeg")
        # BLOCK, או PAINT שאי אפשר לבצע (תמונה מונפשת) → חסימה
        return Verdict("block", decision.reason, PLACEHOLDER_PNG, "image/png")

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
            log.info("stats: scanned=%d cache_hits=%d block=%d replace=%d pass=%d detector=%d undecodable=%d",
                     s["scanned"], s["cache_hits"], s["block"], s["replace"], s["pass"],
                     s["detector_runs"], s["undecodable"])
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
