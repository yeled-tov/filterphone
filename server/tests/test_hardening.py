"""בדיקות להקשחה: פצצות דחיסה, בקרת עומס במנוע, מפסק בפרוקסי, SSRF, מגבלות גוף, מוכנות ומדדים."""
import asyncio
import gzip
import io
import os
import socket
import stat
import time
import zlib

import fakeredis
import numpy as np
import pytest
from helpers import admin_headers
from PIL import Image

from filterphone import metrics, protocol, sdnotify
from filterphone.api import db, fetch
from filterphone.api.security import RateLimiter
from filterphone.api.wg import WireGuard
from filterphone.engine import imaging
from filterphone.engine.batcher import Batcher
from filterphone.engine.service import Engine, Server, Verdict
from filterphone.proxy import addon as A
from filterphone.proxy.bodies import decode_bounded
from filterphone.proxy.client import CircuitBreaker, EngineClient, EngineUnavailable
from filterphone.proxy.policy import LEVELS_KEY, POLICY_KEY, POLICY_REV_KEY, DevicePolicy, PolicyTable
from filterphone.settings import Settings

# ------------------------------------------------------------------ פצצות דחיסה


def test_bounded_decode_roundtrips_and_stops_bombs():
    import brotli
    import zstandard

    data = b"<html>" + b"x" * 5000 + b"</html>"
    for enc, comp in [("gzip", gzip.compress(data)), ("deflate", zlib.compress(data)), ("br", brotli.compress(data)),
                      ("zstd", zstandard.ZstdCompressor().compress(data)), ("identity", data), ("", data)]:
        assert decode_bounded(comp, enc, 10_000) == data, enc
    bomb = b"\0" * 50_000_000
    for enc, comp in [("gzip", gzip.compress(bomb)), ("br", brotli.compress(bomb, quality=1)),
                      ("zstd", zstandard.ZstdCompressor().compress(bomb))]:
        assert decode_bounded(comp, enc, 1_000_000) is None, enc
    assert decode_bounded(brotli.compress(data)[:-4], "br", 10**6) is None          # קטוע
    assert decode_bounded(b"not gzip", "gzip", 10**6) is None
    assert decode_bounded(b"x", "compress", 10**6) is None                           # קידוד לא מוכר
    assert decode_bounded(b"x" * 11, "", 10) is None                                 # גדול מהתקרה


def _png(w, h, mode="1") -> bytes:
    buf = io.BytesIO()
    Image.new(mode, (w, h)).save(buf, "PNG")
    return buf.getvalue()


def test_decompression_bomb_image_is_rejected_not_decoded():
    data = _png(9000, 9000)                     # 81 מגה-פיקסל בכמה KB
    assert len(data) < 100_000
    assert imaging.decode(data) is None
    assert imaging.image_size(data) is None
    assert imaging.decode(_png(300, 200, "RGB")) is not None


def test_exotic_formats_are_not_decoded():
    buf = io.BytesIO()
    Image.new("RGB", (64, 64)).save(buf, "TGA")
    assert imaging.decode(buf.getvalue()) is None


def test_long_animation_decodes_only_within_pixel_budget():
    frames = [Image.new("P", (1500, 1500), i % 200) for i in range(12)]
    buf = io.BytesIO()
    frames[0].save(buf, "GIF", save_all=True, append_images=frames[1:])
    t = time.perf_counter()
    d = imaging.decode(buf.getvalue())
    assert d is not None and 1 <= len(d.frames) <= imaging.MAX_FRAMES
    assert time.perf_counter() - t < 2.0


async def test_proxy_blocks_gzip_bomb_image_and_skips_bomb_page(monkeypatch):
    a = A.FilterPhone()
    a.redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    called = []

    class Eng:
        async def classify(self, body, header):
            called.append(1)
            return {"action": "pass", "reason": "", "ct": ""}, b""

    a.engine = Eng()
    ip = "10.8.0.30"
    a.policies.set(ip, DevicePolicy(level=2))
    from mitmproxy import http
    from mitmproxy.test import tflow

    def flow(ct, body, enc):
        f = tflow.tflow(resp=True)
        f.client_conn.peername = (ip, 1234)
        f.request.url = "https://evil.example/x"
        f.response = http.Response.make(200, b"", {"content-type": ct, "content-encoding": enc})
        f.response.raw_content = body
        return f

    bomb = gzip.compress(b"\0" * 40_000_000)
    img = flow("image/jpeg", bomb, "gzip")
    img.metadata["fp_kind"] = "image"
    await a.response(img)
    assert img.response.headers["x-filterphone"] == "block"     # רמה 2: לא ניתן לבדוק = חסימה
    assert not called
    page_body = gzip.compress(b"data:image" + b"\0" * 30_000_000, mtime=0)
    page = flow("text/html", page_body, "gzip")
    page.metadata["fp_kind"] = "inline"
    await a.response(page)
    assert page.response.raw_content == page_body       # לא נגעו
    assert a.m_oversize.value(kind="inline") == 1
    for t in list(a._background):
        await t


# ------------------------------------------------------------------ מנוע: בקרת עומס


class StubEngine(Engine):
    """מנוע בלי מודלים: classify מוחלף בפונקציה מהבדיקה."""

    def __init__(self, cfg, classify):
        self._classify = classify
        self.batcher = Batcher(lambda b: b, None, 8, 1)
        self._init_runtime(cfg)

    async def classify(self, data, level):
        return await self._classify(data, level)


def cfg(**kw):
    return Settings(**{"engine_max_inflight": 2, "engine_max_queue": 3, "engine_job_timeout_s": 5.0, **kw})


async def test_engine_times_out_and_returns_failure_policy():
    async def slow(data, level):
        await asyncio.sleep(10)

    e = StubEngine(cfg(), slow)
    v = await e.run_job(b"x", 1, budget_s=0.05)
    assert v.action == "block" and v.reason == "timeout" and v.body
    v = await e.run_job(b"x", 3, budget_s=0.05)
    assert v.action == "pass" and v.reason == "timeout"
    assert e.active == 0 and e.waiting == 0
    assert e.m_failed.value(reason="timeout") == 2


async def test_engine_rejects_when_queue_is_full_and_recovers():
    gate = asyncio.Event()

    async def wait(data, level):
        await gate.wait()
        return Verdict("pass", "ok")

    e = StubEngine(cfg(), wait)
    e.batcher.start()
    tasks = [asyncio.create_task(e.run_job(b"x", 2, 5.0)) for _ in range(5)]     # 2 בעיבוד + 3 בתור
    await asyncio.sleep(0.05)
    assert e.active == 2 and e.waiting == 3
    v = await e.run_job(b"x", 2, 5.0)                 # התור מלא — דחייה מיידית
    assert v.reason == "overloaded" and v.action == "block"
    gate.set()
    results = await asyncio.gather(*tasks)
    assert all(r.action == "pass" for r in results)
    assert e.active == 0 and e.waiting == 0
    assert e.healthy()
    await e.batcher.stop()


async def test_engine_error_never_escapes():
    async def boom(data, level):
        raise RuntimeError("model exploded")

    e = StubEngine(cfg(), boom)
    v = await e.run_job(b"x", 2)
    assert v.action == "block" and v.reason.startswith("error")


async def test_watchdog_health_detects_stuck_inference(monkeypatch):
    gate = asyncio.Event()

    async def stuck(data, level):
        await gate.wait()
        return Verdict("pass", "ok")

    e = StubEngine(cfg(), stuck)
    e.batcher.start()
    task = asyncio.create_task(e.run_job(b"x", 1, 5.0))
    await asyncio.sleep(0.01)
    assert e.healthy()                                  # עבודה חדשה — עוד לא "תקוע"
    e.busy_since = e.last_success = time.monotonic() - 120
    assert not e.healthy()
    gate.set()
    await task
    assert e.healthy()
    await e.batcher.stop()


async def test_dedup_waiters_get_regular_error_when_owner_is_cancelled():
    e = StubEngine(cfg(), None)
    e.inflight = {}
    started = asyncio.Event()

    async def analyze(data):
        started.set()
        await asyncio.sleep(10)

    e._analyze = analyze
    owner = asyncio.create_task(e._analyze_dedup("k", b"x"))
    await started.wait()
    waiter = asyncio.create_task(e._analyze_dedup("k", b"x"))
    await asyncio.sleep(0)
    owner.cancel()
    with pytest.raises(RuntimeError):
        await waiter
    with pytest.raises(asyncio.CancelledError):
        await owner
    assert "k" not in e.inflight


async def test_batcher_skips_requests_nobody_waits_for():
    seen = []

    def fn(batch):
        seen.append(len(batch))
        return batch

    from concurrent.futures import ThreadPoolExecutor
    b = Batcher(fn, ThreadPoolExecutor(1), 8, 30)
    b.start()
    t1 = asyncio.create_task(b.submit(np.zeros(3)))
    t2 = asyncio.create_task(b.submit(np.ones(3)))
    await asyncio.sleep(0)
    t1.cancel()
    out = await t2
    assert out.tolist() == [1, 1, 1] and seen == [1]
    await b.stop()


async def test_engine_socket_serves_metrics_and_budget(tmp_path):
    seen = []

    async def fast(data, level):
        return Verdict("pass", "clean")

    e = StubEngine(cfg(), fast)
    orig = e.run_job

    async def spy(data, level, budget_s=None):
        seen.append(budget_s)
        return await orig(data, level, budget_s)

    e.run_job = spy
    e.batcher.start()
    path = str(tmp_path / "e.sock")
    srv = Server(e, path)
    await srv.start()
    r, w = await asyncio.open_unix_connection(path)
    w.write(protocol.encode({"level": 2, "budget_ms": 1500}, b"img"))
    await w.drain()
    h, _ = await protocol.read_message(r)
    assert h["action"] == "pass"
    w.write(protocol.encode({"op": "metrics"}))
    await w.drain()
    h, _ = await protocol.read_message(r)
    assert h["ok"] and h["healthy"] and h["metrics"]['fp_engine_jobs_total{action="pass"}'] == 1
    assert seen == [1.5]
    w.close()
    await srv.drain(0.1)
    await e.batcher.stop()


def test_sd_notify_sends_datagram(tmp_path, monkeypatch):
    path = str(tmp_path / "notify.sock")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    s.bind(path)
    monkeypatch.setenv("NOTIFY_SOCKET", path)
    assert sdnotify.notify("READY=1")
    assert s.recv(100) == b"READY=1"
    s.close()
    monkeypatch.setenv("WATCHDOG_USEC", "40000000")
    monkeypatch.setenv("WATCHDOG_PID", str(os.getpid()))
    assert sdnotify.watchdog_interval_s() == 20.0
    monkeypatch.setenv("WATCHDOG_PID", "1")
    assert sdnotify.watchdog_interval_s() is None
    monkeypatch.delenv("NOTIFY_SOCKET")
    assert not sdnotify.notify("WATCHDOG=1")


# ------------------------------------------------------------------ פרוקסי: לקוח המנוע


def test_circuit_breaker_opens_backs_off_and_probes():
    now = [100.0]
    b = CircuitBreaker(threshold=3, base_s=2, max_s=10, clock=lambda: now[0])
    for _ in range(3):
        assert b.allow()
        b.failure()
    assert not b.allow() and b.is_open
    for _ in range(50):
        b.failure()                      # בקשות ישנות שהיו בדרך — לא מאריכות
    now[0] += 2.1
    assert b.allow()                     # ניסיון אחד
    assert not b.allow()                 # רק אחד בכל פעם
    b.failure()                          # נכשל שוב — פתוח לזמן כפול
    now[0] += 2.1
    assert not b.allow()
    now[0] += 2.0
    assert b.allow()
    b.abandon()                          # הניסיון לא הגיע למנוע — מותר ניסיון אחר
    assert b.allow()
    b.success()
    assert b.allow() and b.failures == 0 and not b.is_open


async def _echo_engine(path, replies, drop_after_reply=False):
    """מנוע מזויף. drop_after_reply: סוגר כל חיבור אחרי תשובה אחת (כמו מנוע שהופעל מחדש
    בזמן שהחיבור חיכה במאגר)."""

    async def handle(r, w):
        try:
            while True:
                h, body = await protocol.read_message(r)
                replies.append(h)
                w.write(protocol.encode({"action": "pass", "reason": "ok"}))
                await w.drain()
                if drop_after_reply:
                    w.close()
                    return
        except (asyncio.IncompleteReadError, ConnectionError):
            pass

    return await asyncio.start_unix_server(handle, path=path)


async def test_engine_client_retries_stale_connection_and_sends_budget(tmp_path):
    path = str(tmp_path / "e.sock")
    seen = []
    srv = await _echo_engine(path, seen, drop_after_reply=True)
    c = EngineClient(path, 2, 3.0)
    h, _ = await c.classify(b"a", {"level": 1})
    assert h["action"] == "pass" and 0 < seen[0]["budget_ms"] <= 3000 - 400
    await asyncio.sleep(0.05)                       # המנוע סגר את החיבור שחזר למאגר
    assert len(c._idle) == 1
    h, _ = await c.classify(b"b", {"level": 1})     # החיבור הישן נכשל — ניסיון אחד בחיבור חדש
    assert h["action"] == "pass" and len(seen) == 2
    assert c.breaker.failures == 0
    c.close_idle()
    srv.close()


async def test_engine_client_opens_circuit_when_engine_is_down(tmp_path):
    c = EngineClient(str(tmp_path / "missing.sock"), 2, 0.5, CircuitBreaker(threshold=2, base_s=5))
    for _ in range(2):
        with pytest.raises(OSError):
            await c.classify(b"x", {})
    t = time.perf_counter()
    with pytest.raises(EngineUnavailable):
        await c.classify(b"x", {})
    assert time.perf_counter() - t < 0.05             # לא מחכים בכלל


async def test_engine_client_deadline_covers_waiting_for_a_slot(tmp_path):
    path = str(tmp_path / "slow.sock")

    async def handle(r, w):
        await protocol.read_message(r)
        await asyncio.sleep(5)

    srv = await asyncio.start_unix_server(handle, path=path)
    c = EngineClient(path, 1, 0.3, CircuitBreaker(threshold=2))
    c._last_ok -= 10                                  # המנוע לא ענה כבר הרבה זמן
    t = time.perf_counter()
    results = await asyncio.gather(*(c.classify(b"x", {}) for _ in range(5)), return_exceptions=True)
    assert all(isinstance(r, TimeoutError) for r in results)
    assert time.perf_counter() - t < 1.0              # הממתינים לא חיכו 0.3 כל אחד בתורו
    assert c.breaker.failures == 1                    # רק הבקשה שהגיעה למנוע נחשבת כשל — עומס אצלנו לא
    srv.close()


async def test_engine_client_timeouts_under_load_do_not_open_circuit(tmp_path):
    """המנוע עונה (לאט) — פסק זמן של חלק מהבקשות בעומס לא פותח את המפסק לכל התמונות."""
    path = str(tmp_path / "busy.sock")

    async def handle(r, w):
        try:
            while True:
                await protocol.read_message(r)
                await asyncio.sleep(0.15)
                w.write(protocol.encode({"action": "pass"}))
                await w.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass

    srv = await asyncio.start_unix_server(handle, path=path)
    c = EngineClient(path, 2, 0.5, CircuitBreaker(threshold=2))
    results = await asyncio.gather(*(c.classify(b"x", {}) for _ in range(20)), return_exceptions=True)
    assert any(isinstance(r, TimeoutError) for r in results) and any(isinstance(r, tuple) for r in results)
    assert c.breaker.allow() and not c.breaker.is_open
    c.close_idle()
    srv.close()


# ------------------------------------------------------------------ פרוקסי: טבלת המדיניות


async def test_policy_table_reloads_only_on_revision_change():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    now = [0.0]
    t = PolicyTable(r, 1, clock=lambda: now[0])
    await r.hset(LEVELS_KEY, "10.8.0.11", 3)
    await r.set("filterphone:vpn_ip:10.8.0.5:filter_level", "2")
    assert await t.refresh()
    assert t.level("10.8.0.11") == 3 and t.level("10.8.0.5") == 2
    await r.hset(POLICY_KEY, "10.8.0.11", '{"level": 2, "hosts": ["tiktok.com"]}')
    now[0] += 3
    assert not await t.refresh()                      # בלי שינוי גרסה — לא קוראים
    assert t.level("10.8.0.11") == 3
    await r.incr(POLICY_REV_KEY)
    now[0] += 3
    assert await t.refresh()
    assert t.get("10.8.0.11").blocks("www.tiktok.com")
    scans = []
    orig = t._scan_legacy

    async def counting():
        scans.append(1)
        return await orig()

    t._scan_legacy = counting
    for _ in range(20):                               # שינויים תכופים — סריקת מפתחות ישנים רק פעם ב-10 דקות
        await r.incr(POLICY_REV_KEY)
        now[0] += 3
        await t.refresh()
    assert scans == []
    now[0] += 600
    await t.refresh(force=True)
    assert scans == [1]


# ------------------------------------------------------------------ API


def test_rate_limiter_is_bounded_and_forgets_old_keys():
    rl = RateLimiter(limit=2, window_s=0.05, max_keys=100)
    assert rl.allow("a") and rl.allow("a") and not rl.allow("a")
    for i in range(100):
        rl.allow(f"ip{i}")
    assert len(rl._hits) <= 100
    assert not rl.allow("new-one-when-full") or len(rl._hits) <= 101
    time.sleep(0.06)
    assert rl.allow("a")                               # החלון עבר
    assert len(rl._hits) < 10                          # ישנים נמחקו


def test_ssrf_blocks_mapped_and_numeric_forms():
    for url in ("http://[::ffff:127.0.0.1]/", "http://2130706433/", "http://0.0.0.0/", "https://u:p@example.com/",
                "http://example.com:99999/", "file:///etc/passwd"):
        with pytest.raises(fetch.FetchError):
            fetch._check_url(url)


def test_fetch_connects_to_the_checked_ip_not_a_second_lookup(monkeypatch):
    """DNS rebinding: השם נבדק פעם אחת, והחיבור הולך לכתובת שנבדקה."""
    import http.server
    import threading

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = _png(80, 80, "RGB")
            self.send_response(200)
            self.send_header("content-type", "image/png")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    lookups = []

    def fake_public_ip(host, port):
        lookups.append(host)
        return "127.0.0.1"                              # "הכתובת הציבורית" שנבדקה

    real_getaddrinfo = socket.getaddrinfo

    def no_name_lookups(host, *a, **k):
        if host == "images.example":
            pytest.fail("second DNS lookup")
        return real_getaddrinfo(host, *a, **k)          # כתובת IP מספרית — בלי DNS

    monkeypatch.setattr(fetch, "_public_ip", fake_public_ip)
    monkeypatch.setattr(socket, "getaddrinfo", no_name_lookups)
    data, ct = fetch.fetch_image(f"http://images.example:{srv.server_port}/a.png", 10**6)
    assert ct == "image/png" and data.startswith(b"\x89PNG") and lookups == ["images.example"]
    srv.shutdown()


def test_fetch_gives_up_on_slow_drip(monkeypatch):
    import http.server
    import threading

    class Slow(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("content-type", "image/png")
            self.end_headers()
            try:
                for _ in range(50):
                    self.wfile.write(b"x")
                    self.wfile.flush()
                    time.sleep(0.1)
            except OSError:
                pass

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Slow)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(fetch, "_public_ip", lambda host, port: "127.0.0.1")
    t = time.perf_counter()
    with pytest.raises(fetch.FetchError):
        fetch.fetch_image(f"http://slow.example:{srv.server_port}/", 10**6, timeout=2, total_s=0.5)
    assert time.perf_counter() - t < 2
    srv.shutdown()


def test_body_limit_rejects_large_requests(env):
    svc, client = env
    r = client.post("/v1/register", content=b"{" + b" " * 2_000_000 + b"}",
                    headers={"content-type": "application/json"})
    assert r.status_code == 413 and r.json()["error"]

    def chunks():
        for _ in range(30):
            yield b" " * 100_000

    r = client.post("/v1/enroll", content=chunks(), headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_ready_and_metrics(env, monkeypatch):
    svc, client = env
    monkeypatch.setattr(svc, "engine_metrics", lambda max_age_s=5.0: {"ok": True, "healthy": True, "metrics": {
        'fp_engine_jobs_total{action="pass"}': 7}})
    r = client.get("/ready")
    assert r.status_code == 200 and r.json()["checks"] == {
        "db": True, "redis": True, "engine": True, "wireguard": True, "adguard": True}
    client.get("/v1/catalog")
    m = client.get("/metrics").text
    assert 'fp_api_requests_total{method="GET",route="/v1/catalog",status="2xx"} 1' in m
    assert 'fp_engine_jobs_total{source="engine",action="pass"} 7' in m
    assert "fp_engine_up 1" in m and 'fp_devices{' not in m or "fp_devices" in m


def test_ready_fails_without_redis(env, monkeypatch):
    svc, client = env
    import redis as R

    def down():
        raise R.ConnectionError("down")

    monkeypatch.setattr(svc.redis, "ping", down)
    r = client.get("/ready")
    assert r.status_code == 503 and r.json()["checks"]["redis"] is False


def test_redis_outage_returns_503_json_not_500(env, monkeypatch):
    svc, client = env
    import redis as R

    def down(*a, **k):
        raise R.ConnectionError("down")

    h = admin_headers(client)
    monkeypatch.setattr(svc.redis, "xrevrange", down)
    monkeypatch.setattr(svc, "recent_events", lambda *a, **k: down())
    r = client.get("/v1/admin/events", headers=h)
    assert r.status_code == 503 and "error" in r.json()


def test_db_pool_bounds_connections_and_times_out(tmp_path):
    path = str(tmp_path / "x.db")
    db.init(path)
    pool = db.Pool(path, size=2, wait_s=0.1)
    with pool.connection() as a, pool.connection() as b:
        assert a is not b
        with pytest.raises(db.PoolTimeout):
            with pool.connection():
                pass
    with pool.connection() as c:
        c.execute("BEGIN IMMEDIATE")                  # נשכח פתוח — המאגר מבטל לפני שימוש חוזר
    with pool.connection() as c:
        assert not c.in_transaction
    pool.close()


def test_redis_resync_after_redis_wipe(env):
    svc, client = env
    svc.sync_redis()
    assert not svc.sync_redis_if_needed()
    svc.redis.flushall()
    assert svc.sync_redis_if_needed()
    assert svc.redis.exists("fp:policy:synced")


# ------------------------------------------------------------------ WireGuard


def _fake_wg(tmp_path, peers):
    """'wg' מזויף: מחזיק מצב בקובץ, ורושם כל קריאה."""
    import json

    state = tmp_path / "wg.json"
    state.write_text(json.dumps(peers))
    log = tmp_path / "wg.log"
    script = tmp_path / "wg"
    script.write_text(f"""#!/usr/bin/env python3
import json, sys
state = json.load(open({str(state)!r}))
args = sys.argv[1:]
open({str(log)!r}, "a").write(" ".join(args) + "\\n")
stdin = sys.stdin.read() if "/dev/stdin" in args else ""
if args[:1] == ["show"]:
    what = args[2]
    for k, v in state.items():
        if what == "allowed-ips": print(k + "\\t" + v["ip"])
        elif what == "preshared-keys": print(k + "\\t" + (v["psk"] or "(none)"))
        elif what == "latest-handshakes": print(k + "\\t0")
        elif what == "transfer": print(k + "\\t0\\t0")
elif args[:1] == ["addconf"]:
    cur = None
    for line in stdin.splitlines():
        if line == "[Peer]": cur = None
        k, _, v = line.partition(" = ")
        if k == "PublicKey": cur = v; state[cur] = {{"ip": "", "psk": ""}}
        elif k == "AllowedIPs": state[cur]["ip"] = v
        elif k == "PresharedKey": state[cur]["psk"] = v
elif args[:1] == ["set"]:
    i = 2
    while i < len(args):
        key = args[i + 1]; i += 2
        if i < len(args) and args[i] == "remove":
            state.pop(key, None); i += 1; continue
        state.setdefault(key, {{"ip": "", "psk": ""}})
        while i < len(args) and args[i] != "peer":
            if args[i] == "allowed-ips": state[key]["ip"] = args[i + 1]
            if args[i] == "preshared-key": state[key]["psk"] = stdin.strip()
            i += 2
json.dump(state, open({str(state)!r}, "w"))
""")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return WireGuard("wg0", str(script)), state, log


def test_wireguard_reconcile_changes_only_what_differs(tmp_path):
    import json

    wg, state, log = _fake_wg(tmp_path, {
        "A": {"ip": "10.8.0.10/32", "psk": "pskA"},        # תקין
        "B": {"ip": "10.8.0.99/32", "psk": "pskB"},        # כתובת שגויה
        "X": {"ip": "10.8.0.50/32", "psk": ""},            # לא במסד
    })
    desired = {"A": ("pskA", "10.8.0.10"), "B": ("pskB", "10.8.0.11"),
               "C": ("pskC", "10.8.0.12"), "D": ("", "10.8.0.13")}
    added, removed = wg.reconcile(desired)
    assert (added, removed) == (3, 1)
    s = json.loads(state.read_text())
    assert s == {"A": {"ip": "10.8.0.10/32", "psk": "pskA"}, "B": {"ip": "10.8.0.11/32", "psk": "pskB"},
                 "C": {"ip": "10.8.0.12/32", "psk": "pskC"}, "D": {"ip": "10.8.0.13/32", "psk": ""}}
    calls = log.read_text().splitlines()
    assert sum(c.startswith("addconf") for c in calls) == 1         # כל החדשים בפקודה אחת
    assert not any("pskC" in c for c in calls)                       # מפתח משותף לא בשורת הפקודה
    log.write_text("")
    assert wg.reconcile(desired) == (0, 0)
    assert [c.split()[0] for c in log.read_text().splitlines()] == ["show", "show"]


def test_metrics_registry_renders_prometheus_text():
    r = metrics.Registry("t")
    c = r.counter("hits_total", "hits")
    c.inc(route="/a")
    c.inc(2, route="/a")
    h = r.histogram("lat_seconds", "lat", buckets=(0.1, 1.0))
    h.observe(0.05)
    h.observe(0.5)
    h.observe(5)
    text = r.render()
    assert 't_hits_total{route="/a"} 3' in text
    assert 't_lat_seconds_bucket{le="0.1"} 1' in text and 't_lat_seconds_bucket{le="+Inf"} 3' in text
    assert "t_lat_seconds_count 3" in text
    snap = r.snapshot()
    assert metrics.render_snapshot(snap, "x").count('source="x"') == len(snap)


def test_device_last_seen_is_written_at_most_once_a_minute(env, monkeypatch):
    from helpers import wg_key

    svc, client = env
    code = svc.create_code("t", 1, 1, 1)["code"]
    r = client.post("/v1/enroll", json={"code": code, "public_key": wg_key(7), "name": "x"})
    token = r.json()["token"]
    h = {"Authorization": f"Bearer {token}"}
    writes = []
    import filterphone.api.service as S

    now = [1_800_000_000]
    monkeypatch.setattr(S.db, "now", lambda: now[0])
    orig = svc.db

    def counting_db():
        cm = orig()

        class Wrap:
            def __enter__(self):
                c = cm.__enter__()
                c.set_trace_callback(lambda sql: writes.append(sql) if sql.startswith("UPDATE devices SET last_seen") else None)
                return c

            def __exit__(self, *a):
                return cm.__exit__(*a)

        return Wrap()

    monkeypatch.setattr(svc, "db", counting_db)
    for _ in range(5):
        assert client.get("/v1/device", headers=h).status_code == 200
    now[0] += 61
    client.get("/v1/device", headers=h)
    assert len(writes) == 2
