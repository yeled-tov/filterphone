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
