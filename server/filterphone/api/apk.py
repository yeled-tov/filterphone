"""קריאת פרטי APK שהמנהל מעלה לחנות: שם חבילה, versionCode, versionName ושם (אם כתוב ישירות).

ה-AndroidManifest.xml בתוך ה-APK שמור בפורמט XML בינארי (AXML). כאן קורא מינימלי שמפענח רק את
מה שצריך מהתגית <manifest> ומהתגית <application> — בלי תלות בכלי Android בשרת. קובץ פגום או לא
APK → ApkError (לא מנחשים).
"""
from __future__ import annotations

import struct
import zipfile
from dataclasses import dataclass

_RES_VERSION_CODE = 0x0101021B
_RES_VERSION_NAME = 0x0101021C
_RES_LABEL = 0x01010001
_TYPE_STRING = 0x03
_TYPE_INT_DEC = 0x10
_TYPE_INT_HEX = 0x11


class ApkError(ValueError):
    pass


@dataclass(frozen=True)
class ApkInfo:
    package: str
    version_code: int
    version_name: str = ""
    label: str = ""


def read_apk(path: str) -> ApkInfo:
    try:
        with zipfile.ZipFile(path) as z:
            data = z.read("AndroidManifest.xml")
    except (zipfile.BadZipFile, KeyError, OSError) as e:
        raise ApkError("הקובץ אינו APK תקין") from e
    return parse_manifest(data)


def _strings(buf: bytes, start: int) -> list[str]:
    _, header_size, _, count, _, flags, strings_start, _ = struct.unpack_from("<HHIIIIII", buf, start)
    utf8 = bool(flags & 0x100)
    offsets = struct.unpack_from(f"<{count}I", buf, start + header_size)
    base = start + strings_start
    out = []
    for off in offsets:
        p = base + off
        if utf8:
            n = buf[p]
            p += 2 if n & 0x80 else 1                       # אורך בתווים (מדלגים)
            n = buf[p]
            if n & 0x80:
                n = ((n & 0x7F) << 8) | buf[p + 1]
                p += 2
            else:
                p += 1
            out.append(buf[p:p + n].decode("utf-8", "replace"))
        else:
            n = struct.unpack_from("<H", buf, p)[0]
            p += 2
            if n & 0x8000:
                n = ((n & 0x7FFF) << 16) | struct.unpack_from("<H", buf, p)[0]
                p += 2
            out.append(buf[p:p + n * 2].decode("utf-16-le", "replace"))
    return out


def parse_manifest(buf: bytes) -> ApkInfo:
    """AXML → פרטי החבילה. טהור — נבדק ביחידה."""
    try:
        if len(buf) < 8 or struct.unpack_from("<H", buf, 0)[0] != 0x0003:
            raise ApkError("המניפסט בקובץ לא תקין")
        strings: list[str] = []
        res_ids: list[int] = []
        info: dict = {}
        pos = struct.unpack_from("<H", buf, 2)[0]
        while pos + 8 <= len(buf):
            ctype, header_size, size = struct.unpack_from("<HHI", buf, pos)
            if size < 8:
                break
            if ctype == 0x0001:
                strings = _strings(buf, pos)
            elif ctype == 0x0180:
                res_ids = list(struct.unpack_from(f"<{(size - header_size) // 4}I", buf, pos + header_size))
            elif ctype == 0x0102:
                name_idx = struct.unpack_from("<I", buf, pos + 20)[0]
                tag = strings[name_idx] if name_idx < len(strings) else ""
                attr_start, attr_size, attr_count = struct.unpack_from("<HHH", buf, pos + 24)
                if tag in ("manifest", "application"):
                    for i in range(attr_count):
                        a = pos + 16 + attr_start + i * attr_size
                        _, aname, raw, _, _, dtype, data = struct.unpack_from("<IIIHBBI", buf, a)
                        key = strings[aname] if aname < len(strings) else ""
                        rid = res_ids[aname] if aname < len(res_ids) else 0
                        value = strings[raw] if raw != 0xFFFFFFFF and raw < len(strings) else None
                        if dtype == _TYPE_STRING and value is None and data < len(strings):
                            value = strings[data]
                        if tag == "manifest" and key == "package":
                            info["package"] = value
                        elif tag == "manifest" and (rid == _RES_VERSION_CODE or key == "versionCode"):
                            info["version_code"] = data if dtype in (_TYPE_INT_DEC, _TYPE_INT_HEX) else int(value or 0)
                        elif tag == "manifest" and (rid == _RES_VERSION_NAME or key == "versionName"):
                            info["version_name"] = value or ""
                        elif tag == "application" and (rid == _RES_LABEL or key == "label") and value:
                            info["label"] = value
                    if tag == "application":
                        break
            pos += size
    except (struct.error, IndexError, UnicodeDecodeError) as e:
        raise ApkError("המניפסט בקובץ לא תקין") from e
    if not info.get("package") or not info.get("version_code"):
        raise ApkError("לא נמצאו שם החבילה וגרסת הקוד בקובץ")
    return ApkInfo(info["package"], int(info["version_code"]), info.get("version_name", ""), info.get("label", ""))
