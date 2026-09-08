"""
Precompute CLIP embeddings once, then iterate on the head for free.

This is the single biggest scheduling decision in a 3-day build. Encoding is
the only expensive step; after it, training a head is seconds, so you can run
thirty ablations instead of three.

Cost model, ViT-L/14 on one modern GPU: roughly 250-400 images/sec at 224px.
20k images x 6 views is ~2-3 GPU-minutes of forward passes, but PIL
augmentation on CPU is usually the bottleneck — give it workers.

    python embed.py --data data/train --out cache/train --views 6
    python embed.py --data data/val   --out cache/val   --views 6
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np

import clipfeat as CF


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="root with real/ and fake/ subdirs")
    ap.add_argument("--out", required=True, help="output cache dir")
    ap.add_argument("--model", default="openai/clip-vit-large-patch14")
    ap.add_argument("--feature", default="proj", choices=["proj", "pooled", "both"])
    ap.add_argument("--preproc", default="resize", choices=["resize", "nativecrop"])
    ap.add_argument("--views", type=int, default=6,
                    help="view 0 is always clean; the rest are random degradation chains")
    ap.add_argument("--max-ops", type=int, default=3)
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--num-workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0, help="cap images per class, for a fast dry run")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--no-renormalise", action="store_true",
                    help="ABLATION ONLY: skip the JPEG re-encode that kills the format shortcut")
    ap.add_argument("--no-amp", action="store_true",
                    help="DIAGNOSTIC: force full fp32 even on GPU. Use this if GPU-embedded "
                         "signal looks weaker than an identical CPU run (e.g. clean AUROC "
                         "near chance during training) -- isolates fp16 precision/kernel "
                         "issues, which are a live concern on very new GPU architectures.")
    args = ap.parse_args()

    samples = CF.scan_dir(args.data)
    if args.limit:
        real = [s for s in samples if s.label == 0][: args.limit]
        fake = [s for s in samples if s.label == 1][: args.limit]
        samples = real + fake

    n_real = sum(1 for s in samples if s.label == 0)
    print(f"[data] {len(samples)} images  real={n_real}  fake={len(samples)-n_real}")
    groups = sorted({s.group for s in samples})
    print(f"[data] groups: {groups}")

    views = [CF.clean_view()] + [CF.random_view(max_ops=args.max_ops) for _ in range(args.views - 1)]

    ds = CF.ViewDataset(
        samples, views,
        preproc=args.preproc,
        renormalise=not args.no_renormalise,
        seed=args.seed,
    )
    model, device = CF.load_clip(args.model)

    t0 = time.time()
    feats, labels = CF.embed(
        model, device, ds,
        feature=args.feature,
        batch_size=args.batch_size,
        num_workers=args.num_workers,
        amp=(False if args.no_amp else None),
    )
    dt = time.time() - t0
    n_fwd = feats.shape[0] * feats.shape[1]
    print(f"[embed] {feats.shape}  {dt:.1f}s  ({n_fwd/max(dt,1e-9):.0f} img/s)")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    np.save(out / "feats.npy", feats)
    np.save(out / "labels.npy", labels.astype(np.int64))
    meta = {
        "data": str(args.data),
        "model": args.model,
        "feature": args.feature,
        "preproc": args.preproc,
        "renormalised": not args.no_renormalise,
        "views": args.views,
        "dim": int(feats.shape[-1]),
        "paths": [s.path for s in samples],
        "group": [s.group for s in samples],
        "label": [s.label for s in samples],
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=2))
    bad = int((labels < 0).sum())
    if bad:
        print(f"[warn] {bad} images failed to load and are labelled -1; train.py drops them")
    print(f"[embed] wrote {out}/feats.npy  ({feats.nbytes/1e6:.1f} MB)")


if __name__ == "__main__":
    main()