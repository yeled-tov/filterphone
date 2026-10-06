"""פריסת גוף תשובה דחוס עם תקרה — הגנה מ"פצצות דחיסה".

mitmproxy פורס gzip/brotli/zstd לזיכרון בלי גבול (get_content). דף של 4MB דחוס יכול
להיפרס לגיגה-בייטים ולהפיל את הפרוקסי לכל המכשירים. כאן פורסים בהדרגה ועוצרים בתקרה:
None = הגוף גדול מהתקרה אחרי פריסה, או שהקידוד לא מוכר/פגום.
"""
from __future__ import annotations

import io
import zlib

CHUNK = 64 * 1024


def _zlib(raw: bytes, limit: int, wbits: int) -> bytes | None:
    d = zlib.decompressobj(wbits)
    out = bytearray()
    view = memoryview(raw)
    for i in range(0, len(view), CHUNK):
        out += d.decompress(view[i:i + CHUNK], limit + 1 - len(out))
        if len(out) > limit:
            return None
        while d.unconsumed_tail:            # נשאר קלט כי הפלט הגיע לתקרה של הקריאה
            out += d.decompress(d.unconsumed_tail, limit + 1 - len(out))
            if len(out) > limit:
                return None
    out += d.flush(limit + 1 - len(out))
    return None if len(out) > limit else bytes(out)


def _deflate(raw: bytes, limit: int) -> bytes | None:
    # "deflate" ב-HTTP אמור להיות zlib, אבל יש שרתים ששולחים deflate גולמי
    try:
        return _zlib(raw, limit, zlib.MAX_WBITS)
    except zlib.error:
        return _zlib(raw, limit, -zlib.MAX_WBITS)


def _brotli(raw: bytes, limit: int) -> bytes | None:
    import brotli

    d = brotli.Decompressor()
    out = bytearray()
    try:
        out += d.process(raw, output_buffer_limit=limit + 1)
        while len(out) <= limit and not d.is_finished() and not d.can_accept_more_data():
            out += d.process(b"", output_buffer_limit=limit + 1 - len(out))
    except TypeError:               # brotli ישן בלי output_buffer_limit — לא פורסים בלי תקרה
        return None
    if len(out) > limit or not d.is_finished():     # גדול מדי, או זרם קטוע
        return None
    return bytes(out)


def _zstd(raw: bytes, limit: int) -> bytes | None:
    import zstandard

    out = bytearray()
    with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(raw), read_across_frames=True) as r:
        while len(out) <= limit:
            chunk = r.read(min(CHUNK, limit + 1 - len(out)))
            if not chunk:
                break
            out += chunk
    return None if len(out) > limit else bytes(out)


_DECODERS = {
    "gzip": lambda raw, limit: _zlib(raw, limit, 16 + zlib.MAX_WBITS),
    "x-gzip": lambda raw, limit: _zlib(raw, limit, 16 + zlib.MAX_WBITS),
    "deflate": _deflate,
    "br": _brotli,
    "zstd": _zstd,
}


def decode_bounded(raw: bytes | None, encoding: str, limit: int) -> bytes | None:
    """→ הגוף אחרי פריסה, או None אם עובר את התקרה / קידוד לא מוכר / פגום."""
    if raw is None:
        return None
    enc = (encoding or "").strip().lower()
    if not enc or enc == "identity":
        return raw if len(raw) <= limit else None
    if "," in enc:                  # כמה קידודים בשרשרת — נדיר מאוד; לא מנסים
        return None
    decoder = _DECODERS.get(enc)
    if decoder is None:
        return None
    try:
        return decoder(raw, limit)
    except Exception:               # קובץ דחוס פגום (zlib.error / brotli.error / ZstdError)
        return None


def bounded_content(message, limit: int) -> bytes | None:
    """כמו message.get_content(), אבל עם תקרה אחרי פריסה (http.Message של mitmproxy)."""
    return decode_bounded(message.raw_content, message.headers.get("content-encoding", ""), limit)
