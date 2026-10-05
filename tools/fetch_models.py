"""הורדת המודלים של המנוע, עם אימות חתימה (SHA-256). ספרייה סטנדרטית בלבד.

    python tools/fetch_models.py /var/lib/filterphone/models

הגרסאות מקובעות: קובץ שהשתנה במקור לא יעבור אימות ולא ייטען.
"""
from __future__ import annotations

import hashlib
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

SIGLIP_REVISION = "7efccb86b5b6601bfe9e14326e1c11b40b68d44c"

MODELS = {
    # SigLIP2 base/32 @256 — Google, רישיון Apache-2.0
    "siglip2-b32-256.onnx": {
        "url": "https://huggingface.co/onnx-community/siglip2-base-patch32-256-ONNX/resolve/"
               f"{SIGLIP_REVISION}/onnx/vision_model.onnx",
        "sha256": "0ddb7507cfcb54c2cd503029949add9dc6586de67164bb69c2c09588cdcc9719",
    },
    # MediaPipe selfie-multiclass 256×256 — Google, רישיון Apache-2.0. הסגמנטציה של רמה 2.
    # המרה ל-ONNX של הקובץ הרשמי (selfie_multiclass_256x256.tflite, גרסה 1,
    # sha256 c6748b1253a99067ef71f7e26ca71096cd449baefa8f101900ea23016507e0e0).
    # נבדק מול ה-TFLite המקורי: הפרש מרבי 6e-5, התאמה מלאה בתווית של כל פיקסל.
    "selfie-multiclass-256.onnx": {
        "url": "https://huggingface.co/senty-au/selfie_multiclass_256x256-ONNX/resolve/"
               "6db8421a7150ac20558f2c24675078eb3a1a04d0/onnx/model.onnx",
        "sha256": "35ec1ecd9ee7f85073c99c00020b7f6751b69506eeacf683bc8665f6117f85b0",
    },
    # NudeNet 320n — מתוך החבילה הרשמית ב-PyPI (nudenet 3.4.2)
    "nudenet-320n.onnx": {
        "url": "https://files.pythonhosted.org/packages/1c/ee/1aa02d44ba958cc77e16ff1e41a0aac5e721037db7bf62b9c9d124917f87/nudenet-3.4.2-py3-none-any.whl",
        "archive_sha256": "5937dbd84e5d8e5de038f08ffea5a1bb50a08475776bf2b4795914ce0eaf0331",
        "member": "nudenet/320n.onnx",
        "sha256": "c15d8273adad2d0a92f014cc69ab2d6c311a06777a55545f2c4eb46f51911f0f",
    },
}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _download(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=300) as r:
        return r.read()


def fetch(name: str, dest_dir: Path) -> Path:
    spec = MODELS[name]
    dest = dest_dir / name
    if dest.exists() and _sha256(dest) == spec["sha256"]:
        return dest
    data = _download(spec["url"])
    if "member" in spec:
        if hashlib.sha256(data).hexdigest() != spec["archive_sha256"]:
            raise SystemExit(f"{name}: archive checksum mismatch")
        data = zipfile.ZipFile(io.BytesIO(data)).read(spec["member"])
    if hashlib.sha256(data).hexdigest() != spec["sha256"]:
        raise SystemExit(f"{name}: checksum mismatch — refusing to install")
    dest_dir.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    tmp.write_bytes(data)
    tmp.replace(dest)
    return dest


def main() -> None:
    dest_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "models")
    for name in MODELS:
        path = fetch(name, dest_dir)
        print(f"ok  {name}  ({path.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
