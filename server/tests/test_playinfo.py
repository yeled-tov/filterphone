"""טאב העדכונים: פענוח עמוד Play, השוואת גרסאות, מטמון, ותקציב זמן."""
import json
import threading

import fakeredis

from filterphone.api import playinfo as P


def play_page(version: str | None, updated: int | None) -> str:
    """עמוד מינימלי באותו מבנה כמו ב-Play (ds:5 → [1][2][140] גרסה, [1][2][145] תאריך)."""
    d2 = [None] * 146
    d2[140] = [[[version]]] if version else [[None, []]]
    d2[145] = [["Oct 13, 2026", [updated, 0]]] if updated else None
    data = [None, [None, None, d2]]
    return ("<html><script>AF_initDataCallback({key: 'ds:5', hash: '7', data:"
            + json.dumps(data) + ", sideChannel: {}});</script></html>")


def test_parse_page_reads_version_and_update_date():
    assert P.parse_page(play_page("6.18", 1790863485)) == {"version": "6.18", "updated": 1790863485}
    assert P.parse_page(play_page(None, 1791906795)) == {"version": None, "updated": 1791906795}
    assert P.parse_page("<html>nothing</html>") == {"version": None, "updated": None}


def test_status_compares_versions_or_dates():
    assert P.status({"version_name": "6.17.2"}, {"version": "6.18"}) == "update"
    assert P.status({"version_name": "6.18"}, {"version": "6.18"}) == "current"
    assert P.status({"version_name": "2.26.34.81-beta"}, {"version": "2.26.34.81"}) == "current"
    day = 86400
    # "משתנה לפי מכשיר": לפי תאריכים — Play עודכנה הרבה אחרי הטלפון → ייתכן עדכון
    assert P.status({"version_name": "x", "updated_ms": 1_000 * 1_700_000_000},
                    {"version": None, "updated": 1_700_000_000 + 5 * day}) == "maybe"
    assert P.status({"updated_ms": 1_000 * 1_700_000_000},
                    {"version": None, "updated": 1_700_000_000 + day}) == "current"     # הדרגתי — עוד לא
    assert P.status({"version_name": "1.0"}, None) == "unknown"                         # לא ב-Play


def test_lookup_caches_results_and_missing_apps():
    r = fakeredis.FakeRedis(decode_responses=True)
    calls = []

    def fetch(pkg):
        calls.append(pkg)
        return None if pkg == "com.oem.thing" else {"version": "2.0", "updated": 1}
    pi = P.PlayInfo(r, fetch)
    got = pi.check([{"package": "com.waze", "version_name": "1.0"}, {"package": "com.oem.thing"},
                    {"package": "not a package"}])
    assert got["com.waze"]["status"] == "update" and got["com.waze"]["play_version"] == "2.0"
    assert got["com.oem.thing"]["status"] == "unknown"
    assert "not a package" not in got
    pi.check([{"package": "com.waze"}, {"package": "com.oem.thing"}])
    assert sorted(calls) == ["com.oem.thing", "com.waze"]                # בפעם השנייה — מהמטמון
    assert 0 < r.ttl("fp:playinfo:com.waze") <= P.CACHE_TTL_S
    assert r.ttl("fp:playinfo:com.oem.thing") > P.CACHE_TTL_S              # "לא ב-Play" — שבוע


def test_lookup_respects_the_time_budget_and_does_not_cache_errors():
    r = fakeredis.FakeRedis(decode_responses=True)
    release = threading.Event()

    def fetch(pkg):
        if pkg == "com.slow":
            release.wait(5)
            return {"version": "1", "updated": 1}
        if pkg == "com.broken":
            raise OSError("network")
        return {"version": "1", "updated": 1}
    got = P.PlayInfo(r, fetch).check([{"package": "com.fast"}, {"package": "com.slow"}, {"package": "com.broken"}],
                                     budget_s=0.5)
    release.set()
    assert got["com.fast"]["play_version"] == "1"                          # נבדקה בזמן
    assert got["com.slow"]["status"] == "pending"                          # לא הספקנו — בפעם הבאה
    assert got["com.broken"]["status"] == "pending" and r.get("fp:playinfo:com.broken") is None


def test_device_endpoint(env, monkeypatch):
    from test_compliance import enroll
    svc, client = env
    _, dh, _ = enroll(svc, client)
    client.app.state.playinfo.fetch = lambda pkg: {"version": "3.0", "updated": 1}
    r = client.post("/v1/device/app-updates", headers=dh, json={"apps": [
        {"package": "com.whatsapp", "version_name": "2.9", "updated_ms": 1}]})
    assert r.status_code == 200, r.text
    assert r.json()["apps"]["com.whatsapp"] == {"status": "update", "play_version": "3.0", "play_updated": 1}
    assert client.post("/v1/device/app-updates", json={"apps": []}).status_code == 401
