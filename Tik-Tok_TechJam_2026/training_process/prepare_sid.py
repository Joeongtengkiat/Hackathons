"""
Prepare SID_Set (saberzl/SID_Set) for the detector pipeline.

SCOPE: this project is BINARY — real (label 0) vs fully synthetic (label 1).
Label 2 (tampered) is a locally-edited real image with a mask. It is a
different task (forgery localisation) and is NEVER trained on. It is written
to a separate root and used only as an evaluation probe.

THE SHORTCUT. In SID_Set every full_synthetic and tampered image is 1024x1024,
while the real OpenImages photos are mostly non-square (1024x683, 768x1024,
1024x492 ...). The rule `width == height => fake` scores in the 90s on the raw
data. Any model trained on the raw data learns that instead of detection.
`normalise_geometry` removes it by random-square-cropping and random-resizing
BOTH classes to the same distribution. Run --audit first to measure it.

    pip install datasets

    # 0. measure the shortcut (2 min, this is a results slide)
    python prepare_sid.py --audit --n 3000

    # 1. binary training data
    python prepare_sid.py --split train      --out data/train --per-class 25000
    python prepare_sid.py --split validation --out data/val   --per-class 3000

    # 2. in-distribution test, disjoint from train via --offset
    python prepare_sid.py --split train --out data/test --per-class 4000 --offset 25000

    # 3. tampered probe (BONUS — evaluation only, never trained on)
    python prepare_sid.py --split validation --out data/probe --per-class 2000 --tampered
"""
from __future__ import annotations

import argparse
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from PIL import Image

from _geom import apply_geometry, geometry_params

LABEL_DIR = {
    0: ("real", "openimages"),
    1: ("fake", "full_synthetic"),
    2: ("fake", "tampered"),
}


# --------------------------------------------------------------------------
# audit
# --------------------------------------------------------------------------


def audit(ds, n: int) -> None:
    shape = defaultdict(Counter)
    square = Counter()
    total = Counter()
    for i, row in enumerate(ds):
        if i >= n:
            break
        lab, w, h = row["label"], row["width"], row["height"]
        total[lab] += 1
        square[lab] += int(w == h)
        shape[lab][f"{w}x{h}"] += 1

    names = {0: "real", 1: "synthetic", 2: "tampered"}
    print(f"\n{'label':<12}{'n':>7}{'square':>9}{'% square':>10}   most common sizes")
    print("-" * 80)
    for lab in sorted(total):
        pct = 100 * square[lab] / total[lab]
        top = ", ".join(f"{k}({v})" for k, v in shape[lab].most_common(3))
        print(f"{lab} {names.get(lab,''):<10}{total[lab]:>7}{square[lab]:>9}{pct:>9.1f}%   {top}")

    n_real, n_fake = total[0], total[1] + total[2]
    if n_real and n_fake:
        # balanced accuracy of the trivial rule "square => fake"
        tpr = (square[1] + square[2]) / n_fake
        tnr = 1 - square[0] / n_real
        print(f"\n  trivial rule 'square => fake': balanced accuracy {0.5*(tpr+tnr):.1%}")
        print("  anything well above 50% means --normalise-geometry is mandatory.\n")


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="train", choices=["train", "validation"])
    ap.add_argument("--out", default="data/train")
    ap.add_argument("--per-class", type=int, default=25000)
    ap.add_argument("--offset", type=int, default=0,
                    help="skip this many matching rows per class first — use it to carve "
                         "disjoint train/test slices out of the same split")
    ap.add_argument("--tampered", action="store_true",
                    help="write label 2 instead of label 1 (BONUS probe set, never train on it)")
    ap.add_argument("--audit", action="store_true", help="report shortcut stats and exit")
    ap.add_argument("--n", type=int, default=3000, help="rows to scan in audit mode")
    ap.add_argument("--no-normalise-geometry", action="store_true",
                    help="ABLATION ONLY: leave the aspect/resolution shortcut in place")
    ap.add_argument("--quality", type=int, default=92)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    from datasets import load_dataset

    ds = load_dataset("saberzl/SID_Set", split=args.split, streaming=True)

    if args.audit:
        if "mask" in ds.column_names:
            ds = ds.remove_columns(["mask"])
        audit(ds, args.n)
        return

    # masks only matter for the tampered probe — dropping the column avoids decoding them
    need_mask = args.tampered
    if not need_mask and "mask" in ds.column_names:
        ds = ds.remove_columns(["mask"])

    fake_label = 2 if args.tampered else 1
    wanted = {0, fake_label}
    py_rng = random.Random(args.seed)
    out = Path(args.out)
    for lab in wanted:
        d, sub = LABEL_DIR[lab]
        (out / d / sub).mkdir(parents=True, exist_ok=True)

    seen = Counter()      # rows of each class encountered (for --offset)
    kept = Counter()
    skipped = 0
    mask_frac: dict[str, float] = {}

    for row in ds:
        lab = row["label"]
        if lab not in wanted:
            continue
        seen[lab] += 1
        if seen[lab] <= args.offset:
            continue
        if kept[lab] >= args.per_class:
            if all(kept[k] >= args.per_class for k in wanted):
                break
            continue

        try:
            img = row["image"].convert("RGB")
            if args.no_normalise_geometry:
                params = None
            else:
                params = geometry_params(*img.size, py_rng, lo=512)
                img = apply_geometry(img, params)

            d, sub = LABEL_DIR[lab]
            name = f"{row['img_id']}.jpg"
            img.save(out / d / sub / name, format="JPEG", quality=args.quality)

            # record how much of the frame was actually edited — the probe bins on this
            if need_mask and lab == 2 and row.get("mask") is not None:
                m = row["mask"].convert("L")
                if params is not None:
                    m = apply_geometry(m, params, resample=Image.Resampling.NEAREST)
                mask_frac[name] = float((np.asarray(m) > 127).mean())

            kept[lab] += 1
        except Exception:
            skipped += 1
            continue

        if sum(kept.values()) % 500 == 0:
            print(f"  kept={dict(kept)}", flush=True)

    if mask_frac:
        (out / "mask_fraction.json").write_text(json.dumps(mask_frac, indent=2))
        v = np.array(list(mask_frac.values()))
        print(f"\n[mask] edited-region fraction: median {np.median(v):.3f}  "
              f"p10 {np.quantile(v,0.1):.3f}  p90 {np.quantile(v,0.9):.3f}")

    print(f"\nwrote {dict(kept)} to {out}  (skipped {skipped})")
    if args.no_normalise_geometry:
        print("WARNING: geometry shortcut left intact. Ablation use only.")


if __name__ == "__main__":
    main()