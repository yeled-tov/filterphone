"""ניהול WireGuard דרך הפקודה wg — בלי shell, בלי root (רק CAP_NET_ADMIN).

מסד הנתונים הוא מקור האמת לרשימת המכשירים. reconcile() (בעלייה ואחר כך כל דקה)
מיישר את הממשק לפיו: קורא את המצב הנוכחי בשתי פקודות, ומשנה רק את מה שבאמת שונה —
מכשירים חסרים נוספים בפקודה אחת (wg addconf, המפתחות המשותפים ב-stdin), שינויים
ומחיקות בפקודה לכל מכשיר. כך גם באלף מכשירים הבדיקה הדקתית היא כמה תהליכים, לא אלף.
המפתח הפרטי של השרת אף פעם לא נקרא כאן (לא משתמשים ב-wg show dump).
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

    def _table(self, what: str) -> dict[str, str]:
        out = {}
        for line in self._run("show", self.iface, what).splitlines():
            key, _, value = line.partition("\t")
            if key:
                out[key] = value.strip()
        return out

    def add_peers(self, peers: dict[str, tuple[str, str]]) -> None:
        """הוספת כמה מכשירים חדשים בפקודה אחת. רק למכשירים שעוד לא קיימים בממשק
        (addconf מוסיף כתובות ולא מחליף — לשינוי מכשיר קיים: add_peer)."""
        if not peers:
            return
        lines = []
        for key, (psk, ip) in peers.items():
            lines += ["[Peer]", f"PublicKey = {key}", f"AllowedIPs = {ip}/32"]
            if psk:
                lines.append(f"PresharedKey = {psk}")
        self._run("addconf", self.iface, "/dev/stdin", stdin="\n".join(lines) + "\n")

    def reconcile(self, desired: dict[str, tuple[str, str]]) -> tuple[int, int]:
        """desired: public_key → (preshared_key, ip). מחזיר (נוספו/תוקנו, הוסרו)."""
        allowed = self._table("allowed-ips")
        psks = self._table("preshared-keys")
        missing = {k: v for k, v in desired.items() if k not in allowed}
        changed = {k: (psk, ip) for k, (psk, ip) in desired.items()
                   if k in allowed and (allowed[k] != f"{ip}/32" or psks.get(k, "(none)") != (psk or "(none)"))}
        extra = [k for k in allowed if k not in desired]
        self.add_peers(missing)
        for key, (psk, ip) in changed.items():
            self.add_peer(key, psk, ip)         # wg set מחליף את הכתובות והמפתח המשותף
        if extra:
            args: list[str] = []
            for key in extra:
                args += ["peer", key, "remove"]
            self._run("set", self.iface, *args)
        added, removed = len(missing) + len(changed), len(extra)
        if added or removed:
            log.info("wireguard reconciled: +%d (fixed %d) -%d", len(missing), len(changed), removed)
        return added, removed
