"""בניית ראשי הסיווג של מנוע הסינון (server/filterphone/engine/heads.json).

רץ ב-GitHub Actions (workflow בשם heads) — לא בשרת.

מקורות:
  * person / female / immodest — רגרסיה לוגיסטית על embeddings של SigLIP2,
    עם תוויות מ-COCO 2017: אדם — מתיוג האובייקטים; אישה ולבוש חשוף — מהכיתובים
    (5 כיתובים אנושיים לכל תמונה: "a woman in a bikini...").
  * nsfw — zero-shot מתיאורי טקסט בלבד (אין צורך בתוכן מפורש). הוא רק "שער":
    ההחלטה הסופית על עירום היא של NudeNet.

הספים נקבעים על קבוצת בדיקה נפרדת (val2017) לפי יעדי דיוק/כיסוי.

שימוש:
    python tools/build_heads.py --work /tmp/heads --train-images 24000
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import functools
import gc
import json
import os
import random
import re
import sys
import urllib.request
import zipfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from filterphone.engine.heads import SCORE_NAMES, encode_weights  # noqa: E402
from filterphone.engine.imaging import decode, siglip_views  # noqa: E402

sys.path.insert(0, str(ROOT / "tools"))
import fetch_models  # noqa: E402

PT_REPO = "google/siglip2-base-patch32-256"
EMBEDDER_NAME = "siglip2-base-patch32-256"
COCO = "http://images.cocodataset.org"
SIZE = 256

FEMALE_RE = re.compile(r"\b(woman|women|girl|girls|lady|ladies|female|females|mother|mom|bride|"
                       r"daughter|daughters|sister|sisters|wife|queen|actress|waitress|grandmother|"
                       r"she|her|hers|herself)\b")
MALE_RE = re.compile(r"\b(man|men|boy|boys|guy|guys|gentleman|gentlemen|male|males|father|dad|"
                     r"groom|son|sons|brother|brothers|husband|king|he|his|him|himself|businessman)\b")
IMMODEST_RE = re.compile(r"\b(bikini|bikinis|swimsuit|swimsuits|swim suit|bathing suit|bathing suits|"
                         r"swimwear|lingerie|underwear|bra|topless|shirtless|no shirt|without a shirt|"
                         r"speedo|swim trunks|miniskirt|short shorts|tank top)\b")

NSFW_POSITIVE = [
    "a photo of a naked person", "a nude woman", "a nude man", "a pornographic image",
    "explicit sexual content", "a topless woman", "a naked body", "nudity",
    "an erotic photo", "people having sex",
]
NSFW_NEGATIVE = [
    "a photo of a person wearing clothes", "a photo of people", "a photo of an object",
    "a landscape photo", "a photo of food", "a photo of an animal", "a screenshot of text",
    "a cartoon illustration", "a photo of a woman in a dress", "a photo of a man in a suit",
    "a photo of a person in a swimsuit at the beach",
]


# ------------------------------------------------------------------ הורדות

def fetch(url: str, dest: Path) -> Path:
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=120) as r, open(tmp, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
    tmp.replace(dest)
    return dest


def coco_annotations(work: Path) -> dict:
    """מחזיר לכל split: {image_id: {"file", "w", "h", "captions", "person_area"}}."""
    z = fetch(f"{COCO}/annotations/annotations_trainval2017.zip", work / "coco" / "annotations.zip")
    out: dict[str, dict[int, dict]] = {}
    with zipfile.ZipFile(z) as zf:
        for split in ("train2017", "val2017"):
            caps = json.loads(zf.read(f"annotations/captions_{split}.json"))
            # קובץ האנשים בלבד (חצי מגודל קובץ כל האובייקטים) — חשוב במכונות עם 7GB זיכרון
            inst = json.loads(zf.read(f"annotations/person_keypoints_{split}.json"))
            imgs = {im["id"]: {"file": im["file_name"], "w": im["width"], "h": im["height"],
                               "captions": [], "person_area": 0.0} for im in caps["images"]}
            for a in caps["annotations"]:
                imgs[a["image_id"]]["captions"].append(a["caption"].lower())
            for a in inst["annotations"]:
                if a["category_id"] == 1 and a["image_id"] in imgs:
                    im = imgs[a["image_id"]]
                    im["person_area"] = max(im["person_area"], a["area"] / (im["w"] * im["h"]))
            out[split] = imgs
            del caps, inst
            gc.collect()
    return out


# ------------------------------------------------------------------ תוויות

def labels_for(im: dict) -> dict[str, int | None]:
    """None = לא ודאי — לא נכנס לאימון של אותו ראש."""
    text = " ".join(im["captions"])
    has_person = im["person_area"] >= 0.01
    tiny_person_only = 0 < im["person_area"] < 0.01
    female = bool(FEMALE_RE.search(text))
    male = bool(MALE_RE.search(text))
    immodest = bool(IMMODEST_RE.search(text))

    person = 1 if has_person else (None if tiny_person_only else 0)
    if not has_person:
        f = None if tiny_person_only else 0
    elif female:
        f = 1
    elif male:
        f = 0
    else:
        f = None                        # "a person skiing" — מגדר לא ידוע
    if immodest and has_person:
        imm = 1
    elif has_person:
        imm = 0
    else:
        imm = None if tiny_person_only else 0
    return {"person": person, "female": f, "immodest": imm}


def pick_train(imgs: dict[int, dict], n: int, seed: int = 7) -> list[int]:
    """מדגם מאוזן: כל תמונות הלבוש החשוף, ושליש-שליש נשים / גברים / בלי אנשים."""
    rnd = random.Random(seed)
    groups: dict[str, list[int]] = {"immodest": [], "female": [], "male": [], "none": []}
    for iid, im in imgs.items():
        lab = labels_for(im)
        if lab["immodest"] == 1:
            groups["immodest"].append(iid)
        elif lab["female"] == 1:
            groups["female"].append(iid)
        elif lab["female"] == 0 and lab["person"] == 1:
            groups["male"].append(iid)
        elif lab["person"] == 0:
            groups["none"].append(iid)
    chosen = list(groups["immodest"])
    per = max(0, (n - len(chosen)) // 3)
    for g in ("female", "male", "none"):
        rnd.shuffle(groups[g])
        chosen += groups[g][:per]
    print("train groups:", {k: len(v) for k, v in groups.items()}, "chosen:", len(chosen))
    return chosen


# ------------------------------------------------------------------ embeddings

class VisionEncoder:
    def __init__(self, model_path: Path, threads: int):
        import onnxruntime as ort
        so = ort.SessionOptions()
        so.intra_op_num_threads = threads
        self.s = ort.InferenceSession(str(model_path), so, providers=["CPUExecutionProvider"])
        self.inp = self.s.get_inputs()[0].name

    def __call__(self, batch: np.ndarray) -> np.ndarray:
        out = self.s.run(["pooler_output"], {self.inp: batch})[0]
        return out / np.linalg.norm(out, axis=1, keepdims=True)


def embed_split(split: str, ids: list[int], imgs: dict[int, dict], enc: VisionEncoder,
                work: Path, cache: Path, batch: int = 32) -> tuple[np.ndarray, np.ndarray]:
    if cache.exists():
        d = np.load(cache)
        return d["ids"], d["emb"]

    def load(iid: int):
        url = f"{COCO}/{split}/{imgs[iid]['file']}"
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                data = r.read()
        except Exception:
            return iid, None
        dec = decode(data, 1024)            # בדיוק כמו בשרת
        if dec is None:
            return iid, None
        return iid, siglip_views(dec.primary, SIZE)[0]

    # בחלקים: pool.map שולח הכול מראש, ותוצאות שלא נצרכו מצטברות בזיכרון (~0.8MB לתמונה)
    done_ids, embs = [], []
    chunk = 512
    with cf.ThreadPoolExecutor(16) as pool:
        for start in range(0, len(ids), chunk):
            loaded = [(iid, x) for iid, x in pool.map(load, ids[start:start + chunk]) if x is not None]
            for b0 in range(0, len(loaded), batch):
                part = loaded[b0:b0 + batch]
                embs.append(enc(np.stack([x for _, x in part])))
                done_ids += [iid for iid, _ in part]
            print(f"  {split}: {min(start + chunk, len(ids))}/{len(ids)}", flush=True)
    ids_arr, emb = np.asarray(done_ids), np.concatenate(embs).astype(np.float32)
    np.savez_compressed(cache, ids=ids_arr, emb=emb)
    return ids_arr, emb


# ------------------------------------------------------------------ טקסט (zero-shot)

@functools.lru_cache(maxsize=1)
def _text_model():
    from transformers import AutoModel, AutoProcessor
    return AutoModel.from_pretrained(PT_REPO).eval(), AutoProcessor.from_pretrained(PT_REPO)


def text_space(prompts: list[str]) -> tuple[np.ndarray, float, float]:
    """embeddings של טקסט מהמודל הרשמי (PyTorch) + logit_scale/bias שלו."""
    import torch
    model, proc = _text_model()
    with torch.no_grad():
        t = proc(text=[p.lower() for p in prompts], padding="max_length", max_length=64,
                 return_tensors="pt")
        e = model.get_text_features(**t)
        if not isinstance(e, torch.Tensor):     # בגרסאות חדשות של transformers מוחזר אובייקט
            e = e.pooler_output
        e = torch.nn.functional.normalize(e, dim=-1).numpy()
        return e, float(model.logit_scale.exp()), float(model.logit_bias)


ALIGNMENT_PROBES = [("000000000139.jpg", "a living room"), ("000000000285.jpg", "a bear"),
                    ("000000000632.jpg", "a bedroom"), ("000000000724.jpg", "a stop sign")]


def all_text_embeddings() -> tuple[np.ndarray, np.ndarray, float, float]:
    """כל הטקסט בבת אחת, ואז משחררים את מודל הטקסט מהזיכרון (~1.5GB)."""
    probes = [f"a photo of {label}" for _, label in ALIGNMENT_PROBES]
    t, scale, bias = text_space(probes + NSFW_POSITIVE + NSFW_NEGATIVE)
    _text_model.cache_clear()
    gc.collect()
    return t[:len(probes)], t[len(probes):], scale, bias


def check_alignment(enc: VisionEncoder, t: np.ndarray, scale: float, bias: float) -> None:
    """וידוא שה-ONNX של הראייה וה-PyTorch של הטקסט באותו מרחב, על תמונות ידועות."""
    probes = ALIGNMENT_PROBES
    xs = []
    for fname, _ in probes:
        with urllib.request.urlopen(f"{COCO}/val2017/{fname}", timeout=60) as r:
            xs.append(siglip_views(decode(r.read(), 1024).primary, SIZE)[0])
    sims = enc(np.stack(xs)) @ t.T
    correct = int((sims.argmax(axis=1) == np.arange(len(probes))).sum())
    print(f"alignment check: {correct}/{len(probes)} correct, scale={scale:.2f} bias={bias:.2f}")
    if correct < len(probes) - 1:
        raise SystemExit("vision/text embeddings are not aligned — wrong model or preprocessing")


# ------------------------------------------------------------------ אימון

def train_head(X: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, float, float]:
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import train_test_split
    Xa, Xb, ya, yb = train_test_split(X, y, test_size=0.15, random_state=1, stratify=y)
    best = None
    for C in (0.3, 1.0, 3.0, 10.0):
        m = LogisticRegression(C=C, max_iter=3000, class_weight="balanced").fit(Xa, ya)
        ll = -np.mean(np.log(np.clip(m.predict_proba(Xb)[np.arange(len(yb)), yb], 1e-6, 1)))
        if best is None or ll < best[0]:
            best = (ll, C)
    m = LogisticRegression(C=best[1], max_iter=3000, class_weight="balanced").fit(X, y)
    return m.coef_[0].astype(np.float32), float(m.intercept_[0]), best[1]


def sigmoid(z):
    return 1.0 / (1.0 + np.exp(-z))


def threshold_for_recall(p: np.ndarray, y: np.ndarray, recall: float) -> float:
    pos = np.sort(p[y == 1])
    return float(pos[int((1 - recall) * len(pos))]) if len(pos) else 0.5


def threshold_for_precision(p: np.ndarray, y: np.ndarray, precision: float) -> float:
    order = np.argsort(-p)
    tp = np.cumsum(y[order] == 1)
    prec = tp / np.arange(1, len(order) + 1)
    ok = np.where(prec >= precision)[0]
    return float(p[order][ok[-1]]) if len(ok) else 0.99


def metrics(p: np.ndarray, y: np.ndarray, thr: float) -> dict:
    from sklearn.metrics import roc_auc_score
    pred = p >= thr
    tp = int((pred & (y == 1)).sum()); fp = int((pred & (y == 0)).sum())
    fn = int((~pred & (y == 1)).sum())
    return {"auc": round(float(roc_auc_score(y, p)), 4),
            "threshold": round(thr, 4),
            "precision": round(tp / max(tp + fp, 1), 4),
            "recall": round(tp / max(tp + fn, 1), 4),
            "n": int(len(y)), "positives": int((y == 1).sum())}


# ------------------------------------------------------------------ main

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", type=Path, default=Path("/tmp/fp-heads"))
    ap.add_argument("--train-images", type=int, default=24000)
    ap.add_argument("--threads", type=int, default=os.cpu_count() or 2)
    ap.add_argument("--out", type=Path, default=ROOT / "server/filterphone/engine/heads.json")
    ap.add_argument("--report", type=Path, default=ROOT / "docs/HEADS_REPORT.md")
    args = ap.parse_args()
    work: Path = args.work
    work.mkdir(parents=True, exist_ok=True)

    model_path = fetch_models.fetch("siglip2-b32-256.onnx", work / "models")
    enc = VisionEncoder(model_path, args.threads)
    probe_t, nsfw_t, scale, bias = all_text_embeddings()
    check_alignment(enc, probe_t, scale, bias)

    ann = coco_annotations(work)
    train_ids = pick_train(ann["train2017"], args.train_images)
    val_ids = sorted(ann["val2017"])
    tr_ids, tr_emb = embed_split("train2017", train_ids, ann["train2017"], enc, work, work / "train.npz")
    va_ids, va_emb = embed_split("val2017", val_ids, ann["val2017"], enc, work, work / "val.npz")
    tr_lab = [labels_for(ann["train2017"][int(i)]) for i in tr_ids]
    va_lab = [labels_for(ann["val2017"][int(i)]) for i in va_ids]

    heads: dict[str, dict] = {}
    report: dict[str, dict] = {}
    val_probs: dict[str, np.ndarray] = {}
    for name in ("person", "female", "immodest"):
        mtr = np.array([l[name] is not None for l in tr_lab])
        ytr = np.array([l[name] or 0 for l in tr_lab])[mtr]
        w, b, C = train_head(tr_emb[mtr], ytr)
        heads[name] = {"w": encode_weights(w), "b": round(b, 6), "source": f"logreg-coco C={C}"}
        mva = np.array([l[name] is not None for l in va_lab])
        yva = np.array([l[name] or 0 for l in va_lab])[mva]
        pva = sigmoid(va_emb[mva] @ w + b)
        val_probs[name] = (pva, yva)
        print(f"{name}: train n={mtr.sum()} pos={ytr.sum()} | val n={mva.sum()} pos={yva.sum()}")

    # --- nsfw: zero-shot ---
    t = nsfw_t
    pos = t[:len(NSFW_POSITIVE)].mean(0); pos /= np.linalg.norm(pos)
    neg = t[len(NSFW_POSITIVE):].mean(0); neg /= np.linalg.norm(neg)
    w_nsfw = (scale * (pos - neg)).astype(np.float32)
    z_all = np.concatenate([tr_emb, va_emb]) @ w_nsfw
    # הטיה כך ש-98% מתמונות COCO (נקיות) יהיו מתחת ל-0.15 → רק ~2% נשלחות לאימות NudeNet
    b_nsfw = float(np.log(0.15 / 0.85) - np.quantile(z_all, 0.98))
    p_all = sigmoid(z_all + b_nsfw)
    heads["nsfw"] = {"w": encode_weights(w_nsfw), "b": round(b_nsfw, 6), "source": "zero-shot"}

    # --- ספים ---
    pf, yf = val_probs["female"]; pi, yi = val_probs["immodest"]; pp, yp = val_probs["person"]
    thresholds = {
        "person": round(threshold_for_recall(pp, yp, 0.95), 4),
        "female_strict": round(threshold_for_precision(pf, yf, 0.90), 4),
        "female_check": round(threshold_for_recall(pf, yf, 0.97), 4),
        "female": round(threshold_for_recall(pf, yf, 0.90), 4),
        "immodest": round(threshold_for_recall(pi, yi, 0.85), 4),
        "immodest_high": round(threshold_for_precision(pi, yi, 0.80), 4),
        "nsfw_check": 0.15,
        "nsfw_block": round(float(min(0.995, max(p_all.max() * 1.02, 0.97))), 4),
    }
    report["person (recall 95%)"] = metrics(pp, yp, thresholds["person"])
    report["female_check (recall 97%)"] = metrics(pf, yf, thresholds["female_check"])
    report["female (recall 90%)"] = metrics(pf, yf, thresholds["female"])
    report["female_strict (precision 90%)"] = metrics(pf, yf, thresholds["female_strict"])
    report["immodest (recall 85%)"] = metrics(pi, yi, thresholds["immodest"])
    report["immodest_high (precision 80%)"] = metrics(pi, yi, thresholds["immodest_high"])
    report["nsfw gate on COCO (clean images)"] = {
        "sent_to_detector": round(float((p_all >= 0.15).mean()), 4),
        "max_score": round(float(p_all.max()), 4)}

    version = dt.datetime.now(dt.UTC).strftime("%Y%m%d-%H%M")
    doc = {"format": 1, "version": f"{EMBEDDER_NAME}-{version}", "embedder": EMBEDDER_NAME,
           "dim": int(tr_emb.shape[1]), "heads": {k: heads[k] for k in SCORE_NAMES},
           "thresholds": thresholds, "metrics": report}
    args.out.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    lines = [f"# דוח ראשי סיווג — {doc['version']}", "",
             f"embedder: `{EMBEDDER_NAME}`, אימון: {len(tr_ids)} תמונות COCO train2017, "
             f"בדיקה: {len(va_ids)} תמונות val2017.", "",
             "| בדיקה | AUC | סף | דיוק | כיסוי | n | חיוביות |", "|---|---|---|---|---|---|---|"]
    for k, m in report.items():
        if "auc" in m:
            lines.append(f"| {k} | {m['auc']} | {m['threshold']} | {m['precision']} | {m['recall']} | {m['n']} | {m['positives']} |")
        else:
            lines.append(f"| {k} | — | — | — | — | {m} | |")
    args.report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=1))
    print("wrote", args.out)


if __name__ == "__main__":
    main()
