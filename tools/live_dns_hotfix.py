#!/usr/bin/env python3
"""One-time, idempotent DNS repair for the live FilterPhone server.

Keeps user rules intact, removes only unscoped broad YouTube service blocks,
retains YouTube thumbnail blocks, and adds specific Spotify image CDN hosts.
If exactly one active device explicitly has the catalog's full-YouTube block
selected, removes that option only for that device. If multiple devices have
it selected, no per-device policy is changed automatically.
"""
from __future__ import annotations

import json
import os
import re
import socket
import sqlite3
import struct
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ENV_PATH = Path("/etc/filterphone/filterphone.env")
DB_PATH = Path("/var/lib/filterphone/filterphone.db")
BACKUP_DIR = Path("/root/fp-backups")

# These are service endpoints, not image CDNs. Their unscoped blocks can
# make YouTube entirely unavailable. Deliberately do NOT include ytimg.com:
# that host is retained as a thumbnail-only block.
YOUTUBE_SERVICE_HOSTS = {
    "youtube.com",
    "youtu.be",
    "googlevideo.com",
    "youtubei.googleapis.com",
    "youtube.googleapis.com",
    "youtube-nocookie.com",
    "youtube.co.il",
    "shorts.youtube.com",
    "charts.youtube.com",
}

# Specific image hosts only. Do not block scdn.co / spotifycdn.com wholesale,
# because those parent domains may also serve non-image app resources.
SPOTIFY_IMAGE_HOSTS = (
    "i.scdn.co",
    "p.scdn.co",
    "mosaic.scdn.co",
    "canvaz.scdn.co",
    "dailymix-images.scdn.co",
    "newjams-images.scdn.co",
    "lineup-images.scdn.co",
    "image-cdn-ak.spotifycdn.com",
    "image-cdn-fa.spotifycdn.com",
    "image-scdn.cdn-gslb.spotify.com",
    "seed-mix-image.spotifycdn.com",
    "seeded-session-images.scdn.co",
    "thisis-images.spotifycdn.com",
    "pickasso.spotifycdn.com",
    "daylist.spotifycdn.com",
    "wrapped-images.spotifycdn.com",
    "concerts.spotifycdn.com",
    "merch-img.scdn.co",
)
RULE_HOST = re.compile(r"^\|\|([a-z0-9.*-]+)\^", re.IGNORECASE)


def _host_from_rule(rule: str) -> str | None:
    match = RULE_HOST.match(rule.strip())
    if not match:
        return None
    return match.group(1).lower().removeprefix("*.")


def update_user_rules(current: list[str]) -> tuple[list[str], list[str], list[str]]:
    """Return merged rules, removed YouTube blocks, and newly added Spotify rules."""
    kept: list[str] = []
    removed: list[str] = []
    for rule in current:
        host = _host_from_rule(rule)
        is_unscoped_block = rule.strip().startswith("||") and "$client=" not in rule.lower()
        if is_unscoped_block and host in YOUTUBE_SERVICE_HOSTS:
            removed.append(rule)
        else:
            kept.append(rule)

    existing = {line.strip().lower() for line in kept}
    added: list[str] = []
    for host in SPOTIFY_IMAGE_HOSTS:
        rule = f"||{host}^"
        if rule.lower() not in existing:
            kept.append(rule)
            existing.add(rule.lower())
            added.append(rule)
    return kept, removed, added


def _request(
    base_url: str, user: str, password: str, method: str, path: str,
    body: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    import base64

    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        base_url.rstrip("/") + "/control" + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
        },
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=8) as response:
        raw = response.read()
    return json.loads(raw) if raw.strip() else None


def _backup_database() -> Path | None:
    if not DB_PATH.is_file():
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    backup = BACKUP_DIR / f"filterphone.db-before-youtube-dns-hotfix-{stamp}"
    with sqlite3.connect(DB_PATH, timeout=30) as source, sqlite3.connect(backup) as target:
        source.backup(target)
    backup.chmod(0o600)
    return backup


def _remove_youtube_option_if_unambiguous() -> tuple[list[int], int]:
    """Only change the option when exactly one active device has it selected."""
    if not DB_PATH.is_file():
        print("Device policy: database not found; left device policies unchanged.")
        return [], 0
    with sqlite3.connect(DB_PATH, timeout=30) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT id, policy FROM devices WHERE status = 'active' AND vpn_ip IS NOT NULL"
        ).fetchall()
        candidates: list[tuple[int, dict[str, Any]]] = []
        for row in rows:
            try:
                policy = json.loads(row["policy"] or "{}")
            except (TypeError, ValueError):
                continue
            if isinstance(policy, dict) and "youtube" in (policy.get("options") or []):
                candidates.append((int(row["id"]), policy))

        if len(candidates) != 1:
            if candidates:
                ids = [device_id for device_id, _ in candidates]
                print(
                    "Device policy: full-YouTube block is selected on "
                    f"{len(ids)} active devices (IDs {ids}); none changed to avoid "
                    "unblocking other customers."
                )
            else:
                print("Device policy: no active device has the full-YouTube block selected.")
            return [], len(candidates)

        device_id, policy = candidates[0]
        backup = _backup_database()
        if backup is None:
            raise RuntimeError("Could not create a database backup before device policy change.")
        policy["options"] = [x for x in policy.get("options", []) if x != "youtube"]
        connection.execute(
            "UPDATE devices SET policy = ? WHERE id = ?",
            (json.dumps(policy, ensure_ascii=False, separators=(",", ":")), device_id),
        )
        connection.commit()
        print(f"Device policy: removed full-YouTube option from the only matching active device ID {device_id}.")
        print(f"Database backup: {backup}")
        return [device_id], 1


def _skip_dns_name(packet: bytes, offset: int) -> int:
    """Skip one DNS name, including compressed names, and return the next offset."""
    while True:
        if offset >= len(packet):
            raise ValueError("truncated DNS name")
        length = packet[offset]
        if length == 0:
            return offset + 1
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(packet):
                raise ValueError("truncated DNS compression pointer")
            return offset + 2
        if length & 0xC0:
            raise ValueError("invalid DNS label")
        offset += 1 + length


def _dns_a_query(host: str) -> dict[str, Any]:
    """Query the local AdGuard DNS listener directly, without an external DNS library."""
    transaction_id = int.from_bytes(os.urandom(2), "big")
    labels = host.rstrip(".").split(".")
    qname = b"".join(bytes([len(label.encode("ascii"))]) + label.encode("ascii") for label in labels) + b"\x00"
    packet = struct.pack("!HHHHHH", transaction_id, 0x0100, 1, 0, 0, 0) + qname + struct.pack("!HH", 1, 1)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(3)
        sock.sendto(packet, ("127.0.0.1", 53))
        response, _ = sock.recvfrom(8192)
    if len(response) < 12:
        raise RuntimeError(f"short DNS response for {host}")
    ident, flags, qdcount, ancount, _, _ = struct.unpack("!HHHHHH", response[:12])
    if ident != transaction_id:
        raise RuntimeError(f"DNS transaction ID mismatch for {host}")
    rcode = flags & 0x000F
    offset = 12
    for _ in range(qdcount):
        offset = _skip_dns_name(response, offset) + 4
    ips: list[str] = []
    for _ in range(ancount):
        offset = _skip_dns_name(response, offset)
        if offset + 10 > len(response):
            raise RuntimeError(f"truncated DNS answer for {host}")
        rtype, rclass, _, rdlength = struct.unpack("!HHIH", response[offset:offset + 10])
        offset += 10
        rdata = response[offset:offset + rdlength]
        if len(rdata) != rdlength:
            raise RuntimeError(f"truncated DNS record for {host}")
        if rclass == 1 and rtype == 1 and rdlength == 4:
            ips.append(socket.inet_ntop(socket.AF_INET, rdata))
        offset += rdlength
    blocked = rcode == 3 or bool(ips) and all(ip == "0.0.0.0" for ip in ips)
    return {"rcode": rcode, "a_records": ips, "blocked": blocked}


def _check_host(base_url: str, user: str, password: str, host: str) -> dict[str, str]:
    query = urllib.parse.urlencode({"name": host})
    try:
        result = _request(base_url, user, password, "GET", f"/filtering/check_host?{query}") or {}
        reason = str(result.get("reason", "unknown"))
        rule = str(result.get("rule", ""))
        return {"reason": reason, "rule": rule}
    except (OSError, ValueError, urllib.error.URLError) as exc:
        return {"error": str(exc)[:180]}


def main() -> int:
    if not ENV_PATH.is_file():
        raise SystemExit(f"Missing server environment file: {ENV_PATH}")
    # This file is sourced by the root-only forced command before this script.
    base_url = os.environ.get("FP_ADGUARD_URL", "").strip()
    user = os.environ.get("FP_ADGUARD_USER", "").strip()
    password = os.environ.get("FP_ADGUARD_PASSWORD", "")
    if not (base_url and user and password):
        raise SystemExit("AdGuard API settings are missing; no changes applied.")

    status = _request(base_url, user, password, "GET", "/filtering/status") or {}
    current = [str(x) for x in (status.get("user_rules") or [])]
    merged, removed, added = update_user_rules(current)

    # Save the exact prior custom-rule set before writing anything.
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    rules_backup = BACKUP_DIR / f"adguard-user-rules-before-youtube-dns-hotfix-{stamp}.json"
    rules_backup.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rules_backup.chmod(0o600)

    if merged != current:
        _request(base_url, user, password, "POST", "/filtering/set_rules", {"rules": merged})

    changed_ids, candidate_count = _remove_youtube_option_if_unambiguous()
    if changed_ids:
        # API startup rebuilds Redis policies and the per-device AdGuard block from SQLite.
        subprocess.run(["systemctl", "restart", "fp-api"], check=True, timeout=45)
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            try:
                req = urllib.request.Request("https://api.filterphone.com/health")
                with urllib.request.urlopen(req, timeout=3) as response:
                    if response.status == 200:
                        break
            except (OSError, urllib.error.URLError):
                time.sleep(2)
        else:
            raise RuntimeError("fp-api did not become healthy after policy sync.")

    # Refresh rules after fp-api has rebuilt the managed per-device block.
    status = _request(base_url, user, password, "GET", "/filtering/status") or {}
    final_rules = [str(x) for x in (status.get("user_rules") or [])]
    final_lower = {x.lower() for x in final_rules}
    missing = [f"||{host}^" for host in SPOTIFY_IMAGE_HOSTS if f"||{host}^".lower() not in final_lower]
    if missing:
        raise RuntimeError(f"Spotify image rules missing after write: {missing}")

    checks = {
        host: _check_host(base_url, user, password, host)
        for host in ("youtube.com", "googlevideo.com", "youtubei.googleapis.com", "i.scdn.co")
    }
    print("DNS hotfix applied.")
    print(f"Unscoped YouTube service-block rules removed: {len(removed)}")
    for rule in removed:
        print(f"  removed: {rule}")
    print(f"Spotify image rules added: {len(added)}; verified present: {len(SPOTIFY_IMAGE_HOSTS)}")
    print(f"Previous AdGuard rules backup: {rules_backup}")
    print(f"Active devices selecting full-YouTube block: {candidate_count}; updated IDs: {changed_ids}")
    print("AdGuard check_host results:")
    print(json.dumps(checks, ensure_ascii=False, indent=2))

    # SafeSearch is a policy rewrite, not a full service block. AdGuard
    # check_host may report FilteredSafeSearch for YouTube's internal API host.
    for host in ("youtube.com", "googlevideo.com", "youtubei.googleapis.com"):
        result = checks[host]
        if "error" in result:
            raise RuntimeError(f"Could not verify YouTube DNS for {host}: {result['error']}")
        if result.get("reason", "").startswith("Filtered") and result.get("reason") != "FilteredSafeSearch":
            raise RuntimeError(
                f"YouTube still appears blocked for {host}: "
                f"{result.get('reason')} {result.get('rule', '')}."
            )

    # check_host can miss a just-written user rule or report SafeSearch behavior;
    # query the actual local DNS service after the write. This is the decisive
    # test for Spotify CDN blocking and verifies that YouTube still resolves.
    dns_checks = {
        host: _dns_a_query(host)
        for host in ("youtube.com", "i.scdn.co")
    }
    print("Direct DNS query results from the live server:")
    print(json.dumps(dns_checks, ensure_ascii=False, indent=2))
    if dns_checks["youtube.com"]["blocked"] or not dns_checks["youtube.com"]["a_records"]:
        raise RuntimeError("YouTube DNS did not return an IPv4 address on the live server.")
    if not dns_checks["i.scdn.co"]["blocked"]:
        raise RuntimeError(
            "The live DNS server still resolves i.scdn.co; its rule appears in user_rules "
            "but is not taking effect. Do not treat Spotify image blocking as verified."
        )

    print("Verified: YouTube resolves on the live server; Spotify image CDN is blocked by live DNS.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
