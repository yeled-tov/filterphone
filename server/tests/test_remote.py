"""Modal (GPU חיצוני): אותה החלטת סינון כמו מקומית, ונפילה למקומי בכל כשל."""
import asyncio
import http.client
import http.server
import json
import os
import threading
import time
import urllib.error
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from helpers import admin_headers

from filterphone.api import gpu
from filterphone.engine import remote as remote_cfg
from filterphone.engine.batcher import Batcher
from filterphone.engine.cache import Entry
from filterphone.engine.policy import Thresholds
from filterphone.engine.remote import RemoteEngine, make_payload
from filterphone.engine.segment import Person, SegStats
from filterphone.engine.service import Engine
from filterphone.settings import Settings

HEADS = "test-heads"

# ציונים לפי (person, female, immodest, nsfw); גלאי וסגמנטציה קבועים לכל "תמונה"
SCORES = {
    b"clean": [0.1, 0.05, 0.05, 0.01],
    b"female": [0.9, 0.8, 0.1, 0.02],
    b"borderline": [0.9, 0.3, 0.1, 0.02],
    b"nsfw": [0.5, 0.3, 0.5, 0.3],
    b"hidden": [0.1, 0.05, 0.05, 0.01],     # הגלאי היה מוצא עירום — אבל הציונים לא מבקשים גלאי
    b"immodest": [0.9, 0.6, 0.6, 0.05],
}
DETS = {
    b"borderline": [["FACE_FEMALE", 0.8, 10, 10, 20, 20]],
    b"nsfw": [["FEMALE_BREAST_EXPOSED", 0.9, 0, 0, 10, 10]],
    b"hidden": [["FEMALE_BREAST_EXPOSED", 0.9, 0, 0, 10, 10]],
}
SEG = {b"immodest": SegStats(0.3, 0.2, 1, 0.0, (Person((0, 0, 50, 80), 0.9, True),))}


class MemCache:
    def __init__(self):
        self.d, self.labels = {}, {}

    async def get(self, k):
        return self.d.get(k)

    async def put(self, k, e):
        self.d[k] = e

    async def get_labels(self, k):
        return self.labels.get(k)

    async def put_labels(self, k, png):
        self.labels[k] = png

    async def close(self):
        pass


class ModelEngine(Engine):
    """מנוע עם "מודלים" קבועים לבדיקה — כל השאר (מדיניות, שלבים, קאש, ציור) הוא הקוד האמיתי."""

    def __init__(self, remote=None, cfg=None):
        self.batcher = Batcher(lambda b: b, None, 8, 1)
        self._init_runtime(cfg or Settings())
        self.heads = SimpleNamespace(version=HEADS)
        self.thresholds = Thresholds()
        self.segmenter = object()
        self.cache = MemCache()
        self.inflight = {}
        self.stats = Counter()
        self.decode_pool = ThreadPoolExecutor(1)
        self.runs = Counter()
        self.remote = remote

    async def _analyze(self, data):
        self.runs["analyze"] += 1
        return Entry(SCORES[data], wh=[100, 80]), None

    async def _add_detections(self, work, entry):
        self.runs["detector"] += 1
        entry = entry.with_(dets=DETS.get(work.data, []))
        await self.cache.put(work.key, entry)
        return entry

    async def _add_segmentation(self, work, entry, detail=True):
        self.runs["segment"] += 1
        st = SEG.get(work.data, SegStats(0.0, 0.0))
        entry = entry.with_(seg=st.dump())
        await self.cache.put(work.key, entry)
        return entry


class FakeModal(RemoteEngine):
    """ה-HTTP מוחלף בהרצה של "השרת ב-Modal" — אותו analyze_resolved ואותו make_payload."""

    def __init__(self, server: ModelEngine, fail: Exception | None = None):
        super().__init__("https://x.modal.run", "wk", "ws", HEADS, timeout_s=1, cooldown_s=60)
        self.server, self.fail, self.calls = server, fail, 0

    def _post(self, data, level):
        self.calls += 1
        if self.fail:
            raise self.fail
        entry, labels = asyncio.run(self.server.analyze_resolved(data, level))
        return make_payload(entry, labels, HEADS)


@pytest.mark.parametrize("level", [1, 2, 3])
@pytest.mark.parametrize("image", list(SCORES))
async def test_modal_gives_the_same_decision_as_local(image, level):
    local = ModelEngine()
    remote = ModelEngine(FakeModal(ModelEngine()))
    v_local = await local.classify(image, level)
    v_remote = await remote.classify(image, level)
    assert (v_remote.action, v_remote.reason) == (v_local.action, v_local.reason)
    assert remote.runs["analyze"] == 0          # הכול רץ "ב-Modal" — שום מודל מקומי
    assert remote.stats["remote_runs"] == 1
    # ובקאש נשמר בדיוק מה שהמנוע המקומי היה שומר (כולל: בלי גלאי כשלא ביקשו)
    cached_local = await local.cache.get(next(iter(local.cache.d)))
    cached_remote = await remote.cache.get(next(iter(remote.cache.d)))
    assert cached_remote.dump() == cached_local.dump()


async def test_modal_does_not_run_detector_the_policy_did_not_ask_for():
    remote = ModelEngine(FakeModal(ModelEngine()))
    v = await remote.classify(b"hidden", 3)
    assert v.action == "pass"
    assert remote.remote.server.runs["detector"] == 0


async def test_modal_failure_falls_back_to_local_and_cools_down():
    modal = FakeModal(ModelEngine(), fail=urllib.error.HTTPError("u", 429, "quota", {}, None))
    e = ModelEngine(modal)
    v = await e.classify(b"female", 1)
    assert v.action == "block" and e.runs["analyze"] == 1      # מקומי
    assert not modal.enabled and modal.calls == 1
    await e.classify(b"clean", 1)
    assert modal.calls == 1                     # ב-cooldown לא פונים שוב ל-Modal
    assert e.runs["analyze"] == 2


class SlowModal(FakeModal):
    """Modal שעונה רק אחרי [delay] שניות — כמו בהפעלה קרה."""

    def __init__(self, server: ModelEngine, delay: float):
        super().__init__(server)
        self.delay, self.started = delay, 0

    def _post(self, data, level):
        self.started += 1
        time.sleep(self.delay)
        return super()._post(data, level)


async def test_slow_modal_does_not_hold_images_back():
    modal = SlowModal(ModelEngine(), delay=0.4)
    e = ModelEngine(modal, Settings(modal_hedge_s=0.05))
    t0 = time.monotonic()
    v = await e.classify(b"female", 1)
    assert v.action == "block" and e.runs["analyze"] == 1         # מקומי — בלי לחכות ל-Modal
    assert time.monotonic() - t0 < 0.3
    await e.classify(b"clean", 1)
    assert modal.started == 1               # הבקשה האיטית עוד תלויה — לא שולחים אחריה עוד
    assert e.runs["analyze"] == 2
    await asyncio.sleep(0.5)                # Modal "התעורר"
    modal.delay = 0
    await e.classify(b"borderline", 1)
    assert modal.started == 2 and e.runs["analyze"] == 2


class _KeepAliveHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    peers: list = []
    close_next = False

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        type(self).peers.append(self.client_address)
        body = json.dumps(make_payload(Entry([0.1, 0.0, 0.0, 0.0], wh=[9, 9]), None, HEADS)).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        if type(self).close_next:               # השרת סוגר חיבור פתוח (כמו keep-alive שפג)
            type(self).close_next = False
            self.close_connection = True

    def log_message(self, *a):
        pass


def test_modal_connection_is_reused_and_reopened_when_closed(monkeypatch):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _KeepAliveHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    monkeypatch.setattr(remote_cfg.http.client, "HTTPSConnection",
                        lambda host, port, timeout, context: http.client.HTTPConnection(host, port, timeout=timeout))
    try:
        r = RemoteEngine(f"https://127.0.0.1:{port}/analyze", "k", "s", HEADS)
        for _ in range(3):
            assert r._parse(r._post(b"img", 1))[0].scores == [0.1, 0.0, 0.0, 0.0]
        assert len(set(_KeepAliveHandler.peers)) == 1        # שלוש תמונות — חיבור אחד
        _KeepAliveHandler.close_next = True
        r._post(b"img", 1)
        assert r._parse(r._post(b"img", 1))[0].wh == [9, 9]   # חיבור חדש, בלי שגיאה
        assert len(set(_KeepAliveHandler.peers)) == 2
    finally:
        srv.shutdown()


def test_parse_validates_remote_answers():
    r = RemoteEngine("https://x", "k", "s", HEADS)
    entry = Entry([0.1, 0.2, 0.3, 0.4], dets=[["FACE_FEMALE", 0.5, 1, 2, 3, 4]], wh=[10, 20])
    got, labels = r._parse(make_payload(entry, b"\x89PNG", HEADS))
    assert got.dump() == entry.dump() and labels == b"\x89PNG"
    bad = [
        make_payload(entry, None, "other-heads"),
        {"entry": Entry([0.1, 0.2]).dump(), "heads": HEADS},
        {"entry": '{"s":["a","b","c","d"]}', "heads": HEADS},
        {"heads": HEADS},
    ]
    for payload in bad:
        with pytest.raises(ValueError):
            r._parse(payload)


def test_modal_is_off_unless_fully_configured():
    assert RemoteEngine.from_settings(Settings(), HEADS) is None
    assert RemoteEngine.from_settings(Settings(modal_url="https://x", modal_key="k"), HEADS) is None
    r = RemoteEngine.from_settings(Settings(modal_url="https://x/", modal_key="k", modal_secret="s"), HEADS)
    assert r is not None and r.url == "https://x" and r.enabled


async def test_overflow_mode_uses_modal_only_when_the_server_is_busy():
    modal = FakeModal(ModelEngine())
    modal.mode = "overflow"
    e = ModelEngine(modal)
    await e.classify(b"female", 1)
    assert modal.calls == 0 and e.runs["analyze"] == 1          # שרת פנוי — מקומי
    e.active = e.cfg.engine_max_inflight                         # עמוס
    await e.classify(b"clean", 1)
    assert modal.calls == 1


def test_dashboard_config_is_picked_up_without_restart(tmp_path):
    cfg = Settings(data_dir=str(tmp_path))
    e = ModelEngine(cfg=cfg)
    e._remote_check_at = 0.0
    e._reload_remote()
    assert e.remote is None                                      # אין הגדרות — מקומי
    path = remote_cfg.config_path(cfg)
    remote_cfg.write_config(path, {"url": "https://a.modal.run", "key": "wk-1", "secret": "ws-1", "mode": "always"})
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"
    e._remote_check_at = 0.0
    e._reload_remote()
    assert e.remote is not None and e.remote.url == "https://a.modal.run"
    remote_cfg.write_config(path, {"url": "https://a.modal.run", "key": "wk-1", "secret": "ws-1", "mode": "off"})
    os.utime(path, (1, 1))                                       # mtime שונה בוודאות
    e._remote_check_at = 0.0
    e._reload_remote()
    assert e.remote is None


def test_env_settings_win_over_the_dashboard():
    cfg = Settings(modal_url="https://env.modal.run", modal_key="k", modal_secret="s")
    r = RemoteEngine.from_settings(cfg, HEADS, {"url": "https://dash.modal.run", "key": "k2", "secret": "s2"})
    assert r.url == "https://env.modal.run"


def test_merge_config_validates_and_keeps_secret():
    cur = {"url": "https://a.modal.run", "key": "wk-1", "secret": "ws-1", "mode": "always"}
    assert remote_cfg.merge_config(cur, {"url": "https://b.modal.run/", "secret": ""})["secret"] == "ws-1"
    assert remote_cfg.merge_config(cur, {"url": "https://b.modal.run/"})["url"] == "https://b.modal.run"
    assert remote_cfg.merge_config(cur, {"clear": True}) == {}
    for bad in ({"url": "http://plain.example"}, {"url": "javascript:alert(1)"}, {"key": "has space"},
                {"mode": "sometimes"}):
        with pytest.raises(ValueError):
            remote_cfg.merge_config(cur, bad)


def test_admin_gpu_settings_save_and_test(env, monkeypatch):
    svc, client = env
    h = admin_headers(client)
    st = client.get("/v1/admin/gpu", headers=h).json()
    assert st["configured"] is False and st["source"] == "none"
    r = client.put("/v1/admin/gpu", headers=h, json={"url": "https://me--filterphone-engine.modal.run",
                                                     "key": "wk-abc", "secret": "ws-secret", "mode": "always"})
    assert r.status_code == 200
    st = r.json()
    assert st["active"] and st["secret_set"] and "secret" not in st     # הסוד לא חוזר לדפדפן
    saved = json.load(open(remote_cfg.config_path(svc.cfg)))
    assert saved["secret"] == "ws-secret"
    assert client.put("/v1/admin/gpu", headers=h, json={"url": "ftp://x"}).status_code == 400

    heads = gpu.heads_version(svc.cfg.heads_file)
    monkeypatch.setattr(RemoteEngine, "_post", lambda self, data, level: make_payload(
        Entry([0.1, 0.0, 0.0, 0.0], wh=[96, 96]), None, heads))
    assert client.post("/v1/admin/gpu/test", headers=h).json()["ok"] is True

    def unauthorized(self, data, level):
        raise urllib.error.HTTPError("u", 401, "no", {}, None)
    monkeypatch.setattr(RemoteEngine, "_post", unauthorized)
    res = client.post("/v1/admin/gpu/test", headers=h).json()
    assert res["ok"] is False and "טוקן" in res["error"]

    assert client.put("/v1/admin/gpu", headers=h, json={"clear": True}).json()["configured"] is False
    assert client.get("/v1/admin/gpu").status_code == 401


# ---------------------------------------------------------------- תמונות קטנות (בלי Modal)

async def test_old_cached_tiny_entry_is_checked_again():
    """רשומה שנשמרה כ"קטנה מדי" כשהסף היה גבוה יותר — נבדקת עכשיו במקום לעבור."""
    from filterphone.engine.cache import image_key
    e = ModelEngine()
    await e.cache.put(image_key(b"female"), Entry(None, note="tiny", wh=[30, 30]))
    v = await e.classify(b"female", 1)
    assert e.runs["analyze"] == 1 and v.action == "block"


async def test_tiny_image_skips_segmentation_and_counts_as_no_person():
    from filterphone.engine.service import Engine, _Work
    e = ModelEngine()
    entry = Entry([0.9, 0.3, 0.1, 0.0], wh=[48, 40])
    called = []

    async def fake_segment(*a, **k):
        called.append(1)
    e._segment = fake_segment
    out = await Engine._add_segmentation(e, _Work("k", b""), entry)
    assert called == [] and SegStats.load(out.seg) == SegStats(0.0, 0.0)


async def test_level1_coarse_segmentation_is_redone_in_full_for_level2():
    """רמה 1 מסתפקת במעבר ראשון; כשאותה תמונה מגיעה לרמה 2 — הסגמנטציה מחושבת שוב במלואה."""
    from filterphone.engine.cache import image_key
    from filterphone.engine.segment import COARSE
    e = ModelEngine()
    coarse = SegStats(0.3, 0.2, COARSE, 0.0, (Person((0, 0, 50, 80), 0.9, True),))
    await e.cache.put(image_key(b"immodest"), Entry(SCORES[b"immodest"], dets=[], seg=coarse.dump(), wh=[100, 80]))
    v1 = await e.classify(b"immodest", 1)
    assert v1.action == "block" and e.runs["segment"] == 0       # לרמה 1 — מספיק
    await e.classify(b"immodest", 2)
    assert e.runs["segment"] == 1                                # לרמה 2 — מחושב מחדש
