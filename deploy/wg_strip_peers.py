"""משאיר ב-wg0.conf רק את [Interface] — בלי לקוחות ובלי PostUp/PostDown.

הלקוחות נשמרים במסד הנתונים וה-API מוסיף אותם בעלייה; חומת האש מנוהלת ב-firewall.sh.
כך יש מקור אמת אחד לכל דבר, ואין כללי iptables כפולים.
"""
from __future__ import annotations

import os
import sys


def main(path: str) -> None:
    out, in_interface = [], False
    for line in open(path, encoding="utf-8").read().splitlines():
        s = line.strip()
        if s.startswith("["):
            in_interface = s == "[Interface]"
        if not in_interface:
            continue
        if s.split("=", 1)[0].strip() in ("PostUp", "PostDown"):
            continue
        out.append(line)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(out).rstrip() + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    print("  wg0.conf: נשאר [Interface] בלבד")


if __name__ == "__main__":
    main(sys.argv[1])
