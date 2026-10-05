"""ניהול WireGuard דרך הפקודה wg — בלי shell, בלי root (רק CAP_NET_ADMIN).

מסד הנתונים הוא מקור האמת לרשימת המכשירים. בעליית השירות, reconcile()
מיישר את הממשק לפיו: מוסיף מכשירים חסרים ומסיר מכשירים שלא קיימים או מושבתים.
המפתח הפרטי של השרת אף פעם לא נקרא כאן.
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass

log = logging.getLogger("filterphone.wg")


class WireGuardError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PeerState:
    public_key: str
    latest_handshake: int        # שניות epoch, 0 = מעולם לא
    rx: int
    tx: int


class WireGuard:
    def __init__(self, interface: str, wg_binary: str = "wg"):
        self.iface = interface
        self.bin = wg_binary

    def _run(self, *args: str, stdin: str | None = None) -> str:
        try:
            r = subprocess.run([self.bin, *args], input=stdin, capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise WireGuardError(str(e)) from e
        if r.returncode != 0:
            raise WireGuardError(r.stderr.strip() or f"wg exited {r.returncode}")
        return r.stdout

    def public_key(self) -> str:
        return self._run("show", self.iface, "public-key").strip()

    def peers(self) -> dict[str, PeerState]:
        hs = {}
        for line in self._run("show", self.iface, "latest-handshakes").splitlines():
            key, _, ts = line.partition("\t")
            hs[key] = int(ts or 0)
        out = {}
        for line in self._run("show", self.iface, "transfer").splitlines():
            key, rx, tx = (line.split("\t") + ["0", "0"])[:3]
            out[key] = PeerState(key, hs.get(key, 0), int(rx), int(tx))
        for key, ts in hs.items():
            out.setdefault(key, PeerState(key, ts, 0, 0))
        return out

    def add_peer(self, public_key: str, preshared_key: str, ip: str) -> None:
        if not preshared_key:           # לקוחות ישנים בלי מפתח משותף
            self._run("set", self.iface, "peer", public_key, "allowed-ips", f"{ip}/32")
            return
        # המפתח המשותף עובר ב-stdin, לא בשורת הפקודה (שנראית ב-ps)
        self._run("set", self.iface, "peer", public_key, "preshared-key", "/dev/stdin",
                  "allowed-ips", f"{ip}/32", stdin=preshared_key + "\n")

    def remove_peer(self, public_key: str) -> None:
        self._run("set", self.iface, "peer", public_key, "remove")

    def reconcile(self, desired: dict[str, tuple[str, str]]) -> tuple[int, int]:
        """desired: public_key → (preshared_key, ip). מחזיר (נוספו, הוסרו)."""
        current = set(self.peers())
        added = removed = 0
        for key, (psk, ip) in desired.items():
            self.add_peer(key, psk, ip)         # wg set הוא upsert — מיישר גם מפתח משותף וכתובת
            added += key not in current
        for key in current - set(desired):
            self.remove_peer(key)
            removed += 1
        if added or removed:
            log.info("wireguard reconciled: +%d -%d", added, removed)
        return added, removed
