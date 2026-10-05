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


def test_learning_is_per_device_and_expires(policy):
    assert policy.learn("10.8.0.11", "api.someapp.com", level=2)
    assert policy.should_passthrough("10.8.0.11", "api.someapp.com")
    assert not policy.should_passthrough("10.8.0.12", "api.someapp.com")
    policy.learn("10.8.0.11", "old.someapp.com", level=2, expires_at=time.time() - 1)
    assert not policy.should_passthrough("10.8.0.11", "old.someapp.com")


def test_strict_level_never_learns_social_image_cdns(policy):
    assert not policy.learn("10.8.0.13", "scontent.cdninstagram.com", level=1)
    assert not policy.should_passthrough("10.8.0.13", "scontent.cdninstagram.com")
    assert policy.learn("10.8.0.13", "scontent.cdninstagram.com", level=2)


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
