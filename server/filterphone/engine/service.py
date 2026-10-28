"""שירות מנוע הסינון: מקבל תמונות מהפרוקסי ב-Unix socket ומחזיר החלטה.

זרימה לכל תמונה:
    hash → קאש (Redis) → [פענוח → SigLIP2 → ראשים] → מדיניות → [NudeNet] → [סגמנטציה] → החלטה
בקשות זהות שמגיעות במקביל (אותה תמונה פעמיים בדף) מחושבות פעם אחת.

פעולות (שדה op בכותרת):
    classify (ברירת מחדל) — החלטה לתמונה, מהפרוקסי. budget_ms = כמה זמן הפרוקסי עוד מחכה.
    explain — לממשק הניהול ("בדיקת תמונה"): כל הציונים וההחלטה בכל רמה.
    metrics — מדדי המנוע (JSON), ל-/metrics של ה-API.

עומס: לכל היותר engine_max_inflight תמונות בעיבוד, ועוד engine_max_queue בתור. תור מלא, או
תמונה שלא הספיקה בזמן שהפרוקסי מחכה — מקבלות מיד את מדיניות הכשל של הרמה (רמות 1–2: חסימה,
רמה 3: מעבר), והעבודה עליהן מתבטלת. כך עומס לא מצטבר, והמנוע לא קורס.

הפעלה:  python -m filterphone.engine.service   (systemd: Type=notify עם watchdog)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import numpy as np

from .. import metrics, protocol, sdnotify
from ..settings import Settings, get_settings
from . import remote as remote_cfg
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
    siglip_crop,
    siglip_views,
)
from .models import Embedder, NudeDetector, Segmenter
from .policy import SMALL_IMAGE_PX, Action, Decision, PaintMode, Thresholds, decide, on_failure, women_among
from .remote import RemoteEngine
from .segment import Person, SegStats

log = logging.getLogger("filterphone.engine")

MIN_SIDE = 24                       # ברמות 1–2 נבדקת כל תמונה שהצלע הקצרה שלה לפחות כזו (מתחת — אייקונים)
SEG_MIN_SIDE = 64                   # מתחת לזה הסגמנטציה כמעט לא מוצאת אנשים (נמדד: 4% ב-48px) — לא מריצים אותה,
                                    # וההחלטה לפי הציונים (policy: תמונה ממוזערת שלא נמצא בה אדם)
NO_PERSON = SegStats(0.0, 0.0)      # סגמנטציה שלא רצה/נכשלה — המדיניות חוזרת לגלאי
FEMALE = SCORE_NAMES.index("female")
MIN_USEFUL_S = 0.25                 # פחות זמן מזה עד שהפרוקסי מוותר — לא מתחילים לעבוד (עבודה שתיזרק)
STUCK_AFTER_S = 60.0                # יש עבודה, ושום תמונה לא הסתיימה בהצלחה דקה שלמה — המנוע תקוע
REMOTE_RECHECK_S = 15.0             # כל כמה זמן בודקים אם הגדרות Modal השתנו בדשבורד


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
        self.infer_pool = ThreadPoolExecutor(max(1, cfg.infer_threads), thread_name_prefix="infer")
        self.decode_pool = ThreadPoolExecutor(cfg.decode_threads, thread_name_prefix="decode")
        self.batcher = Batcher(self.embedder, self.infer_pool, cfg.batch_max, cfg.batch_wait_ms)
        self.cache = ResultCache(cfg.redis_url, self.heads.version, cfg.cache_ttl_days)
        self.inflight: dict[str, asyncio.Future[Entry]] = {}
        self.stats: Counter[str] = Counter()
        self._init_runtime(cfg)
        # Modal (GPU חיצוני): רק אם מוגדר. כשל → נפילה מיידית לניתוח המקומי (ראו _remote)
        self._remote_check_at = 0.0
        self._reload_remote()

    def _init_runtime(self, cfg: Settings) -> None:
        """בקרת עומס ומדדים (נפרד מטעינת המודלים — כדי שאפשר יהיה לבדוק בלי מודלים)."""
        self.cfg = cfg
        self.remote: RemoteEngine | None = None
        self._remote_check_at = float("inf")    # בלי מודלים (בדיקות) — לא קוראים הגדרות מהדיסק
        self._remote_mtime: float | None = None
        self._remote_loaded = False
        self._remote_probe: asyncio.Future | None = None    # בקשה ל-Modal שמתעכבת (ראו _remote)
        self._slots = asyncio.Semaphore(cfg.engine_max_inflight)
        self._explain_slot = asyncio.Semaphore(1)
        self.active = 0
        self.waiting = 0
        self.last_success = time.monotonic()    # תמונה אחרונה שהסתיימה בהצלחה (ל-watchdog)
        self.busy_since = time.monotonic()      # מתי התחילה תקופת העבודה הנוכחית (אחרי מנוחה)
        m = self.metrics = metrics.Registry("fp_engine")
        self.m_jobs = m.counter("jobs_total", "תמונות שהתקבלו, לפי תוצאה סופית")
        self.m_failed = m.counter("jobs_failed_total", "תמונות שנכשלו, לפי סיבה (timeout/overload/error)")
        self.m_job_seconds = m.histogram("job_seconds", "זמן עיבוד תמונה (בלי ההמתנה בתור)")
        self.m_queue_seconds = m.histogram("queue_seconds", "זמן המתנה בתור לפני עיבוד")
        self.m_cache = m.counter("cache_total", "פגיעות בקאש (hit/miss)")
        self.m_models = m.counter("model_runs_total", "הרצות מודלים, לפי מודל")
        self.m_remote = m.counter("remote_total", "ניתוח בשרת ה-GPU (Modal), לפי תוצאה (ok / fallback)")
        self.m_active = m.gauge("active_jobs", "תמונות בעיבוד עכשיו")
        self.m_waiting = m.gauge("waiting_jobs", "תמונות בתור")
        self.m_batch_depth = m.gauge("batch_queue", "תצוגות שממתינות לאצוות SigLIP2")
        self.m_memory = m.gauge("memory_bytes", "זיכרון התהליך (RSS)")

    async def start(self) -> None:
        self.batcher.start()
        # חימום: ההרצה הראשונה של ONNX איטית (הקצאות זיכרון)
        dummy = np.zeros((3, self.embedder.size, self.embedder.size), np.float32)
        await self.batcher.submit(dummy)
        log.info("engine ready: heads=%s embedder=%s segmentation=%s", self.heads.version,
                 self.cfg.embedder_model, "on" if self.segmenter else "off")

    # ------------------------------------------------------------ בקרת עומס

    def _failure(self, level: int, reason: str) -> Verdict:
        d = on_failure(level)
        block = d.action is Action.BLOCK
        return Verdict(d.action.value, reason, PLACEHOLDER_PNG if block else b"", "image/png" if block else "")

    async def run_job(self, data: bytes, level: int, budget_s: float | None = None) -> Verdict:
        """תמונה מהפרוקסי, עם בקרת עומס ומגבלת זמן. לעולם לא זורק — כשל = מדיניות הכשל של הרמה."""
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        budget = min(budget_s or self.cfg.engine_job_timeout_s, self.cfg.engine_job_timeout_s)
        if self.waiting >= self.cfg.engine_max_queue:
            self.m_failed.inc(reason="overload")
            self.m_jobs.inc(action=on_failure(level).action.value)
            return self._failure(level, "overloaded")
        if self.active == 0 and self.waiting == 0:
            self.busy_since = time.monotonic()
        self.waiting += 1
        acquired = False
        try:
            async with asyncio.timeout_at(t0 + budget):
                await self._slots.acquire()
                acquired = True
                self.waiting -= 1
                t1 = loop.time()
                self.m_queue_seconds.observe(t1 - t0)
                if t0 + budget - t1 < MIN_USEFUL_S:
                    raise TimeoutError          # חיכה בתור כמעט את כל הזמן — מפנים את המקום לבקשה אחרת
                self.active += 1
                try:
                    v = await self.classify(data, level)
                    self.last_success = time.monotonic()
                finally:
                    self.active -= 1
                    self.m_job_seconds.observe(loop.time() - t1)
        except TimeoutError:
            self.m_failed.inc(reason="timeout" if acquired else "queue_timeout")
            v = self._failure(level, "timeout")
        except Exception as e:
            log.exception("classify failed")
            self.m_failed.inc(reason="error")
            v = self._failure(level, f"error: {type(e).__name__}")
        finally:
            if acquired:
                self._slots.release()
            else:
                self.waiting -= 1
        self.m_jobs.inc(action=v.action)
        return v

    def healthy(self) -> bool:
        """ל-watchdog: אין עבודה, או שתמונה הסתיימה בהצלחה לאחרונה. אחרת (דקה שלמה שבה כל
        התמונות נכשלות / ממתינות) — חוט ההסקה תקוע, ועדיף הפעלה מחדש."""
        if not self.batcher.alive:
            return False
        if self.active == 0 and self.waiting == 0:
            return True
        return time.monotonic() - max(self.last_success, self.busy_since) < STUCK_AFTER_S

    def snapshot(self) -> dict[str, float]:
        self.m_active.set(self.active)
        self.m_waiting.set(self.waiting)
        self.m_batch_depth.set(self.batcher.depth)
        self.m_memory.set(metrics.process_memory_bytes())
        return self.metrics.snapshot()

    # ------------------------------------------------------------ ניתוח

    async def classify(self, data: bytes, level: int) -> Verdict:
        t0 = time.perf_counter()
        self.stats["scanned"] += 1
        work = _Work(image_key(data), data)

        entry = await self.cache.get(work.key)
        if entry is not None and entry.scores is None and entry.note == "tiny" and entry.wh \
                and min(entry.wh) >= MIN_SIDE:
            entry = None        # נשמר כשסף התמונות הקטנות היה גבוה יותר — עכשיו היא נבדקת
        cached = entry is not None
        if entry is None:
            entry = await self._remote(work, level)
        if entry is None:
            entry, work.decoded = await self._analyze_dedup(work.key, data)

        entry, decision = await self._resolve(level, entry, work)
        verdict = await self._render(decision, work, entry)
        verdict.cached = cached
        verdict.ms = round((time.perf_counter() - t0) * 1000, 1)
        self.stats[verdict.action] += 1
        if cached:
            self.stats["cache_hits"] += 1
        self.m_cache.inc(result="hit" if cached else "miss")
        return verdict

    def _reload_remote(self) -> None:
        """הגדרות Modal מהדשבורד נקלטות תוך REMOTE_RECHECK_S, בלי הפעלה מחדש (בדיקת mtime זולה)."""
        now = time.monotonic()
        if now < self._remote_check_at:
            return
        self._remote_check_at = now + REMOTE_RECHECK_S
        path = remote_cfg.config_path(self.cfg)
        try:
            mtime: float | None = os.stat(path).st_mtime
        except OSError:
            mtime = None
        if self._remote_loaded and mtime == self._remote_mtime:
            return
        self._remote_loaded, self._remote_mtime = True, mtime
        saved = remote_cfg.read_config(path) if mtime is not None else None
        self.remote = RemoteEngine.from_settings(self.cfg, self.heads.version, saved)
        log.info("remote engine (Modal): %s", f"{self.remote.mode} {self.remote.url}" if self.remote else "off")

    async def _remote(self, work: _Work, level: int) -> Entry | None:
        """ניתוח ב-Modal (אם מוגדר וזמין). מחזיר את ה-Entry שהיה נשמר בקאש מקומית — אותם
        ציונים ואותם שלבים (גלאי/סגמנטציה) שהמדיניות ביקשה לרמה הזו — או None (→ ניתוח מקומי)."""
        self._reload_remote()
        if self.remote is None or not self.remote.enabled:
            return None
        # "רק בעומס": כל עוד השרת המקומי פנוי — מנתחים מקומית (חוסך את הקרדיט של Modal)
        if self.remote.mode == "overflow" and self.waiting == 0 \
                and self.active < max(2, self.cfg.engine_max_inflight // 2):
            return None
        if self._remote_probe is not None and not self._remote_probe.done():
            # בקשה קודמת עדיין מחכה ל-Modal (הפעלה קרה / רשת איטית) — לא מצטופפים מאחוריה
            self.m_remote.inc(result="skipped_slow")
            return None
        task = asyncio.ensure_future(self.remote.analyze(work.data, level))
        hedge = self.cfg.modal_hedge_s
        try:
            result = await asyncio.wait_for(asyncio.shield(task), hedge) if hedge > 0 else await task
        except TimeoutError:
            # Modal איטי עכשיו: מנתחים מקומית בלי לחכות. הבקשה ממשיכה ברקע — כשהיא חוזרת
            # (Modal "התעורר") פונים אליו שוב; אם נכשלה — cooldown כרגיל (remote.analyze)
            self._remote_probe = task
            self.stats["remote_slow"] += 1
            self.m_remote.inc(result="slow")
            return None
        if result is None:
            self.stats["remote_fallbacks"] += 1
            self.m_remote.inc(result="fallback")
            return None
        entry, labels = result
        if entry.scores is None and entry.note == "tiny" and entry.wh and min(entry.wh) >= MIN_SIDE:
            return None         # גרסה ישנה ב-Modal דילגה על תמונה קטנה שכאן כן נבדקת — מנתחים מקומית
        await self.cache.put(work.key, entry)
        if labels is not None:
            await self.cache.put_labels(work.key, labels)
            work.labels = segment.decode_labels(labels)
        self.stats["remote_runs"] += 1
        self.m_remote.inc(result="ok")
        return entry

    async def analyze_resolved(self, data: bytes, level: int) -> tuple[Entry, bytes | None]:
        """ניתוח + השלבים שהמדיניות מבקשת לרמה הזו — בדיוק כמו classify, בלי קאש ובלי ציור.
        רץ בשרת Modal: מחזיר את ה-Entry הסופי ואת מפת הסגמנטציה (PNG) אם חושבה."""
        entry, decoded = await self._analyze(data)
        work = _Work(image_key(data), data, decoded=decoded)
        entry, _ = await self._resolve(level, entry, work)
        labels = segment.encode_labels(work.labels) if work.labels is not None else None
        return entry, labels

    async def _resolve(self, level: int, entry: Entry, work: _Work) -> tuple[Entry, Decision]:
        """מריץ את השלבים שהמדיניות מבקשת (גלאי, סגמנטציה) עד להחלטה סופית."""
        decision = self._decide(level, entry, work)
        for _ in range(3):
            if decision.action is Action.NEED_DETECTOR:
                entry = await self._add_detections(work, entry)
            elif decision.action is Action.NEED_SEGMENT:
                # רמה 1 צריכה רק "מי בתמונה" — קודם מעבר ראשון בלבד; רמה 2 צובעת — מסכה מלאה.
                # מעבר ראשון שכבר לא הספיק (ראו _decide) — עכשיו המלא
                coarse = entry.seg is not None and entry.seg.get("crops") == segment.COARSE
                entry = await self._add_segmentation(work, entry, detail=level == 2 or coarse)
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
        if entry.note == "tiny" and level >= 3:
            return Decision(Action.PASS, "tiny")
        scores = dict(zip(SCORE_NAMES, entry.scores, strict=True))
        wh = entry.wh or ([work.decoded.width, work.decoded.height] if work.decoded else None)
        small = bool(wh) and min(wh) < SMALL_IMAGE_PX
        seg = SegStats.load(entry.seg) if entry.seg else None
        if seg is not None and seg.crops == segment.COARSE and (
                level == 2 or (seg.person < self.thresholds.seg_min_person
                               and seg.others >= self.thresholds.seg_min_person)):
            # חושב במעבר ראשון בלבד: לצביעה (רמה 2) צריך את המסכה המלאה; וברמה 1 — כשנמצאו אנשים
            # אבל אף אחד לא זוהה כאישה, ההגדלה של כל אדם (ראש ברור יותר) יכולה לגלות אישה קטנה
            seg = None
        if seg is None and (work.seg_failed or self.segmenter is None):
            seg = NO_PERSON
        dets = [tuple(d) for d in entry.dets] if entry.dets is not None else None
        if dets is None and entry.note == "detector_failed":
            # NudeNet נכשל — לא מנסים שוב לנצח, ומחמירים (ברמות שבהן כשל = חסימה)
            decision = decide(level, scores, self.thresholds, [], seg, small)
            if decision.action is Action.PASS and on_failure(level).action is Action.BLOCK:
                return on_failure(level)
            return decision
        return decide(level, scores, self.thresholds, dets, seg, small)

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
            if not fut.done():
                # מי שמחכה לאותה תמונה מקבל שגיאה רגילה — גם אם הבקשה שלנו בוטלה (פג זמנה)
                fut.set_exception(RuntimeError("analysis cancelled")
                                  if isinstance(e, asyncio.CancelledError) else e)
                fut.exception()         # מסמן כ"נקרא" כדי שלא יודפס אזהרה
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
        if short < MIN_SIDE:
            return Entry(None, note="tiny", wh=wh), decoded
        note = "tiny" if short < self.cfg.min_side_px else ""

        size = self.embedder.size
        views: list[np.ndarray] = []
        for frame in decoded.frames:
            views.extend(await loop.run_in_executor(self.decode_pool, siglip_views, frame, size))
        embs = np.stack(await asyncio.gather(*(self.batcher.submit(v) for v in views)))
        self.m_models.inc(len(views), model="siglip2")
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
            self.m_models.inc(model="nudenet")
            work.steps.append("detector")
            entry = entry.with_(dets=[list(d) for d in dets])
        except Exception as e:
            log.warning("detector failed: %s", e)
            entry = entry.with_(dets=None, note="detector_failed")
        await self.cache.put(work.key, entry)
        return entry

    async def _segment(self, work: _Work, entry: Entry, detail: bool = True) -> SegStats | None:
        """מריץ את הסגמנטציה, מסווג כל אדם (אישה / גבר) ושומר את מפת התוויות — שבה גברים
        מסומנים MAN ולא ייצבעו (בבקשה ובקאש). None = נכשל / אין מודל.
        detail=False: מעבר ראשון בלבד (ראו segment.analyze) — לרמה 1."""
        if self.segmenter is None:
            work.seg_failed = True
            return None
        loop = asyncio.get_running_loop()
        try:
            decoded = await self._decode(work)
            if decoded is None:
                raise ValueError("undecodable")
            im = decoded.primary
            labels, st = await loop.run_in_executor(self.infer_pool, segment.analyze, self.segmenter, im, detail)
            ids, boxes, headed = segment.find_people(labels, im.width, im.height)
            people = await self._classify_people(im, boxes, headed, entry)
            labels = segment.keep_women(labels, ids, {i for i, p in enumerate(people, 1) if p.woman})
            st = segment.stats(labels, st.crops, people)
            png = await loop.run_in_executor(self.decode_pool, segment.encode_labels, labels)
        except Exception as e:
            log.warning("segmentation failed: %s", e)
            work.seg_failed = True      # לא נשמר בקאש — בפעם הבאה ננסה שוב
            return None
        work.labels = labels
        work.steps.append("segment")
        self.stats["segment_runs"] += 1
        self.m_models.inc(model="segmentation")
        await self.cache.put_labels(work.key, png)
        return st

    async def _classify_people(self, im, boxes: list[segment.Box], headed: list[bool],
                               entry: Entry) -> tuple[Person, ...]:
        """לכל אדם: אישה או גבר. ה-MAX_PEOPLE הגדולים שרואים להם ראש — לפי החלק העליון שלהם בלבד
        (SigLIP2 + ראש "אישה", חיתוך אחד לאדם); השאר לפי האחרים / ציון התמונה (women_among)."""
        image_female = float(entry.scores[FEMALE]) if entry.scores else 1.0
        judge = [i for i, h in enumerate(headed) if h][:segment.MAX_PEOPLE]
        female = [-1.0] * len(boxes)
        if judge:
            loop = asyncio.get_running_loop()
            size = self.embedder.size

            vs = await loop.run_in_executor(self.decode_pool, lambda: [
                siglip_crop(im, segment.person_view_box(boxes[i], im.width, im.height), size) for i in judge])
            embs = np.stack(await asyncio.gather(*(self.batcher.submit(v) for v in vs)))
            for i, f in zip(judge, self.heads.scores(embs)[:, FEMALE], strict=True):
                female[i] = round(float(f), 4)
        woman = women_among(female, headed, image_female, self.thresholds)
        return tuple(Person(b, f, w) for b, f, w in zip(boxes, female, woman, strict=True))

    async def _add_segmentation(self, work: _Work, entry: Entry, detail: bool = True) -> Entry:
        if entry.wh and min(entry.wh) < SEG_MIN_SIDE:
            entry = entry.with_(seg=NO_PERSON.dump())       # זעירה: "לא נמצא אדם" — בלי להריץ את המודל
            await self.cache.put(work.key, entry)
            return entry
        st = await self._segment(work, entry, detail)
        if st is None:
            return entry
        entry = entry.with_(seg=st.dump())
        await self.cache.put(work.key, entry)
        return entry

    async def _labels(self, work: _Work, entry: Entry) -> np.ndarray | None:
        """מפת הסגמנטציה: מהבקשה הזו, מהקאש, או חישוב מחדש (אם פגה בקאש לפני הרשומה)."""
        if work.labels is not None:
            return work.labels
        png = await self.cache.get_labels(work.key)
        labels = segment.decode_labels(png) if png else None
        if labels is not None:
            work.labels = labels
            return labels
        await self._segment(work, entry)
        return work.labels

    async def _render(self, decision: Decision, work: _Work, entry: Entry) -> Verdict:
        if decision.action is Action.PASS:
            return Verdict("pass", decision.reason)
        if decision.action is Action.PAINT:
            decoded = await self._decode(work)
            if decoded is not None and not decoded.animated:
                loop = asyncio.get_running_loop()
                labels = None if decision.mode is PaintMode.BOXES else await self._labels(work, entry)
                if labels is not None:
                    body = await loop.run_in_executor(
                        self.decode_pool, lambda: segment.paint_regions(
                            decoded.primary, labels, decision.mode.value, decision.boxes, decision.keep,
                            fill_boxes=decision.fill))
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
        self._server: asyncio.base_events.Server | None = None
        self.connections = 0

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        try:
            while True:
                try:
                    header, body = await protocol.read_message(reader)
                except (asyncio.IncompleteReadError, ConnectionError):
                    return
                op = header.get("op", "classify")
                if op == "metrics":
                    snap = self.engine.snapshot()
                    snap["fp_engine_connections"] = self.connections
                    writer.write(protocol.encode({"ok": True, "metrics": snap, "healthy": self.engine.healthy()}))
                    await writer.drain()
                    continue
                try:
                    level = int(header.get("level", 1))
                except (TypeError, ValueError):
                    level = 1
                if op == "explain":
                    await self._explain(writer, body, level)
                    continue
                budget = header.get("budget_ms")
                budget_s = float(budget) / 1000.0 if isinstance(budget, int | float) and budget > 0 else None
                v = await self.engine.run_job(body, level, budget_s)
                writer.write(protocol.encode(
                    {"action": v.action, "reason": v.reason, "ct": v.ct,
                     "cached": v.cached, "ms": v.ms},
                    v.body))
                await writer.drain()
        except protocol.ProtocolError as e:
            log.warning("protocol error: %s", e)
        except ConnectionError:
            pass                        # הפרוקסי סגר את החיבור (ויתר על הבקשה)
        finally:
            self.connections -= 1
            writer.close()

    async def _explain(self, writer: asyncio.StreamWriter, body: bytes, level: int) -> None:
        try:
            async with self.engine._explain_slot, asyncio.timeout(60):
                info, v = await self.engine.explain(body, level)
            header = {"ok": True, "info": info, "action": v.action, "ct": v.ct}
            out = v.body
        except TimeoutError:
            header, out = {"ok": False, "error": "timeout"}, b""
        except Exception as e:
            log.exception("explain failed")
            header, out = {"ok": False, "error": type(e).__name__}, b""
        writer.write(protocol.encode(header, out))
        await writer.drain()

    async def start(self) -> None:
        if os.path.exists(self.path):
            os.unlink(self.path)
        self._server = await asyncio.start_unix_server(self.handle, path=self.path)
        os.chmod(self.path, 0o660)
        log.info("listening on %s", self.path)

    async def drain(self, timeout_s: float = 8.0) -> None:
        """כיבוי מסודר: מפסיקים לקבל חיבורים, ומחכים שהתמונות שבעיבוד יסתיימו."""
        if self._server is not None:
            self._server.close()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        while (self.engine.active or self.engine.waiting) and loop.time() < deadline:
            await asyncio.sleep(0.05)


async def _report_stats(engine: Engine, every_s: float = 300.0) -> None:
    """סיכום כל 5 דקות ללוג — במקום שורה לכל תמונה (חוסך דיסק)."""
    while True:
        await asyncio.sleep(every_s)
        s = engine.stats
        if s["scanned"]:
            log.info("stats %s", json.dumps({
                "scanned": s["scanned"], "cache_hits": s["cache_hits"], "block": s["block"],
                "replace": s["replace"], "pass": s["pass"], "detector": s["detector_runs"],
                "segment": s["segment_runs"], "undecodable": s["undecodable"],
                "remote": s["remote_runs"], "remote_fallbacks": s["remote_fallbacks"],
                "memory_mb": metrics.process_memory_bytes() // 1_000_000}))
        engine.stats.clear()


async def _watchdog(engine: Engine, interval: float) -> None:
    """systemd מפעיל מחדש אם הפינג לא מגיע: לולאת האירועים תקועה, או חוט ההסקה תקוע."""
    while True:
        if engine.healthy():
            sdnotify.notify("WATCHDOG=1")
        else:
            log.error("engine unhealthy: active=%d waiting=%d batch_queue=%d — skipping watchdog ping",
                      engine.active, engine.waiting, engine.batcher.depth)
        await asyncio.sleep(interval)


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

    server = Server(engine, cfg.engine_socket)
    await server.start()
    tasks = [asyncio.create_task(_report_stats(engine))]
    interval = sdnotify.watchdog_interval_s()
    if interval:
        tasks.append(asyncio.create_task(_watchdog(engine, interval)))
    sdnotify.notify("READY=1")
    await stop.wait()
    sdnotify.notify("STOPPING=1")
    await server.drain()
    for t in tasks:
        t.cancel()
    await engine.close()


if __name__ == "__main__":
    asyncio.run(main())
