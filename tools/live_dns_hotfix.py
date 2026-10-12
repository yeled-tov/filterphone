#!/usr/bin/env python3
"""One-time, idempotent global YouTube unblock for the live FilterPhone server.

Preserves unrelated user rules, removes YouTube block rules and YouTube-specific
entries from every stored device policy, adds DNS exceptions for YouTube service
domains, and preserves Spotify image/ad blocks plus GitHub access exceptions.
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

# Remove YouTube-related blocks globally, including thumbnails and video delivery.
# Keep unrelated Google APIs and all other non-YouTube policy choices unchanged.
YOUTUBE_HOST_SUFFIXES = (
    "youtube.com",
    "youtube-nocookie.com",
    "youtu.be",
    "googlevideo.com",
    "ytimg.com",
    "youtubei.googleapis.com",
    "youtube.googleapis.com",
    "youtubeembeddedplayer.googleapis.com",
    "youtube.co.il",
    "ggpht.com",
    "yt3.googleusercontent.com",
)
YOUTUBE_ALLOW_HOSTS = YOUTUBE_HOST_SUFFIXES
# Some parent zones (notably ytimg.com) have no A record of their own.
# Resolve a real YouTube service hostname from each allowed family instead.
YOUTUBE_DNS_CHECK_HOSTS = {
    "youtube.com": "youtube.com",
    "youtube-nocookie.com": "youtube-nocookie.com",
    "youtu.be": "youtu.be",
    "googlevideo.com": "googlevideo.com",
    "ytimg.com": "i.ytimg.com",
    "youtubei.googleapis.com": "youtubei.googleapis.com",
    "youtube.googleapis.com": "youtube.googleapis.com",
    "youtubeembeddedplayer.googleapis.com": "youtubeembeddedplayer.googleapis.com",
    "youtube.co.il": "youtube.co.il",
    "ggpht.com": "yt3.ggpht.com",
    "yt3.googleusercontent.com": "yt3.googleusercontent.com",
}
YOUTUBE_APP_PACKAGES = {
    "com.google.android.youtube",
    "com.google.android.apps.youtube.kids",
    "com.google.android.apps.youtube.music",
    "app.revanced.android.youtube",
    "com.vanced.android.youtube",
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

# Spotify-owned ad endpoints only. Avoid broad third-party ad networks and
# audio delivery edges, which could break unrelated apps or interrupt playback.
SPOTIFY_AD_HOSTS = (
    "ads.spotify.com",
    "ads-fa.spotify.com",
    "adeventtracker.spotify.com",
    "audio-ads.spotify.com",
)

# GitHub app/API/repository assets. These exceptions keep core GitHub access
# working even if an upstream DNS blocklist accidentally matches one of them.
GITHUB_ALLOW_HOSTS = (
    "github.com",
    "githubusercontent.com",
    "githubassets.com",
    "githubapp.com",
)
RULE_HOST = re.compile(r"^\|\|([a-z0-9.*-]+)\^", re.IGNORECASE)


def _host_from_rule(rule: str) -> str | None:
    match = RULE_HOST.match(rule.strip())
    if not match:
        return None
    return match.group(1).lower().removeprefix("*.")


def update_user_rules(current: list[str]) -> tuple[list[str], list[str], list[str], list[str], list[str]]:
    """Allow YouTube/GitHub service domains while blocking specific Spotify image/ad hosts."""
    kept: list[str] = []
    removed: list[str] = []
    for rule in current:
        host = _host_from_rule(rule)
        is_block_rule = rule.strip().startswith("||")
        if is_block_rule and host:
            youtube_host = any(
                host == suffix or host.endswith("." + suffix)
                for suffix in YOUTUBE_HOST_SUFFIXES
            )
            github_host = any(
                host == suffix or host.endswith("." + suffix)
                for suffix in GITHUB_ALLOW_HOSTS
            )
            if youtube_host or github_host:
                removed.append(rule)
                continue
        kept.append(rule)

    existing = {line.strip().lower() for line in kept}
    added_spotify: list[str] = []
    added_github: list[str] = []
    added_youtube: list[str] = []
    for host in (*SPOTIFY_IMAGE_HOSTS, *SPOTIFY_AD_HOSTS):
        rule = f"||{host}^"
        if rule.lower() not in existing:
            kept.append(rule)
            existing.add(rule.lower())
            added_spotify.append(rule)

    # Explicitly allow YouTube even if an upstream DNS blocklist contains it.
    for host in YOUTUBE_ALLOW_HOSTS:
        rule = f"@@||{host}^$important"
        if rule.lower() not in existing:
            kept.append(rule)
            existing.add(rule.lower())
            added_youtube.append(rule)

    # $important makes the DNS exception take precedence over matching blocklists.
    for host in GITHUB_ALLOW_HOSTS:
        rule = f"@@||{host}^$important"
        if rule.lower() not in existing:
            kept.append(rule)
            existing.add(rule.lower())
            added_github.append(rule)
    return kept, removed, added_spotify, added_github, added_youtube

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


def _disable_youtube_safe_search(base_url: str, user: str, password: str) -> dict[str, Any]:
    """Disable only AdGuard's YouTube SafeSearch rewrite, preserving other engines."""
    before = _request(base_url, user, password, "GET", "/safesearch/status") or {}
    if not isinstance(before, dict) or "youtube" not in before:
        raise RuntimeError(
            "AdGuard SafeSearch status did not expose the YouTube setting; "
            "refusing to claim YouTube is fully unblocked."
        )

    if before.get("youtube") is not False:
        updated = {**before, "youtube": False}
        _request(base_url, user, password, "PUT", "/safesearch/settings", updated)

    after = _request(base_url, user, password, "GET", "/safesearch/status") or {}
    if not isinstance(after, dict) or after.get("youtube") is not False:
        raise RuntimeError(
            f"AdGuard still has YouTube SafeSearch rewriting enabled: {after!r}"
        )
    # AdGuard can override the global setting per configured DNS client/device.
    clients_response = _request(base_url, user, password, "GET", "/clients") or {}
    clients = clients_response.get("clients") if isinstance(clients_response, dict) else None
    if not isinstance(clients, list):
        raise RuntimeError("AdGuard clients API did not return a clients list; cannot verify per-device YouTube settings.")

    changed_clients = 0
    for client in clients:
        if not isinstance(client, dict) or not isinstance(client.get("name"), str):
            continue
        client_safe_search = client.get("safe_search")
        if not isinstance(client_safe_search, dict):
            client_safe_search = {}
        legacy_enabled = client.get("safesearch_enabled") is True
        youtube_override = client_safe_search.get("youtube") is True
        if not legacy_enabled and not youtube_override:
            continue

        # Older AdGuard client records use one SafeSearch boolean for all engines.
        # Migrate that behavior to the per-engine object before disabling only YouTube.
        if legacy_enabled and not client_safe_search:
            client_safe_search = {**after, "enabled": True}
        updated_client = {
            **client,
            "safe_search": {**client_safe_search, "youtube": False},
        }
        if legacy_enabled:
            updated_client["safesearch_enabled"] = False
        _request(
            base_url, user, password, "POST", "/clients/update",
            {"name": client["name"], "data": updated_client},
        )
        changed_clients += 1

    verify_response = _request(base_url, user, password, "GET", "/clients") or {}
    verified_clients = verify_response.get("clients") if isinstance(verify_response, dict) else None
    if not isinstance(verified_clients, list):
        raise RuntimeError("Could not verify per-device YouTube SafeSearch settings.")
    still_enabled = [
        str(client.get("name", "<unnamed>"))
        for client in verified_clients
        if isinstance(client, dict)
        and (
            isinstance(client.get("safe_search"), dict)
            and client["safe_search"].get("youtube") is True
            or client.get("safesearch_enabled") is True
        )
    ]
    if still_enabled:
        raise RuntimeError(
            "YouTube SafeSearch rewrite remains enabled for AdGuard clients: "
            + ", ".join(still_enabled)
        )
    print(
        "YouTube SafeSearch rewrite: disabled globally and verified; "
        f"per-client overrides changed: {changed_clients}; other engines unchanged."
    )
    return after


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


def _remove_youtube_policy_everywhere() -> tuple[list[int], int]:
    """Remove YouTube blocking from every stored device policy, active or inactive."""
    if not DB_PATH.is_file():
        print("Device policy: database not found; left device policies unchanged.")
        return [], 0

    def yt_host(host: Any) -> bool:
        value = str(host).strip().lower().removeprefix("*.").rstrip(".")
        return any(value == suffix or value.endswith("." + suffix)
                   for suffix in YOUTUBE_HOST_SUFFIXES)

    with sqlite3.connect(DB_PATH, timeout=30) as connection:
        connection.row_factory = sqlite3.Row
        columns = {str(row["name"]) for row in connection.execute("PRAGMA table_info(devices)")}
        required = {"id", "policy", "policy_version", "policy_checksum", "policy_revision", "policy_updated_at"}
        missing = sorted(required - columns)
        if missing:
            raise RuntimeError(
                "Device database schema is missing required policy columns: " + ", ".join(missing)
            )

        rows = connection.execute("SELECT id, policy FROM devices").fetchall()
        changed: list[tuple[int, dict[str, Any]]] = []
        for row in rows:
            try:
                policy = json.loads(row["policy"] or "{}")
            except (TypeError, ValueError):
                raise RuntimeError(f"Device {row['id']} has invalid policy JSON; refusing to overwrite it.") from None
            if not isinstance(policy, dict):
                raise RuntimeError(f"Device {row['id']} policy is not a JSON object; refusing to overwrite it.")

            before = json.dumps(policy, sort_keys=True, ensure_ascii=False)
            options = policy.get("options")
            if isinstance(options, list):
                policy["options"] = [item for item in options if item != "youtube"]

            custom_hosts = policy.get("custom_hosts")
            if isinstance(custom_hosts, list):
                policy["custom_hosts"] = [host for host in custom_hosts if not yt_host(host)]

            # custom_apps is a compatibility field inside policy JSON, not a DB column.
            custom_apps = policy.get("custom_apps")
            if isinstance(custom_apps, list):
                policy["custom_apps"] = [
                    pkg for pkg in custom_apps
                    if str(pkg).strip().lower() not in YOUTUBE_APP_PACKAGES
                ]

            app_rules = policy.get("app_rules")
            if isinstance(app_rules, dict):
                cleaned_rules: dict[str, Any] = {}
                for pkg, rule in app_rules.items():
                    if str(pkg).strip().lower() in YOUTUBE_APP_PACKAGES:
                        continue
                    if isinstance(rule, dict) and isinstance(rule.get("hosts"), list):
                        rule = {**rule, "hosts": [host for host in rule["hosts"] if not yt_host(host)]}
                    cleaned_rules[pkg] = rule
                policy["app_rules"] = cleaned_rules
            elif isinstance(app_rules, list):
                cleaned_rules = []
                for rule in app_rules:
                    if not isinstance(rule, dict):
                        cleaned_rules.append(rule)
                        continue
                    if str(rule.get("package", "")).strip().lower() in YOUTUBE_APP_PACKAGES:
                        continue
                    if isinstance(rule.get("hosts"), list):
                        rule = {**rule, "hosts": [host for host in rule["hosts"] if not yt_host(host)]}
                    cleaned_rules.append(rule)
                policy["app_rules"] = cleaned_rules

            if before != json.dumps(policy, sort_keys=True, ensure_ascii=False):
                changed.append((int(row["id"]), policy))

        if not changed:
            print("Device policy: no stored YouTube blocks found.")
            return [], 0

        # Require a successful SQLite backup before changing any stored policy.
        backup = _backup_database()
        if backup is None or not backup.is_file():
            raise RuntimeError("Could not back up the database before changing device policies.")

        has_meta = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'"
        ).fetchone() is not None
        meta_columns = {
            str(row["name"]) for row in connection.execute("PRAGMA table_info(meta)")
        } if has_meta else set()
        has_revision_meta = {"key", "value"} <= meta_columns and connection.execute(
            "SELECT 1 FROM meta WHERE key = 'policy_revision'"
        ).fetchone() is not None
        revision = int(connection.execute(
            "SELECT value FROM meta WHERE key = 'policy_revision'"
        ).fetchone()["value"]) if has_revision_meta else 0
        updated_at = int(time.time())
        changed_ids: list[int] = []

        with connection:
            for device_id, policy in changed:
                if has_revision_meta:
                    connection.execute(
                        "UPDATE meta SET value = CAST(value AS INTEGER) + 1 WHERE key = 'policy_revision'"
                    )
                    revision = int(connection.execute(
                        "SELECT value FROM meta WHERE key = 'policy_revision'"
                    ).fetchone()["value"])
                else:
                    revision += 1

                connection.execute(
                    "UPDATE devices SET policy = ?, policy_version = policy_version + 1, "
                    "policy_checksum = '', policy_updated_at = ?, policy_revision = ? WHERE id = ?",
                    (json.dumps(policy, ensure_ascii=False, separators=(",", ":")),
                     updated_at, revision, device_id),
                )
                changed_ids.append(device_id)

        print(f"Device policy: removed YouTube blocks from {len(changed_ids)} stored device records.")
        print(f"Database backup: {backup}")
        return changed_ids, len(changed_ids)

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

    # SafeSearch rewriting is separate from block rules and can alter YouTube API hosts.
    # Disable YouTube's rewrite only; leave other search-engine settings unchanged.
    _disable_youtube_safe_search(base_url, user, password)

    status = _request(base_url, user, password, "GET", "/filtering/status") or {}
    current = [str(x) for x in (status.get("user_rules") or [])]
    merged, removed, added_spotify, added_github, added_youtube = update_user_rules(current)

    # Save the exact prior custom-rule set before writing anything.
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    rules_backup = BACKUP_DIR / f"adguard-user-rules-before-youtube-dns-hotfix-{stamp}.json"
    rules_backup.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rules_backup.chmod(0o600)

    if merged != current:
        _request(base_url, user, password, "POST", "/filtering/set_rules", {"rules": merged})

    changed_ids, changed_count = _remove_youtube_policy_everywhere()
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
    missing = [f"||{host}^" for host in (*SPOTIFY_IMAGE_HOSTS, *SPOTIFY_AD_HOSTS)
               if f"||{host}^".lower() not in final_lower]
    missing_github = [f"@@||{host}^$important" for host in GITHUB_ALLOW_HOSTS
                      if f"@@||{host}^$important".lower() not in final_lower]
    missing_youtube = [f"@@||{host}^$important" for host in YOUTUBE_ALLOW_HOSTS
                       if f"@@||{host}^$important".lower() not in final_lower]
    if missing or missing_github or missing_youtube:
        raise RuntimeError(
            f"DNS rules missing: Spotify={missing}; GitHub={missing_github}; YouTube={missing_youtube}"
        )

    checks = {
        host: _check_host(base_url, user, password, host)
        for host in (*YOUTUBE_ALLOW_HOSTS, "i.scdn.co", *SPOTIFY_AD_HOSTS,
                     "github.com", "api.github.com",
                     "raw.githubusercontent.com", "avatars.githubusercontent.com")
    }
    print("DNS hotfix applied.")
    print(f"Unscoped YouTube service-block rules removed: {len(removed)}")
    for rule in removed:
        print(f"  removed: {rule}")
    print(f"Spotify image/ad block rules added: {len(added_spotify)}; verified present: {len(SPOTIFY_IMAGE_HOSTS) + len(SPOTIFY_AD_HOSTS)}")
    print(f"GitHub allow exceptions added: {len(added_github)}; verified present: {len(GITHUB_ALLOW_HOSTS)}")
    for rule in added_github:
        print(f"  allowed: {rule}")
    print(f"Previous AdGuard rules backup: {rules_backup}")
    print(f"Stored device policies cleaned of YouTube blocks: {changed_count}; updated IDs: {changed_ids}")
    print(f"YouTube allow exceptions added: {len(added_youtube)}; verified present: {len(YOUTUBE_ALLOW_HOSTS)}")
    for rule in added_youtube:
        print(f"  allowed: {rule}")
    print("AdGuard check_host results:")
    print(json.dumps(checks, ensure_ascii=False, indent=2))

    # SafeSearch is a policy rewrite, not a full service block. AdGuard
    # check_host may report FilteredSafeSearch for YouTube's internal API host.
    for host in YOUTUBE_ALLOW_HOSTS:
        result = checks.get(host)
        if result and "error" in result:
            raise RuntimeError(f"Could not verify YouTube DNS for {host}: {result['error']}")

    # check_host can miss a just-written user rule or report SafeSearch behavior;
    # query the actual local DNS service after the write. This is the decisive
    # test for Spotify CDN blocking and verifies that YouTube still resolves.
    dns_checks = {
        host: _dns_a_query(host)
        for host in (*YOUTUBE_DNS_CHECK_HOSTS.values(), "i.scdn.co", *SPOTIFY_AD_HOSTS,
                     "github.com", "api.github.com", "raw.githubusercontent.com",
                     "avatars.githubusercontent.com")
    }
    print("Direct DNS query results from the live server:")
    print(json.dumps(dns_checks, ensure_ascii=False, indent=2))
    for service_domain, query_host in YOUTUBE_DNS_CHECK_HOSTS.items():
        if dns_checks[query_host]["blocked"] or not dns_checks[query_host]["a_records"]:
            raise RuntimeError(
                f"YouTube DNS blocked or not resolving for {service_domain} "
                f"(checked {query_host}): {dns_checks[query_host]}"
            )
    if not dns_checks["i.scdn.co"]["blocked"]:
        raise RuntimeError(
            "The live DNS server still resolves i.scdn.co; its rule appears in user_rules "
            "but is not taking effect. Do not treat Spotify image blocking as verified."
        )
    for host in SPOTIFY_AD_HOSTS:
        # Direct DNS is the authoritative check. AdGuard's check_host endpoint
        # can report NotFilteredNotFound for a just-written user rule even when
        # the running DNS server correctly returns 0.0.0.0 (seen with ads-fa).
        if not dns_checks[host]["blocked"]:
            raise RuntimeError(
                f"Spotify ad host {host} is not blocked by live DNS: {dns_checks[host]}"
            )

    for host in ("github.com", "api.github.com", "raw.githubusercontent.com",
                 "avatars.githubusercontent.com"):
        if dns_checks[host]["blocked"] or not dns_checks[host]["a_records"]:
            raise RuntimeError(
                f"GitHub domain {host} is still blocked or has no IPv4 answer: {dns_checks[host]}"
            )

    print("Verified: all YouTube host families and GitHub resolve on live DNS; Spotify image CDN and Spotify ad endpoints remain blocked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
