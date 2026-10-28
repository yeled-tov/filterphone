"""אבחון סינון התמונות בשרת — לקריאה בלבד, בלי פרטי לקוחות (רק מזהי מכשיר ומספרים).

מורץ מ-fp-ci-deploy (`images`) בפייתון של השרת:
    sudo /opt/filterphone/venv/bin/python /opt/filterphone/src/deploy/diag_images.py

מה רואים:
  * גרסאות ותמיכה בפורמטים (AVIF/HEIF), המעבד והזיכרון, ההגדרות שקובעות מה נבדק.
  * מדדי המנוע והפרוקסי: כמה תמונות נבדקו / נחסמו / נצבעו, כשלים לפי סיבה, זמני עיבוד, קאש,
    ושרת ה-GPU (Modal): כמה הצליח וכמה נפל חזרה לשרת.
  * לכל מכשיר: רמה, מצב תמונות, ומונים יומיים (נבדקו / נחסמו / נצבעו).
  * 3000 האירועים האחרונים, מקובצים לפי סיבה ולפי שרת תמונות (בלי כתובות מלאות).
  * מדידה חיה: אותן תמונות דרך המנוע המקומי (בלי Modal), ישירות ל-Modal, ובמסלול הרגיל.
    התמונות הן תמונות ציבוריות של COCO; כל ריצה משנה בייט בסוף הקובץ — כך שהקאש לא עונה.
"""
from __future__ import annotations

import collections
import concurrent.futures as cf
import json
import os
import platform
import re
import statistics
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

from filterphone import protocol
from filterphone.engine import imaging
from filterphone.engine import remote as remote_cfg
from filterphone.settings import get_settings

cfg = get_settings()
SAMPLES = ["000000000785", "000000001490", "000000002261", "000000003156", "000000004395",
           "000000000139", "000000000632", "000000000872"]


def sh(*a: str) -> str:
    try:
        return subprocess.run(list(a), capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return ""


def quantiles(snap: dict[str, float], name: str, label: str = "") -> str:
    """p50/p90/p99 מהיסטוגרמה (גבולות הדליים) + ממוצע."""
    buckets = []
    for k, v in snap.items():
        if k.startswith(name + "_bucket") and label in k:
            m = re.search(r'le="([^"]+)"', k)
            if m:
                buckets.append((float("inf") if m.group(1) == "+Inf" else float(m.group(1)), v))
    buckets.sort()
    count = next((v for k, v in snap.items() if k.startswith(name + "_count") and label in k), 0)
    total = next((v for k, v in snap.items() if k.startswith(name + "_sum") and label in k), 0.0)
    if not count:
        return "אין נתונים"
    out = []
    for q in (0.5, 0.9, 0.99):
        le = next((b for b, c in buckets if c >= q * count), float("inf"))
        out.append(f"p{int(q * 100)}≤{le * 1000:.0f}ms" if le != float("inf") else f"p{int(q * 100)}>10s")
    return f"{int(count)} תמונות, ממוצע {total / count * 1000:.0f}ms, " + " ".join(out)


def counters(snap: dict[str, float], name: str) -> dict[str, int]:
    out = {}
    for k, v in snap.items():
        if k.startswith(name + "{") or k == name:
            lab = k[len(name):].strip("{}") or "-"
            out[lab] = int(v)
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


# ---------------------------------------------------------------- גרסאות והגדרות
print("== גרסה:", open("/opt/filterphone/src/REVISION").read().strip() if os.path.exists("/opt/filterphone/src/REVISION") else "?")
try:
    import onnxruntime
    import PIL
    print(f"== Python {platform.python_version()} · Pillow {PIL.__version__} · onnxruntime {onnxruntime.__version__}")
except Exception as e:
    print("== גרסאות:", e)
print("== פורמטים שהמנוע מפענח:", ", ".join(imaging.FORMATS),
      "" if "AVIF" in imaging.FORMATS else "← אין AVIF! (תמונות AVIF ייחסמו כ'לא ניתן לבדוק')")
cpu = re.search(r"Model name:\s*(.+)", sh("lscpu"))
print(f"== מעבד: {cpu.group(1).strip() if cpu else '?'} · {os.cpu_count()} ליבות · עומס {os.getloadavg()}")
mem = re.search(r"Mem:\s+(\S+)\s+(\S+)\s+(\S+)", sh("free", "-h"))
print("== זיכרון (סה\"כ/בשימוש/פנוי):", mem.groups() if mem else "?")
keys = ("min_image_bytes", "min_side_px", "max_image_bytes", "max_decode_side", "ort_threads", "decode_threads",
        "batch_max", "batch_wait_ms", "engine_max_inflight", "engine_max_queue", "engine_job_timeout_s",
        "engine_timeout_s", "engine_pool_size", "modal_mode", "modal_timeout_s", "modal_cooldown_s")
print("== הגדרות:", {k: getattr(cfg, k) for k in keys if hasattr(cfg, k)})
saved = remote_cfg.read_config(remote_cfg.config_path(cfg))
murl = cfg.modal_url or saved.get("url", "")
mkey = cfg.modal_key or saved.get("key", "")
msecret = cfg.modal_secret or saved.get("secret", "")
mmode = cfg.modal_mode if (cfg.modal_url or cfg.modal_key) else saved.get("mode", cfg.modal_mode)
print(f"== Modal: {'מוגדר' if murl and mkey and msecret else 'לא מוגדר'} · מצב={mmode} · "
      f"שרת={urllib.parse.urlparse(murl).hostname or '-'}")

# ---------------------------------------------------------------- מדדים
try:
    header, _ = protocol.call_sync(cfg.engine_socket, {"op": "metrics"}, b"", timeout=3)
    snap = header.get("metrics", {})
    print("== מנוע — תקין:", header.get("healthy"))
    print("   תוצאות:", counters(snap, "fp_engine_jobs_total"))
    print("   כשלים:", counters(snap, "fp_engine_jobs_failed_total"))
    print("   קאש:", counters(snap, "fp_engine_cache_total"))
    print("   הרצות מודלים:", counters(snap, "fp_engine_model_runs_total"))
    print("   Modal:", counters(snap, "fp_engine_remote_total"))
    print("   זמן עיבוד:", quantiles(snap, "fp_engine_job_seconds"))
    print("   זמן בתור:", quantiles(snap, "fp_engine_queue_seconds"))
    print("   עכשיו: בעיבוד", snap.get("fp_engine_active_jobs"), "בתור", snap.get("fp_engine_waiting_jobs"),
          f"זיכרון {snap.get('fp_engine_memory_bytes', 0) / 1e6:.0f}MB")
except Exception as e:
    print("== מנוע — לא עונה:", e)

try:
    import redis
    r = redis.Redis.from_url(cfg.redis_url, decode_responses=True, socket_timeout=3)
    px = {k: float(v) for k, v in r.hgetall("fp:metrics:proxy").items() if not k.startswith("_")}
    print("== פרוקסי — תמונות:", counters(px, "fp_proxy_images_total"))
    print("   שגיאות מנוע:", counters(px, "fp_proxy_engine_errors_total"))
    print("   גדולות מדי / לא ניתנות לפענוח:", counters(px, "fp_proxy_oversize_bodies_total"))
    print("   תמונות בתוך דפים (inline):", counters(px, "fp_proxy_inline_pages_total"))
    print("   זמן תשובה מהמנוע (כפי שהפרוקסי מרגיש):", quantiles(px, "fp_proxy_engine_seconds"))
    other = {k: int(v) for k, v in px.items() if "_bucket" not in k and "_sum" not in k and "_count" not in k
             and not k.startswith(("fp_proxy_images_total", "fp_proxy_engine_errors_total",
                                   "fp_proxy_oversize_bodies_total", "fp_proxy_inline_pages_total"))}
    print("   שאר המונים:", other)
except Exception as e:
    r = None
    print("== פרוקסי — Redis לא זמין:", e)

# ---------------------------------------------------------------- מכשירים
ip_to_id: dict[str, int] = {}
try:
    import sqlite3
    db = sqlite3.connect(f"file:{cfg.db_path}?mode=ro", uri=True)
    for did, ip in db.execute("SELECT id, vpn_ip FROM devices WHERE vpn_ip IS NOT NULL"):
        ip_to_id[ip] = did
except Exception as e:
    print("== מסד הנתונים:", e)

if r is not None:
    pols = r.hgetall("fp:policy")
    print(f"== מדיניות בפרוקסי: {len(pols)} מכשירים")
    for ip, raw in sorted(pols.items(), key=lambda kv: ip_to_id.get(kv[0], 0)):
        try:
            p = json.loads(raw)
        except ValueError:
            continue
        flags = {k: p.get(k) for k in ("level", "images", "app_images", "video") if k in p}
        print(f"   #{ip_to_id.get(ip, '?')}: {flags} חסימות-אתרים={len(p.get('hosts') or [])} "
              f"נתיבים={len(p.get('paths') or [])} מעקפים-קבועים={len(p.get('passthrough') or [])}")
    # מעקפים (passthrough) לכל מכשיר: שרת תמונות שנמצא כאן — התמונות ממנו עוברות בלי בדיקה
    now = time.time()
    pt: dict[str, list[str]] = collections.defaultdict(list)
    for f, exp in r.hgetall("fp:pt").items():
        ip, _, host = f.partition("|")
        try:
            if float(exp) > now:
                pt[ip].append(host)
        except ValueError:
            continue
    imgish = re.compile(r"(googleusercontent|ggpht|gstatic|alicdn|aliexpress|ltwebstatic|shein|img|image|pic|photo|"
                        r"thumb|media|cdn)", re.I)
    print("== מעקפים פעילים (בלי בדיקת HTTPS) לכל מכשיר:")
    for ip, hosts in sorted(pt.items(), key=lambda kv: ip_to_id.get(kv[0], 0)):
        risky = sorted(h for h in hosts if imgish.search(h))
        print(f"   #{ip_to_id.get(ip, '?')}: {len(hosts)} שרתים; שרתי תמונות/CDN: {risky[:12]}")
    for day in (time.strftime("%Y%m%d"), time.strftime("%Y%m%d", time.localtime(time.time() - 86400))):
        st = r.hgetall(f"fp:stats:{day}")
        per: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
        for k, v in st.items():
            ip, _, what = k.partition("|")
            per[ip][what] += int(v)
        print(f"== מונים יומיים {day}:")
        for ip, c in sorted(per.items(), key=lambda kv: -kv[1]["scanned"])[:15]:
            print(f"   #{ip_to_id.get(ip, '?')}: {dict(c)}")
    ev = r.xrevrange("fp:events", count=3000)
    by_reason, by_host = collections.Counter(), collections.Counter()
    first = None
    for _, e in ev:
        first = e.get("ts", first)
        reason = re.sub(r"[0-9.]+", "#", e.get("reason", ""))
        by_reason[(e.get("kind") or "-", e.get("action") or "-", reason)] += 1
        if e.get("kind") in ("image", "inline"):
            host = e.get("host", "")
            by_host[(".".join(host.split(".")[-3:]), e.get("action") or "-")] += 1
    since = f" (מאז {time.strftime('%d/%m %H:%M', time.localtime(int(first)))})" if first else ""
    print(f"== {len(ev)} אירועים אחרונים{since} — לפי סיבה:")
    for (k, a, rs), n in by_reason.most_common(30):
        print(f"   {n:5d}  {k:6s} {a:8s} {rs}")
    print("== תמונות שנחסמו / נצבעו — לפי שרת:")
    for (h, a), n in by_host.most_common(25):
        print(f"   {n:5d}  {a:8s} {h}")

# ---------------------------------------------------------------- מדידה חיה
print("== מדידה חיה (תמונות ציבוריות של COCO):")
imgs = []
for sid in SAMPLES:
    try:
        with urllib.request.urlopen(f"http://images.cocodataset.org/val2017/{sid}.jpg", timeout=20) as f:
            imgs.append(f.read())
    except Exception as e:
        print("   הורדה נכשלה:", sid, e)
salt = os.urandom(4)


def fresh(data: bytes, i: int) -> bytes:
    return data + salt + bytes([i])         # בייטים אחרי סוף ה-JPEG: אותה תמונה, hash אחר (קאש ריק)


def local_explain(data: bytes) -> float:
    t = time.perf_counter()
    protocol.call_sync(cfg.engine_socket, {"op": "explain", "level": 2}, data, timeout=60)
    return (time.perf_counter() - t) * 1000


def classify(data: bytes, level: int) -> tuple[float, dict]:
    t = time.perf_counter()
    h, _ = protocol.call_sync(cfg.engine_socket, {"op": "classify", "level": level, "budget_ms": 9000}, data,
                              timeout=30)
    return (time.perf_counter() - t) * 1000, h


def modal(data: bytes, level: int) -> tuple[float, str]:
    req = urllib.request.Request(murl.rstrip("/"), data=data, method="POST")
    req.add_header("Content-Type", "application/octet-stream")
    req.add_header("X-FP-Level", str(level))
    req.add_header("Modal-Key", mkey)
    req.add_header("Modal-Secret", msecret)
    t = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            resp.read()
            status = str(resp.status)
    except urllib.error.HTTPError as e:
        status = f"HTTP {e.code}"
    except Exception as e:
        status = type(e).__name__
    return (time.perf_counter() - t) * 1000, status


def summary(ms: list[float]) -> str:
    if not ms:
        return "-"
    return f"חציון {statistics.median(ms):.0f}ms, מקסימום {max(ms):.0f}ms (n={len(ms)})"


if imgs:
    try:
        # explain = מקומי בלבד (בלי Modal, בלי קאש): גלאי + סגמנטציה לכל הרמות — העבודה המלאה
        ms = [local_explain(fresh(d, 1)) for d in imgs]
        print("   מקומי, תמונה אחרי תמונה (ניתוח מלא לכל הרמות):", summary(ms))
        with cf.ThreadPoolExecutor(8) as pool:
            t = time.perf_counter()
            ms = list(pool.map(lambda d: local_explain(fresh(d, 2)), imgs))
            print(f"   מקומי, 8 במקביל: {summary(ms)}; סה\"כ {(time.perf_counter() - t) * 1000:.0f}ms")
        for level in (1, 2):
            rows = [classify(fresh(d, 10 + level), level) for d in imgs]
            print(f"   המסלול הרגיל (כמו טלפון) רמה {level}: {summary([m for m, _ in rows])} · "
                  f"תוצאות: {collections.Counter(h.get('action') for _, h in rows)}")
    except Exception as e:
        print("   המנוע המקומי לא עונה:", e)
    if murl and mkey and msecret:
        first = modal(fresh(imgs[0], 20), 2)
        print(f"   Modal — פנייה ראשונה (אולי הפעלה קרה): {first[0]:.0f}ms ({first[1]})")
        rows = [modal(fresh(d, 21), 2) for d in imgs]
        print(f"   Modal — תמונה אחרי תמונה: {summary([m for m, _ in rows])} · "
              f"{collections.Counter(s for _, s in rows)}")
        with cf.ThreadPoolExecutor(8) as pool:
            t = time.perf_counter()
            rows = list(pool.map(lambda d: modal(fresh(d, 22), 2), imgs))
            print(f"   Modal — 8 במקביל: {summary([m for m, _ in rows])}; "
                  f"סה\"כ {(time.perf_counter() - t) * 1000:.0f}ms")
        host = urllib.parse.urlparse(murl).hostname or ""
        ping = sh("curl", "-s", "-o", "/dev/null", "-w", "%{time_connect} %{time_appconnect}", f"https://{host}/")
        print(f"   רשת אל Modal: TCP+TLS = {ping} שניות (כל פנייה פותחת חיבור חדש)")
    else:
        print("   Modal לא מוגדר — אין מה למדוד")
print("== סוף")
