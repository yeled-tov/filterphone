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
    await engine.close()
    print(f"cold: median {statistics.median(cold):.0f}ms | cached: median {statistics.median(warm):.1f}ms "
          f"| cache hits on repeat: {hits}/{len(images)}")
    if hits != len(images):
        print("FAIL: cache did not return repeated images")
        return 1
    return 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="models")
    sys.exit(asyncio.run(run(ap.parse_args().models)))


if __name__ == "__main__":
    main()
