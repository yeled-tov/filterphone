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
import sqlite3
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

    # Don't report success just because rules were written: verify that core
    # YouTube service hosts are not blocked by remaining filters/rules and that
    # the Spotify image CDN is actually blocked. The client-specific policy is
    # handled separately and is deliberately not relaxed for multiple devices.
    for host in ("youtube.com", "googlevideo.com", "youtubei.googleapis.com"):
        result = checks[host]
        if "error" in result:
            raise RuntimeError(f"Could not verify YouTube DNS for {host}: {result['error']}")
        if result.get("reason", "").startswith("Filtered"):
            raise RuntimeError(
                f"YouTube still appears DNS-filtered for {host}: "
                f"{result.get('reason')} {result.get('rule', '')}. "
                "See check_host output and active per-device policies."
            )
    spotify = checks["i.scdn.co"]
    if "error" in spotify:
        raise RuntimeError(f"Could not verify Spotify image DNS block: {spotify['error']}")
    if not spotify.get("reason", "").startswith("Filtered"):
        raise RuntimeError(
            "Spotify image host i.scdn.co is not reported blocked by AdGuard; "
            f"reason={spotify.get('reason', 'unknown')}, rule={spotify.get('rule', '')}"
        )
    print("Verified: YouTube service hosts are not DNS-filtered globally; Spotify image CDN is blocked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
