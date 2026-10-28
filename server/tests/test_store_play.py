"""הדשבורד: חיפוש ב-Google Play, פרטים להוספה לחנות, ותמונות מ-Play רק אחרי מסנן התמונות."""
import pytest
from helpers import admin_headers
from test_playinfo import details_page

from filterphone.api import playinfo as P
from filterphone.api import service as S


@pytest.fixture
def play(env):
    svc, client = env
    cat = P.PlayCatalog(svc.redis, details=lambda pkg: P.parse_details(details_page()) if pkg != "com.none" else None,
                        search=lambda q: ["com.waze", "com.newapp.x"])
    client.app.state.playcatalog = cat
    return svc, client


def test_search_marks_what_is_already_in_the_store_and_details_fill_the_form(play):
    svc, client = play
    h = admin_headers(client)
    r = client.get("/v1/admin/play/search", params={"q": "ניווט"}, headers=h)
    assert r.status_code == 200
    res = {x["package"]: x for x in r.json()["results"]}
    assert res["com.waze"]["in_store"] is True and res["com.newapp.x"]["in_store"] is False
    assert res["com.newapp.x"]["screenshots"] and res["com.newapp.x"]["icon"].startswith("https://")
    d = client.get("/v1/admin/play/app/com.newapp.x", headers=h).json()
    assert d["play_url"] == "https://play.google.com/store/apps/details?id=com.newapp.x" and d["description"]
    # הוספה לחנות עם מה שנמשך — נשמר כמו הוספה ידנית
    add = client.post("/v1/admin/apps", headers=h, json={
        "package": "com.newapp.x", "label": d["title"], "category": "תחבורה", "note": d["summary"],
        "description": d["description"], "icon_url": d["icon"], "screenshots": d["screenshots"], "play_url": d["play_url"]})
    assert add.status_code == 201
    assert client.get("/v1/admin/play/app/com.none", headers=h).status_code == 404
    assert client.get("/v1/admin/play/search", params={"q": "x"}, headers=h).status_code == 400
    assert client.get("/v1/admin/play/search", params={"q": "ניווט"}).status_code == 401


def test_play_images_only_from_play_and_only_after_the_filter(play, monkeypatch):
    svc, client = play
    h = admin_headers(client)
    monkeypatch.setattr(S, "fetch_image", lambda url, n: (b"ORIGINAL", "image/png"))
    monkeypatch.setattr(S.protocol, "call_sync", lambda sock, header, data, timeout:
                        ({"action": "block", "ct": "image/png"}, b"PLACEHOLDER"))
    r = client.get("/v1/admin/play/image", params={"u": "https://play-lh.googleusercontent.com/s0"}, headers=h)
    assert r.status_code == 200 and r.content == b"PLACEHOLDER"                # מה שהמנוע החזיר — לא המקור
    assert client.get("/v1/admin/play/image", params={"u": "https://evil.example.com/a.png"}, headers=h).status_code == 400
    assert client.get("/v1/admin/play/image", params={"u": "https://play-lh.googleusercontent.com.evil.com/a"},
                      headers=h).status_code == 400
