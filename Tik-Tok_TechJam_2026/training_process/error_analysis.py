"""
Find every misclassified image and let you actually look at it.

The single most useful thing you can do to push accuracy from 0.75 toward
0.90 is not another hyperparameter sweep -- it's looking at what the model
currently gets wrong. A pattern you can SEE (all false positives are heavily
filtered phone photos; all false negatives are one specific generator) tells
you exactly what to fix. A pattern buried in a metrics table doesn't.

Outputs, under --out:
    manifest.json         every misclassified image: path, true label,
                          predicted label, raw score, calibrated probability
    gallery.html          open this in a browser -- thumbnails of every
                          miss, sorted worst-first (most confidently wrong)
    false_positives/      copies of real images the model called fake
    false_negatives/      copies of fake images the model called real

    python error_analysis.py --data data/test --ckpt runs/cvar/head.pt \
                             --out errors/test
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import shutil
from pathlib import Path

import numpy as np
import torch
from PIL import Image

import clipfeat as CF
from train import Head


def thumb_b64(path: str, size: int = 160) -> str:
    """Small base64 JPEG so the HTML gallery is one self-contained file."""
    try:
        img = Image.open(path).convert("RGB")
        img.thumbnail((size, size))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=80)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return ""


def build_gallery(rows: list[dict], out_path: Path, title: str) -> None:
    cards = []
    for r in rows:
        b64 = thumb_b64(r["path"])
        if not b64:
            continue
        kind = "False Positive (real called fake)" if r["true"] == 0 else \
              "False Negative (fake called real)"
        color = "#c0392b" if r["true"] == 0 else "#2980b9"
        cards.append(f"""
        <div class="card">
          <img src="data:image/jpeg;base64,{b64}">
          <div class="meta">
            <div class="kind" style="color:{color}">{kind}</div>
            <div>score: {r['score']:.4f}</div>
            <div>P(fake): {r['prob']:.3f}</div>
            <div class="path">{Path(r['path']).name}</div>
          </div>
        </div>""")

    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{title}</title>
<style>
  body {{ font-family: system-ui, sans-serif; background: #1a1a1a; color: #eee; margin: 20px; }}
  h1 {{ font-size: 18px; }}
  .grid {{ display: flex; flex-wrap: wrap; gap: 12px; }}
  .card {{ background: #2a2a2a; border-radius: 6px; overflow: hidden; width: 180px; }}
  .card img {{ display: block; width: 100%; height: 160px; object-fit: cover; }}
  .meta {{ padding: 8px; font-size: 12px; }}
  .kind {{ font-weight: 600; margin-bottom: 4px; }}
  .path {{ color: #888; word-break: break-all; margin-top: 4px; }}
</style></head>
<body>
  <h1>{title} — {len(rows)} misclassified, sorted most-confidently-wrong first</h1>
  <div class="grid">{''.join(cards)}</div>
</body></html>"""
    out_path.write_text(html, encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="root with real/ and fake/ subdirs")
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--num-workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--max-copy", type=int, default=100,
                    help="cap on how many actual image files to copy per class "
                         "(the HTML gallery and manifest always cover everything)")
    args = ap.parse_args()

    ck = torch.load(args.ckpt, map_location="cpu", weights_only=False)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    head = Head(ck["dim"], ck["config"]["head"], ck["config"]["hidden"], ck["config"]["dropout"])
    head.load_state_dict(ck["state_dict"])
    head.eval().to(device)
    mu, sd, thr = ck["mu"], ck["sd"], ck["threshold"]
    pa, pb = ck.get("platt_a", 1.0), ck.get("platt_b", 0.0)
    print(f"[ckpt] dim={ck['dim']} thr={thr:.4f}")

    samples = CF.scan_dir(args.data)
    if args.limit:
        real = [s for s in samples if s.label == 0][: args.limit]
        fake = [s for s in samples if s.label == 1][: args.limit]
        samples = real + fake

    ds = CF.ViewDataset(samples, [CF.clean_view()], preproc=ck["preproc"],
                        renormalise=True, seed=777)
    model, _ = CF.load_clip(ck["clip_model"], device)
    feats, labels = CF.embed(model, device, ds, feature=ck["feature"],
                             batch_size=args.batch_size, num_workers=args.num_workers)

    ok = labels >= 0
    paths = [s.path for s, k in zip(samples, ok) if k]
    y = labels[ok]
    X = feats[ok, 0].astype(np.float32)     # clean view only
    if ck["l2"]:
        X = X / (np.linalg.norm(X, axis=-1, keepdims=True) + 1e-8)
    X = (X - mu) / sd

    with torch.no_grad():
        scores = np.concatenate([
            head(torch.from_numpy(X[i:i + 4096]).to(device)).cpu().numpy()
            for i in range(0, len(X), 4096)
        ])
    probs = 1.0 / (1.0 + np.exp(-(pa * scores + pb)))
    preds = (scores >= thr).astype(int)

    wrong = preds != y
    rows = [
        {"path": paths[i], "true": int(y[i]), "pred": int(preds[i]),
         "score": float(scores[i]), "prob": float(probs[i]),
         "confidence": abs(float(scores[i]) - thr)}
        for i in range(len(y)) if wrong[i]
    ]
    rows.sort(key=lambda r: -r["confidence"])   # worst (most confidently wrong) first

    fp = [r for r in rows if r["true"] == 0]    # real called fake
    fn = [r for r in rows if r["true"] == 1]    # fake called real
    n_real, n_fake = int((y == 0).sum()), int((y == 1).sum())
    print(f"\ntotal: {len(y)} (real={n_real} fake={n_fake})")
    print(f"misclassified: {len(rows)}  ({len(fp)} false positives, {len(fn)} false negatives)")
    print(f"accuracy: {1 - len(rows)/len(y):.4f}")
    print(f"FP rate (of real): {len(fp)/max(n_real,1):.4f}   "
         f"FN rate (of fake): {len(fn)/max(n_fake,1):.4f}")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "manifest.json").write_text(json.dumps(rows, indent=2))
    build_gallery(rows, out / "gallery.html", f"Misclassified — {args.data}")

    for name, group in [("false_positives", fp), ("false_negatives", fn)]:
        d = out / name
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
        for i, r in enumerate(group[: args.max_copy]):
            try:
                ext = Path(r["path"]).suffix or ".jpg"
                shutil.copy2(r["path"], d / f"{i:04d}_score{r['score']:.3f}{ext}")
            except Exception as e:
                print(f"  [skip copy] {r['path']}: {e}")

    print(f"\n[save] {out}/manifest.json")
    print(f"[save] {out}/gallery.html  <- open this in a browser")
    print(f"[save] {out}/false_positives/  ({min(len(fp), args.max_copy)} files)")
    print(f"[save] {out}/false_negatives/  ({min(len(fn), args.max_copy)} files)")


if __name__ == "__main__":
    main()