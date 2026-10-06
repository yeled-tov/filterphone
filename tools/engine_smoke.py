"""בדיקת עשן למנוע עם המודלים האמיתיים: טעינה, סיווג תמונות אמיתיות, קאש וזמנים.

    python tools/engine_smoke.py --models models
דורש Redis מקומי (ב-CI: service container).
"""
from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

# תמונות קבועות מ-COCO val2017: נוף, חיות, ספורט, אנשים
SAMPLES = ["000000000139", "000000000285", "000000000632", "000000000724", "000000000776",
           "000000000785", "000000000802", "000000000872", "000000000885", "000000001000",
           "000000001268", "000000001296", "000000001353", "000000001425", "000000001490"]


async def run(models: str) -> int:
    heads = Path(os.environ.get("FP_HEADS_FILE", ROOT / "server/filterphone/engine/heads.json"))
    if not heads.exists():
        print("heads.json not built yet — skipping smoke test")
        return 0
    os.environ["FP_MODELS_DIR"] = models
    os.environ["FP_ORT_THREADS"] = str(os.cpu_count() or 2)
    from filterphone.engine.service import Engine
    from filterphone.settings import get_settings

    engine = Engine(get_settings())
    await engine.start()
    images = []
    for sid in SAMPLES:
        with urllib.request.urlopen(f"http://images.cocodataset.org/val2017/{sid}.jpg", timeout=60) as r:
            images.append((sid, r.read()))

    cold, warm = [], []
    for sid, data in images:
        row = []
        for level in (1, 2, 3):
            v = await engine.classify(data, level)
            (warm if v.cached else cold).append(v.ms)
            row.append(f"L{level}={v.action:<7} ({v.reason})")
        print(sid, " | ".join(row))
    # כל התמונות שוב — חייבות לבוא מהקאש
    hits = 0
    for _, data in images:
        v = await engine.classify(data, 1)
        hits += v.cached
        warm.append(v.ms)

    # סגמנטציה (רמה 2): המודל נטען ומחזיר מסכה הגיונית לתמונה עם אדם
    fail = 0
    if engine.segmenter is None:
        print("FAIL: segmentation model not loaded")
        fail = 1
    else:
        from filterphone.engine import segment
        from filterphone.engine.heads import SCORE_NAMES
        from filterphone.engine.imaging import decode
        person = SCORE_NAMES.index("person")
        scored = [((await engine._analyze(data))[0].scores or [0] * 4)[person] for _, data in images]
        sid, data = images[max(range(len(images)), key=scored.__getitem__)]
        dec = decode(data, 1024)
        print(f"segmentation sample: {sid} (person score {max(scored):.2f})")
        labels, st = segment.analyze(engine.segmenter, dec.primary)
        print(f"segmentation: person={st.person:.3f} skin={st.skin:.3f} crops={st.crops} "
              f"png={len(segment.encode_labels(labels))}B")
        if not 0 < st.person < 1:
            print("FAIL: segmentation found no person in a people photo")
            fail = 1
    info, v = await engine.explain(images[0][1], 2)
    print("explain:", {k: info[k] for k in ("decisions", "steps", "ms")})
    # רמה 2 צובעת רק נשים: כל תמונה שהגיעה לסגמנטציה מחזיקה סיווג לכל אדם
    seen = 0
    for sid, data in images:
        info, _ = await engine.explain(data, 2)
        seg = info.get("segmentation")
        if seg:
            seen += 1
            people = [("אישה" if w else "גבר", f) for *_, f, w in seg.get("people", [])]
            print(f"  {sid} people: {people} women-area={seg['person']} men-area={seg['others']} skin={seg['skin']}")
            if seg.get("v") != segment.FORMAT:
                print("FAIL: segmentation stats in old format")
                fail += 1
    print(f"level-2 images with per-person classification: {seen}")
    await engine.close()
    def med(xs: list[float]) -> str:
        return f"{statistics.median(xs):.1f}ms" if xs else "n/a"

    print(f"cold: median {med(cold)} | cached: median {med(warm)} | cache hits on repeat: {hits}/{len(images)}")
    if hits != len(images):
        print("FAIL: cache did not return repeated images")
        return 1
    return fail


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="models")
    sys.exit(asyncio.run(run(ap.parse_args().models)))


if __name__ == "__main__":
    main()
