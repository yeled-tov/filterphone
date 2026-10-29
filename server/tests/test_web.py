"""רמות גלישה בכרום: הרמות והקבוצות, סינון דפי תוצאות, האכיפה בפרוקסי, הבוט ובקשות הבדיקה."""
import json

import fakeredis
import pytest
from helpers import admin_headers, wg_key
from mitmproxy import http
from test_addon import IP, mkflow

from filterphone import websites as W
from filterphone.api.fetch import FetchError
from filterphone.api.webcheck import PageInfo, WebDesk, classify_page, parse_page
from filterphone.proxy import addon as A
from filterphone.proxy import serp
from filterphone.proxy.policy import DevicePolicy, compile_hosts
from filterphone.proxy.web import CHECK_KEY, REVIEWS_KEY, WebState


def web(level, extras=(), without=(), allow=()):
    return W.WebPolicy(level, frozenset(W.resolve_groups(level, extras, without)), tuple(allow))


# ------------------------------------------------------------------ רמות וקבוצות

def test_levels_open_progressively_more():
    ess, clean, shop = (set(W.resolve_groups(x)) for x in ("essential", "clean", "shopping"))
    assert {"essential", "payments", "accounts"} <= ess
    assert "search" not in ess and "auto_clean" not in ess
    assert ess < clean < shop
    assert "shopping_general" in shop and "shopping_general" not in clean
    assert not {"news_haredi", "news_general"} & shop              # חדשות — רק כתוספת
    assert W.resolve_groups("") == ()                              # פתוח — בלי רשימה לבנה
    # תוספת / הורדה; תשתית (תשלום, התחברות) לא יורדת
    g = set(W.resolve_groups("clean", ["news_haredi"], ["auto_clean", "payments"]))
    assert "news_haredi" in g and "auto_clean" not in g and "payments" in g


def test_essential_opens_only_essential_sites():
    p = web("essential")
    assert p.decide("www.gov.il", None) == "allow"
    assert p.decide("myaccount.leumi.co.il", None) == "allow"
    assert p.decide("secure.cardcom.solutions", None) == "allow"     # דף תשלום
    assert p.decide("www.google.com", None) == "block"               # בלי חיפוש
    assert p.decide("example.org", None) == "block"                  # לא בודקים — חוסמים
    assert p.decide("example.org", "auto_clean") == "block"


def test_clean_level_checks_unknown_sites_and_follows_verdicts():
    p = web("clean")
    assert p.decide("www.sefaria.org", None) == "allow"
    assert p.decide("www.ksp.co.il", None) == "allow"                # קניות נקיות
    assert p.decide("he.aliexpress.com", None) == "block"            # קניות כלליות — לא ברמה הזו
    assert p.decide("www.ynet.co.il", None) == "block"
    assert p.decide("www.kikar.co.il", None) == "block"              # חדשות חרדיות — רק כתוספת
    assert web("clean", ["news_haredi"]).decide("www.kikar.co.il", None) == "allow"
    assert p.decide("wedding-rsvp.example", None) == "check"         # לא מוכר — הבוט בודק
    assert p.decide("wedding-rsvp.example", "auto_clean") == "allow"
    assert p.decide("shop.wedding-rsvp.example", "images") == "block"
    assert web("clean", without=["auto_clean"]).decide("new.example", None) == "block"
    assert web("clean", allow=["new.example"]).decide("www.new.example", None) == "allow"
    assert web("shopping").decide("he.aliexpress.com", None) == "allow"


def test_google_subsites_are_not_all_search():
    p = web("clean")
    assert p.decide("www.google.com", None) == "allow"
    assert p.decide("accounts.google.com", None) == "allow"
    assert p.decide("docs.google.com", None) == "allow"               # כלים
    assert p.decide("news.google.com", None) == "block"               # חדשות
    assert W.group_of("news.google.com") == "news_general"
    assert p.decide("sites.google.com", None) == "block"              # אתרים של כולם — לא אוטומטי
    assert W.no_auto("foo.blogspot.com")


def test_open_level_allows_everything():
    assert web("").decide("anything.example", None) == "allow"
    assert not W.WebPolicy.from_json({}).active
    p = W.WebPolicy.from_json({"level": "clean", "groups": ["torah", "bogus"], "allow": ["www.x.co.il"]})
    assert p.groups == {"torah"} and p.allow == ("x.co.il",)


def test_every_listed_domain_is_a_plain_host():
    for g in W.GROUPS:
        for d in g.domains:
            assert d == d.lower().strip() and "/" not in d and " " not in d and "." in d, d


# ------------------------------------------------------------------ דף תוצאות

def test_search_query_parsing():
    assert serp.search_query("www.google.com", "/search?q=hello&hl=he") == ("google", "hello", False)
    assert serp.search_query("www.google.co.il", "/complete/search?q=he&client=chrome") == ("google", "he", True)
    assert serp.search_query("www.bing.com", "/search?q=a+b") == ("bing", "a b", False)
    assert serp.search_query("duckduckgo.com", "/?q=x") == ("duckduckgo", "x", False)
    assert serp.search_query("www.youtube.com", "/results?search_query=y") == ("youtube", "y", False)
    assert serp.search_query("www.google.com", "/maps?q=x") is None
    assert serp.search_query("example.com", "/search?q=x") is None


def test_term_matcher_words_and_prefixes():
    m = serp.TermMatcher(["sex", "פורנו", "色情"])
    assert m.matches("free SEX videos")
    assert not m.matches("Sussex university")                        # לא חלק ממילה
    assert m.matches("אתרי הפורנו")                                  # אות שימוש
    assert m.matches("色情片")
    assert not m.matches("weather tomorrow")
    assert m.count("sex sex") == 2


def test_link_host_resolves_redirects():
    assert serp.link_host("https://www.ksp.co.il/web/") == "www.ksp.co.il"
    assert serp.link_host("/url?q=https://www.instagram.com/x&sa=U") == "www.instagram.com"
    assert serp.link_host("/search?q=more") is None
    assert serp.link_host("https://maps.google.com/x") is None
    import base64
    u = "a1" + base64.urlsafe_b64encode(b"https://www.facebook.com/p").decode().rstrip("=")
    assert serp.link_host(f"https://www.bing.com/ck/a?!&&p=1&u={u}&ntb=1") == "www.facebook.com"


GOOGLE_HTML = """<html><head><title>q</title></head><body>
<form action="/search"><input name="q" value="q"><a href="/search?q=q&tbm=isch">תמונות</a></form>
<div id="rso">
  <div class="MjjYud"><div class="g"><a href="https://www.instagram.com/someone/"><h3>Instagram</h3></a>
    <div><a href="/search?q=related:instagram.com">דומים</a></div></div></div>
  <div class="MjjYud"><div class="g"><a href="https://www.ksp.co.il/web/item/1"><h3>KSP</h3></a></div></div>
  <div class="MjjYud"><div class="g"><a href="/url?q=https://m.facebook.com/page&amp;sa=U"><h3>FB</h3></a></div></div>
  <div class="MjjYud"><div>Top stories
     <a href="https://www.ynet.co.il/a">ynet</a> <a href="https://www.kikar.co.il/b">kikar</a></div></div>
</div><footer><a href="https://policies.google.com/">פרטיות</a></footer></body></html>"""


def test_filter_results_removes_only_hidden_results():
    hidden = {"www.instagram.com", "m.facebook.com", "www.ynet.co.il"}
    out, removed = serp.filter_results(GOOGLE_HTML, lambda h: h in hidden)
    assert removed == 3
    assert "instagram" not in out and "facebook" not in out and "ynet" not in out
    assert "www.ksp.co.il" in out and "kikar" in out                  # בקרוסלה — רק הקישור החסום נמחק
    assert '<input name="q"' in out and "תמונות" in out and "פרטיות" in out
    assert "הוסתרו 3 תוצאות" in out
    same, n = serp.filter_results(GOOGLE_HTML, lambda h: False)
    assert n == 0 and same == GOOGLE_HTML


def test_filter_results_never_removes_most_of_the_page():
    # מבנה לא צפוי: כל הדף בתוך "בלוק" אחד עם קישור חסום — נמחק רק הקישור
    html = '<div class="g">' + "<p>תוכן חשוב</p>" * 400 + '<a href="https://bad.example/">bad</a></div>'
    out, removed = serp.filter_results(html, lambda h: h == "bad.example", notice=False)
    assert removed == 1 and "bad.example" not in out and out.count("תוכן חשוב") == 400


def test_filter_results_survives_broken_html():
    html = '<div class="g"><a href="https://bad.example/">x<div><p>unclosed <b>tags</div></div><a href="https://ok.example/">ok'
    out, removed = serp.filter_results(html, lambda h: h == "bad.example", notice=False)
    assert removed == 1 and "bad.example" not in out and "ok.example" in out


# ------------------------------------------------------------------ הפרוקסי

@pytest.fixture
def fp():
    a = A.FilterPhone()
    a.redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    a.web = WebState(a.redis)
    a.policies.set(IP, DevicePolicy(level=1, web=web("clean")))
    return a


def nav(url):
    return mkflow(url, {"sec-fetch-dest": "document", "sec-fetch-mode": "navigate", "accept": "text/html"})


async def drain(a):
    for t in list(a._background):
        await t


async def test_unknown_site_shows_checking_page_and_is_queued(fp):
    f = nav("https://rsvp-example.co.il/event/1")
    fp.requestheaders(f)
    assert f.response.status_code == 200 and "בודקים את האתר" in f.response.text
    await drain(fp)
    assert await fp.redis.lrange(CHECK_KEY, 0, -1) == ["rsvp-example.co.il"]
    fp.requestheaders(nav("https://rsvp-example.co.il/"))             # רענון — לא נדחף שוב
    await drain(fp)
    assert await fp.redis.llen(CHECK_KEY) == 1
    # הבוט סיים: האתר נקי → נפתח
    fp.web.apply("rsvp-example.co.il", "auto_clean")
    f2 = nav("https://rsvp-example.co.il/")
    fp.requestheaders(f2)
    assert f2.response is None


async def test_site_with_wrong_verdict_gets_block_page_with_review(fp):
    fp.web.apply("fashion.example", "images")
    f = nav("https://www.fashion.example/sale")
    fp.requestheaders(f)
    body = f.response.text
    assert "האתר חסום" in body and "יש באתר תמונות של נשים" in body and "נקי לגמרי" in body
    assert 'action="/__filterphone__/review"' in body
    # שליחה לבדיקה — נרשם, ולא יוצא לאתר
    r = mkflow("https://www.fashion.example/__filterphone__/review", {"content-type": "application/x-www-form-urlencoded"})
    r.request.method = "POST"
    r.request.text = "url=https%3A%2F%2Fwww.fashion.example%2Fsale&note=%D7%A6%D7%A8%D7%99%D7%9A"
    fp.requestheaders(r)
    assert r.response is None
    await fp.request(r)
    assert "נשלח לבדיקה" in r.response.text
    (_, ev), = await fp.redis.xrange(REVIEWS_KEY)
    assert ev["ip"] == IP and ev["host"] == "fashion.example" and ev["url"].endswith("/sale") and ev["note"] == "צריך"


async def test_subresources_of_unknown_hosts_load(fp):
    f = mkflow("https://cdn.unknown.example/app.js", {"sec-fetch-dest": "script"})
    fp.requestheaders(f)
    assert f.response is None


async def test_slow_check_ends_in_block_page(fp, monkeypatch):
    clock = [100.0]
    fp.web._clock = lambda: clock[0]
    fp.requestheaders(nav("https://slow.example/"))
    clock[0] += 60
    f = nav("https://slow.example/")
    fp.requestheaders(f)
    assert "האתר חסום" in f.response.text and "לא הצלחנו לבדוק" in f.response.text


async def test_explicit_search_returns_no_results(fp):
    fp.policies.set(IP, DevicePolicy(level=1))                        # גם בלי רמת גלישה
    f = nav("https://www.google.com/search?q=free+porn+videos")
    fp.requestheaders(f)
    assert "לא נמצאו תוצאות" in f.response.text
    s = mkflow("https://www.google.com/complete/search?q=porn&client=chrome-omni", {"sec-fetch-dest": "empty"})
    fp.requestheaders(s)
    assert s.response.status_code == 204
    ok = nav("https://www.google.com/search?q=weather+jerusalem")
    fp.requestheaders(ok)
    assert ok.response is None


async def test_google_tabs_closed_in_clean_level(fp):
    for q in ("tbm=isch", "udm=2", "tbm=nws", "udm=7"):
        f = nav(f"https://www.google.com/search?q=cats&{q}")
        fp.requestheaders(f)
        assert f.response is not None and "לא נמצאו תוצאות" in f.response.text, q
    f = nav("https://www.google.com/search?q=cats")
    fp.requestheaders(f)
    assert f.response is None


async def test_adult_sites_blocked_for_everyone(fp):
    fp.policies.set(IP, DevicePolicy(level=3))
    fp.web.adult_hosts = frozenset({"bad-adult.example"})
    f = nav("https://www.bad-adult.example/")
    fp.requestheaders(f)
    assert "למבוגרים" in f.response.text


async def test_serp_hides_blocked_networks_and_out_of_level_sites(fp):
    fp.policies.set(IP, DevicePolicy(level=2, hosts=compile_hosts(["instagram.com", "facebook.com"]),
                                     web=web("clean")))
    fp.web.apply("ynet.co.il", "news")
    f = mkflow("https://www.google.com/search?q=x", {"sec-fetch-dest": "document"}, "text/html; charset=UTF-8",
               GOOGLE_HTML.encode())
    fp.responseheaders(f)
    assert f.metadata["fp_kind"] == "serp"
    await fp.response(f)
    out = f.response.text
    assert "instagram" not in out and "facebook" not in out and "www.ynet.co.il" not in out
    assert "www.ksp.co.il" in out
    # כיכר השבת — ברשימת חדשות חרדיות, לא פתוחה ברמה הזו → מוסתרת
    assert "kikar" not in out


async def test_learned_passthrough_does_not_open_sites_to_chrome(fp):
    from types import SimpleNamespace
    fp.hosts.learn(IP, "unknown-site.example", False, 9e18)
    hello = SimpleNamespace(sni="unknown-site.example", cipher_suites=[0x0A0A, 0x1301], extensions=[])
    data = SimpleNamespace(client_hello=hello, context=SimpleNamespace(client=SimpleNamespace(peername=(IP, 1))),
                           ignore_connection=False)
    fp.tls_clienthello(data)
    assert data.ignore_connection is False                            # כרום — נבדק (דף חסימה / בדיקה)
    app_hello = SimpleNamespace(sni="unknown-site.example", cipher_suites=[0x1301], extensions=[])
    data2 = SimpleNamespace(client_hello=app_hello, context=data.context, ignore_connection=False)
    fp.tls_clienthello(data2)
    assert data2.ignore_connection is True                            # האפליקציה — כרגיל


# ------------------------------------------------------------------ הבוט

def test_parse_page_collects_text_signals_and_images():
    html = """<html><head><title>חנות כלי בית</title><meta property="og:image" content="/og.jpg">
    <meta name="description" content="כלי מטבח"></head><body><script>var x="add to cart"</script>
    <img src="/img/pot.jpg" width="300"><img src="/logo.png"><img data-src="https://cdn.x.com/p/2.webp">
    <img src="/icons/i.svg"><img src="/tiny.jpg" width="20"><button>הוספה לסל</button></body></html>"""
    info = parse_page(html, "https://shop.example/", 12)
    assert info.title == "חנות כלי בית" and info.description == "כלי מטבח"
    assert info.images == ["https://shop.example/og.jpg", "https://shop.example/img/pot.jpg", "https://cdn.x.com/p/2.webp"]
    assert "add to cart" not in info.text and "הוספה לסל" in info.text


def test_classify_page_priorities():
    t = serp.TermMatcher(["porn"])
    assert classify_page(PageInfo(title="Free porn"), t, 0, 0)[0] == "adult"
    assert classify_page(PageInfo(title="חדשות היום"), t, 0, 0)[0] == "news"
    assert classify_page(PageInfo(text="הוסף לסל"), t, 0, 0)[0] == "shopping_clean"
    assert classify_page(PageInfo(text="הוסף לסל"), t, 2, 0)[0] == "shopping_general"
    assert classify_page(PageInfo(text="שלום"), t, 1, 0)[0] == "images"
    assert classify_page(PageInfo(text="שלום"), t, 0, 1)[0] == "adult"
    v, sig = classify_page(PageInfo(text="אישורי הגעה לחתונה"), t, 0, 0)
    assert v == "auto_clean" and sig["thin"] is True


class FakeSite:
    def __init__(self, pages, images):
        self.pages = pages
        self.images = images

    def page(self, url):
        host = url.split("/")[2]
        if host not in self.pages:
            raise FetchError("no")
        return self.pages[host], url

    def image(self, url):
        return self.images.get(url, b"x" * 5000), "image/jpeg"


def desk_for(svc, pages, images=None, women=(), broken=()):
    site = FakeSite(pages, images or {})

    def classify(data):
        if data in broken:
            return {"action": "block", "reason": "unverifiable"}
        return {"action": "block", "reason": "female 0.9"} if data in women else {"action": "pass"}
    return WebDesk(svc, classify=classify, page_fetcher=site.page, image_fetcher=site.image)


def test_undecodable_images_are_not_counted_as_women(env):
    """לוגו בפורמט שהמנוע לא מפענח — המנוע חוסם ('unverifiable'), אבל זו לא ראיה לאישה."""
    svc, _ = env
    logo, woman = b"L" * 5000, b"W" * 5000
    desk = desk_for(svc, {
        "tools.example": "<title>כלים</title><img src='/hero.avif'><img src='/b.jpg'>",
        "mixed.example": "<title>אתר</title><img src='/hero.avif'><img src='/w.jpg'>",
    }, {"https://tools.example/hero.avif": logo, "https://mixed.example/hero.avif": logo,
        "https://mixed.example/w.jpg": woman}, women=(woman,), broken=(logo,))
    v, d = desk.analyze("tools.example")
    assert v == "auto_clean" and d["images_checked"] == 1 and d["images_unverifiable"] == 1
    assert d["women_images"] == 0 and d["image_reasons"] == ["unverifiable"]
    v, d = desk.analyze("mixed.example")
    assert v == "images" and d["women_images"] == 1 and sorted(d["image_reasons"]) == ["female 0.9", "unverifiable"]


def test_bot_verdicts_are_saved_and_published(env):
    svc, _ = env
    woman = b"W" * 5000
    desk = desk_for(svc, {
        "rsvp.example": "<title>אישור הגעה</title><p>נשמח לראותכם</p><img src='/a.jpg'>",
        "fashion.example": "<title>אופנה</title><img src='/w.jpg'>",
    }, {"https://fashion.example/w.jpg": woman}, women=(woman,))
    assert desk.check("www.rsvp.example")["verdict"] == "auto_clean"
    assert desk.check("fashion.example")["verdict"] == "images"
    assert desk.check("gone.example")["verdict"] == "error"
    assert desk.check("instagram.com")["verdict"] == "social"         # מהקטלוג, בלי להוריד
    assert svc.redis.hget("fp:web:verdict", "rsvp.example") == "auto_clean"
    changes = svc.redis.xrange("fp:web:changes")
    assert [c[1]["host"] for c in changes][:2] == ["rsvp.example", "fashion.example"]
    # החלטה של אדם לא נדרסת על ידי הבוט
    desk.set_verdict("fashion.example", "shopping_general", "human", "admin")
    assert desk.check("fashion.example", force=True)["verdict"] == "shopping_general"
    assert desk.sync_redis() == 4


def enroll_clean(svc, client, n=1):
    code = svc.create_code("t", 1, 1, 1)["code"]
    r = client.post("/v1/enroll", json={"code": code, "public_key": wg_key(n), "name": f"טלפון {n}",
                                        "platform": "android", "app_version": "2.0.0"})
    assert r.status_code == 201, r.text
    did, ip = r.json()["device_id"], r.json()["vpn"]["address"].split("/")[0]
    h = admin_headers(client)
    r = client.patch(f"/v1/admin/devices/{did}", headers=h, json={"web_level": "clean", "web_extras": ["news_haredi"]})
    assert r.status_code == 200, r.text
    return did, ip, h


def test_device_web_level_reaches_the_proxy(env):
    svc, client = env
    did, ip, h = enroll_clean(svc, client)
    pol = json.loads(svc.redis.hget("fp:policy", ip))
    assert pol["web"]["level"] == "clean"
    assert "news_haredi" in pol["web"]["groups"] and "auto_clean" in pol["web"]["groups"]
    proxy = DevicePolicy.from_json(svc.redis.hget("fp:policy", ip), 1)
    assert proxy.web.decide("www.kikar.co.il", None) == "allow" and proxy.filtering
    assert client.patch(f"/v1/admin/devices/{did}", headers=h, json={"web_level": "bogus"}).status_code == 400
    d = client.get(f"/v1/admin/devices/{did}", headers=h).json()
    assert d["settings"]["web_level"] == "clean"
    assert {"id": "web:clean", "title": "רמת גלישה: נקי לגמרי + חדשות לציבור החרדי", "category": "web"} in d["blocks"]
    cat = client.get("/v1/admin/catalog", headers=h).json()
    assert [lv["id"] for lv in cat["web"]["levels"]] == ["essential", "clean", "shopping", ""]


def test_review_requests_auto_approve_or_wait_for_admin(env):
    svc, client = env
    did, ip, h = enroll_clean(svc, client)
    woman = b"W" * 5000
    desk = desk_for(svc, {"clean.example": "<title>טופס</title>", "pics.example": "<img src='/w.jpg'>"},
                    {"https://pics.example/w.jpg": woman}, women=(woman,))
    client.app.state.web = desk
    for host in ("clean.example", "pics.example", "pics.example"):
        svc.redis.xadd("fp:web:reviews", {"ip": ip, "host": host, "url": f"https://{host}/", "note": "", "ts": 1})
    svc.redis.xadd("fp:web:reviews", {"ip": "10.9.9.9", "host": "x.example", "url": "", "note": "", "ts": 1})
    assert desk.ingest_reviews() == 2                                   # כפילות ומכשיר לא מוכר — לא נספרים
    rows = {r["host"]: r for r in desk.reviews("")}
    assert rows["clean.example"]["status"] == "approved" and rows["clean.example"]["decision"] == "auto"
    assert rows["pics.example"]["status"] == "pending" and rows["pics.example"]["auto_verdict"] == "images"
    assert desk.ingest_reviews() == 0                                   # כבר נקלטו
    # המנהל: לאשר רק למכשיר הזה
    r = client.get("/v1/admin/web/reviews", headers=h).json()
    assert r["pending"] == 1 and r["reviews"][0]["device_name"] == "טלפון 1"
    rid = r["reviews"][0]["id"]
    assert client.post(f"/v1/admin/web/reviews/{rid}", headers=h, json={"action": "device"}).json()["status"] == "approved"
    pol = DevicePolicy.from_json(svc.redis.hget("fp:policy", ip), 1)
    assert pol.web.decide("pics.example", "images") == "allow"
    # המנהל: פסק דין לכולם
    assert client.put("/v1/admin/web/sites/www.torah-site.example", headers=h,
                      json={"verdict": "torah"}).json()["verdict"] == "torah"
    assert svc.redis.hget("fp:web:verdict", "torah-site.example") == "torah"
    assert client.put("/v1/admin/web/sites/x.example", headers=h, json={"verdict": "nope"}).status_code == 400
    sites = client.get("/v1/admin/web/sites?q=torah", headers=h).json()["sites"]
    assert sites[0]["host"] == "torah-site.example" and sites[0]["source"] == "human"
    assert client.delete("/v1/admin/web/sites/torah-site.example", headers=h).status_code == 204
    assert svc.redis.hget("fp:web:verdict", "torah-site.example") is None


def test_adult_list_is_loaded_for_the_bot(env, tmp_path):
    svc, _ = env
    path = svc.cfg.adult_list_file
    import os
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("# list\nbad-adult.example\n||other.example^\n")
    desk = desk_for(svc, {})
    assert desk.check("www.bad-adult.example")["verdict"] == "adult"
    assert desk.is_adult("cdn.other.example")


def test_http_response_type_for_block(fp):
    assert isinstance(fp._page(b"x"), http.Response)
