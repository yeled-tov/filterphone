import asyncio
import time

import pytest

from filterphone import protocol
from filterphone.proxy.hosts import HostPolicy, load_lists
from filterphone.settings import PACKAGE_DIR


@pytest.fixture(scope="module")
def policy():
    pt, insp = load_lists(PACKAGE_DIR / "proxy" / "passthrough.txt")
    return HostPolicy(pt, insp, learn_ttl_s=3600)


def test_static_passthrough_matches_domain_and_subdomains(policy):
    assert policy.should_passthrough("10.8.0.10", "web.whatsapp.com")
    assert policy.should_passthrough("10.8.0.10", "whatsapp.com")
    assert policy.should_passthrough("10.8.0.10", "login.bankhapoalim.co.il")
    assert not policy.should_passthrough("10.8.0.10", "www.ynet.co.il")
    assert not policy.should_passthrough("10.8.0.10", "notwhatsapp.com")


def test_missing_sni_passes_through(policy):
    assert policy.should_passthrough("10.8.0.10", None)
    assert policy.should_passthrough("10.8.0.10", "")


def test_inspect_overrides_a_broad_passthrough_glob(policy):
    # *.googleapis.com ב-passthrough, אבל Street View רשום ב-[inspect] — ולכן נבדק (לא passthrough)
    assert policy.should_passthrough("10.8.0.10", "foo.googleapis.com")
    assert policy.is_inspect_only("streetviewpixels-pa.googleapis.com")
    assert not policy.should_passthrough("10.8.0.10", "streetviewpixels-pa.googleapis.com")
    # תמונות מקומות בגוגל מפות (ggpht / googleusercontent) — נבדקות כרגיל
    assert not policy.should_passthrough("10.8.0.10", "lh3.ggpht.com")
    assert not policy.should_passthrough("10.8.0.10", "lh5.googleusercontent.com")


def test_learning_is_per_device_and_expires(policy):
    assert policy.learn("10.8.0.11", "api.someapp.com", strict_images=True)
    assert policy.should_passthrough("10.8.0.11", "api.someapp.com")
    assert not policy.should_passthrough("10.8.0.12", "api.someapp.com")
    policy.learn("10.8.0.11", "old.someapp.com", strict_images=False, expires_at=time.time() - 1)
    assert not policy.should_passthrough("10.8.0.11", "old.someapp.com")


def test_strict_devices_never_learn_image_servers(policy):
    for host in ("scontent.cdninstagram.com", "ae01.alicdn.com", "ae-pic-a1.aliexpress-media.com",
                 "encrypted-tbn0.gstatic.com", "img.kwcdn.com", "images-na.example.com", "media.licdn.com"):
        assert not policy.learn("10.8.0.13", host, strict_images=True), host
        assert not policy.should_passthrough("10.8.0.13", host)
    # מכשיר לא מחמיר (רמה 3) — כן לומד, כדי שהאפליקציה תעבוד
    assert policy.learn("10.8.0.13", "scontent.cdninstagram.com", strict_images=False)


def test_image_host_heuristic_does_not_catch_regular_apis():
    for host in ("api.someapp.com", "graph.facebook.com", "imaging-api.bank.co.il", "mediation.ads.com"):
        assert not HostPolicy.looks_like_image_host(host), host
    for host in ("img.kwcdn.com", "ae-pic-a1.aliexpress-media.com", "thumbs2.example.org", "images.x.com"):
        assert HostPolicy.looks_like_image_host(host), host


def test_distrust_error_detection():
    assert HostPolicy.is_distrust_error(
        "Client TLS handshake failed. The client does not trust the proxy's certificate for x.com")
    assert HostPolicy.is_distrust_error("sslv3 alert certificate unknown")
    assert not HostPolicy.is_distrust_error("connection reset by peer")
    assert not HostPolicy.is_distrust_error(None)


async def test_protocol_roundtrip():
    reader = asyncio.StreamReader()
    reader.feed_data(protocol.encode({"level": 2, "ct": "image/jpeg"}, b"\x01\x02\x03"))
    reader.feed_eof()
    header, body = await protocol.read_message(reader)
    assert header == {"level": 2, "ct": "image/jpeg"} and body == b"\x01\x02\x03"


async def test_protocol_rejects_oversized():
    reader = asyncio.StreamReader()
    reader.feed_data((10**9).to_bytes(4, "big") + (0).to_bytes(4, "big"))
    with pytest.raises(protocol.ProtocolError):
        await protocol.read_message(reader)


def test_grease_marks_chromium_client_hellos():
    from types import SimpleNamespace

    from filterphone.proxy.hosts import is_chromium_hello, is_grease
    assert all(is_grease(v) for v in (0x0A0A, 0x1A1A, 0xFAFA)) and not any(is_grease(v) for v in (0x1301, 0x0A1A, 0))
    assert is_chromium_hello(SimpleNamespace(cipher_suites=[0x2A2A, 0x1301], extensions=[]))
    assert is_chromium_hello(SimpleNamespace(cipher_suites=[0x1301], extensions=[(0x5A5A, b"")]))
    assert not is_chromium_hello(SimpleNamespace(cipher_suites=[0x1301, 0xC02B], extensions=[(0, b"")]))
    assert not is_chromium_hello(SimpleNamespace(sni="x"))                       # בלי מידע — כמו קודם


def test_content_hosts_and_thumbnail_servers(policy):
    for host in ("encrypted-tbn0.gstatic.com", "encrypted-vtbn3.gstatic.com", "www.google.co.il",
                 "m.aliexpress.com", "ae-pic-a1.aliexpress-media.com", "il.shein.com"):
        assert policy.is_content_host(host), host
    assert not policy.is_content_host("api.bank.example")
    assert not policy.learn("10.8.0.14", "encrypted-vtbn0.gstatic.com", strict_images=True)


# ---------------------------------------------------------------- סינון טקסט בדפים

def test_textscan_blocks_only_dense_bad_pages():
    from filterphone.proxy import serp, textscan
    terms = serp.TermMatcher(["badterm", "מילהרעה"])
    clean = "<html><body><p>" + "שלום עולם זה דף רגיל לגמרי. " * 200 + "badterm</p></body></html>"
    dense = "<html><body>" + "<p>badterm מילהרעה text here</p>" * 20 + "</body></html>"
    hidden = "<html><script>" + "badterm " * 50 + "</script><body>דף נקי</body></html>"
    assert not textscan.is_bad(textscan.visible_text(clean.encode()), terms)      # מילה אחת בכתבה — עובר
    assert textscan.is_bad(textscan.visible_text(dense.encode()), terms)          # מלא — נחסם
    assert not textscan.is_bad(textscan.visible_text(hidden.encode()), terms)     # בתוך סקריפט — לא נספר
    assert textscan.exempt("www.health.gov.il") and not textscan.exempt("example.com")


def test_news_all_option_blocks_haredi_and_general_news():
    from filterphone.api.catalog import OPTIONS
    opt = {o.id: o for o in OPTIONS}
    assert "news_haredi" in opt and "news_all" in opt
    assert "ynet.co.il" in opt["news_all"].hosts and "kikar.co.il" in opt["news_all"].hosts or \
        any("kikar" in h for h in opt["news_all"].hosts)


def test_url_words_and_global_terms_reach_every_check():
    from filterphone.api import catalog
    from filterphone.proxy import serp, textscan
    # כתובת של ערך בוויקיפדיה / חיפוש בתוך אתר — מפוענחת למילים
    assert "badterm" in textscan.url_words("/wiki/Some_badterm_page")
    assert "מילהרעה" in textscan.url_words("/w/index.php?search=%D7%9E%D7%99%D7%9C%D7%94%D7%A8%D7%A2%D7%94")
    try:
        catalog.set_global_terms(["globalterm"])
        terms = catalog.resolve_keywords({})["terms"]
        assert "globalterm" in terms                       # נכנס לרשימה של הטלפונים ושל הפרוקסי
        assert serp.TermMatcher(terms).matches(textscan.url_words("/wiki/globalterm"))
    finally:
        catalog.set_global_terms([])


def test_hello_fingerprint_ignores_grease_and_order():
    from types import SimpleNamespace

    from filterphone.proxy.hosts import hello_fingerprint
    chrome = SimpleNamespace(extensions=[(0x0A0A, b""), (43, b""), (0xFE0D, b""), (16, b"")], alpn_protocols=[b"h2"])
    chrome2 = SimpleNamespace(extensions=[(16, b""), (0xFE0D, b""), (0x1A1A, b""), (43, b"")], alpn_protocols=[b"h2"])
    cronet = SimpleNamespace(extensions=[(0x2A2A, b""), (43, b""), (16, b"")], alpn_protocols=[b"h2"])
    assert hello_fingerprint(chrome) == hello_fingerprint(chrome2)      # סדר + GREASE לא משנים
    assert hello_fingerprint(chrome) != hello_fingerprint(cronet)       # כרום ≠ Cronet (בלי ECH)
    assert hello_fingerprint(object()) == ""
    assert hello_fingerprint(chrome) == "16,43,65037|h2"                # יציב — נשמר ב-Redis בין הפעלות


def test_api_hosts_skip_web_level():
    from filterphone.proxy.hosts import is_api_host
    assert is_api_host("speechs3proto2-pa.googleapis.com")              # המיקרופון במקלדת (Gboard)
    assert is_api_host("play-fe.googleapis.com") and is_api_host("rr3---sn-x.googlevideo.com")
    assert is_api_host("www.gstatic.com") and is_api_host("clients4.google.com")
    assert not is_api_host("www.google.com") and not is_api_host("play.google.com")
    assert not is_api_host("evilgoogleapis.com") and not is_api_host("") and not is_api_host(None)
