"""
Prepare MS COCOAI / Defactify (Rajarshi-Roy-research/Defactify_Image_Dataset).

Why this dataset is a good fit: it is SEMANTICALLY ALIGNED. Every synthetic
image is generated from the same MS COCO caption as its real counterpart, so
real and fake depict the same scenes. That structurally removes the content
/ framing shortcut -- unlike datasets where fakes are aesthetic portraits and
reals are candid snapshots, and the model can separate them on subject matter
alone without learning anything about generation.

It also carries per-generator labels (label_2: SD 3, SD 2.1, SDXL, DALL-E 3,
MidJourney v6), which this script maps to separate output subfolders. That is
what makes `train.py --holdout-group <generator>` a real leave-one-generator
-out test rather than a no-op.

THE RESOLUTION CAVEAT -- READ THIS. The paper states images are stored at
native resolution with no normalisation. But the reals are MS COCO (commonly
~640x480) while the generated images come from SD3/SDXL/DALL-E 3/MidJourney
(commonly 1024x1024). That is potentially the SAME resolution shortcut that
made an earlier Kaggle dataset useless here -- a model learns "big and square
=> fake" and scores near chance on anything real-world. ALWAYS run --audit
first, and use --normalise-geometry if it warns.

Note --normalise-geometry equalises OUTPUT dimensions but cannot undo
resampling history (an upscaled small image stays soft; a downscaled large
one does not). The only way to know whether that residual difference is
being exploited is to train and then evaluate against a DIFFERENT, trusted
test set. Do that before merging this into your main training data.

    pip install datasets

    # 0. ALWAYS audit first
    python prepare_cocoai.py --audit --n 3000

    # 1. build train/val, one folder per generator
    python prepare_cocoai.py --split train      --out data_cocoai/train --per-generator 3000
    python prepare_cocoai.py --split validation --out data_cocoai/val   --per-generator 500
"""
from __future__ import annotations

import argparse
import random
from collections import Counter, defaultdict
from pathlib import Path

from PIL import Image

from _geom import apply_geometry, geometry_params

HF_DATASET = "Rajarshi-Roy-research/Defactify_Image_Dataset"

# label_2 values -> folder-safe generator names. Values are matched
# case-insensitively with spaces/dots/hyphens stripped, since the exact
# spelling in the dataset ("DALL-E 3" vs "dalle3") is not guaranteed.
GENERATOR_SLUGS = {
    "sd3": "SD3",
    "stablediffusion3": "SD3",
    "sd21": "SD2_1",
    "stablediffusion21": "SD2_1",
    "sdxl": "SDXL",
    "dalle3": "DALLE3",
    "midjourney6": "MidJourney6",
    "midjourneyv6": "MidJourney6",
}


def slugify_generator(raw) -> str:
    """Map a label_2 value to a stable folder name; fall back to a cleaned form."""
    s = str(raw).lower()
    key = "".join(ch for ch in s if ch.isalnum())
    if key in GENERATOR_SLUGS:
        return GENERATOR_SLUGS[key]
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in str(raw)).strip("_")
    return cleaned or "unknown"


# --------------------------------------------------------------------------
# audit
# --------------------------------------------------------------------------


def inspect(ds, n: int = 3) -> None:
    """
    Print the dataset's ACTUAL schema. The paper documents label_1 (real/fake)
    and label_2 (generator), but an uploaded dataset's real column names often
    differ from its paper -- so read them rather than assuming.
    """
    print("\n=== dataset schema ===")
    cols = getattr(ds, "column_names", None)
    print(f"column_names: {cols}")

    for i, row in enumerate(ds):
        if i >= n:
            break
        print(f"\n--- row {i} ---")
        for k, v in row.items():
            if hasattr(v, "size") and hasattr(v, "mode"):        # a PIL image
                print(f"  {k:20s} = <PIL Image {v.size} {v.mode}>")
            else:
                s = str(v)
                print(f"  {k:20s} = {s[:100]}{'...' if len(s) > 100 else ''}"
                     f"   (type={type(v).__name__})")

    print("\nFind the binary real/fake column and the generator column above,")
    print("then rerun with --label-col and --generator-col set accordingly, e.g.:")
    print("  python prepare_cocoai.py --audit --label-col label --generator-col model\n")


def audit(ds, n: int, label_col: str, generator_col: str, image_col: str = "image") -> None:
    sizes = defaultdict(Counter)
    square = Counter()
    total = Counter()

    for i, row in enumerate(ds):
        if i >= n:
            break
        if label_col not in row:
            raise SystemExit(
                f"column {label_col!r} not found. Available: {list(row.keys())}\n"
                f"Run with --inspect to see the schema, then pass --label-col."
            )
        if image_col not in row:
            raise SystemExit(
                f"column {image_col!r} not found. Available: {list(row.keys())}\n"
                f"Pass --image-col with the correct name."
            )
        is_fake = int(row[label_col]) == 1
        key = slugify_generator(row.get(generator_col, "?")) if is_fake else "real"
        img = row[image_col]
        w, h = img.size
        total[key] += 1
        square[key] += int(w == h)
        sizes[key][f"{w}x{h}"] += 1

    print(f"\n{'class/generator':<18}{'n':>7}{'square':>9}{'%square':>9}   most common sizes")
    print("-" * 88)
    for key in sorted(total):
        pct = 100 * square[key] / total[key]
        top = ", ".join(f"{k}({v})" for k, v in sizes[key].most_common(3))
        print(f"{key:<18}{total[key]:>7}{square[key]:>9}{pct:>8.1f}%   {top}")

    n_real = total.get("real", 0)
    n_fake = sum(v for k, v in total.items() if k != "real")
    if n_real and n_fake:
        sq_real = square.get("real", 0) / n_real
        sq_fake = sum(v for k, v in square.items() if k != "real") / n_fake
        tpr, tnr = sq_fake, 1 - sq_real
        print(f"\n  trivial rule 'square => fake': balanced accuracy {0.5*(tpr+tnr):.1%}")
        if abs(sq_real - sq_fake) > 0.3:
            print("  WARNING: squareness differs sharply by class -- use "
                 "--normalise-geometry, then validate against a separate trusted test set.")
        else:
            print("  squareness looks similar across classes.")
        print()


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="train")
    ap.add_argument("--out", default=Path("data_cocoai/train"), type=Path)
    ap.add_argument("--per-generator", type=int, default=3000,
                    help="cap per generator; reals are capped at the resulting total "
                         "so the two classes stay balanced")
    ap.add_argument("--audit", action="store_true", help="report shortcut stats and exit")
    ap.add_argument("--inspect", action="store_true",
                    help="print the dataset's actual column names + sample rows, then exit. "
                         "Run this FIRST -- the paper documents label_1/label_2 but the "
                         "uploaded dataset may use different names.")
    ap.add_argument("--label-col", default="Label_A",
                    help="column holding the binary real(0)/fake(1) label. This dataset "
                         "uses capitalised names (Label_A/Label_B), NOT the label_1/label_2 "
                         "documented in the paper -- always --inspect to confirm.")
    ap.add_argument("--generator-col", default="Label_B",
                    help="column holding the generator name for fake images")
    ap.add_argument("--image-col", default="Image",
                    help="column holding the image itself")
    ap.add_argument("--n", type=int, default=3000, help="rows to scan in audit mode")
    ap.add_argument("--normalise-geometry", action="store_true",
                    help="use if --audit warns about a shape/size difference")
    ap.add_argument("--quality", type=int, default=92)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    from datasets import load_dataset

    ds = load_dataset(HF_DATASET, split=args.split, streaming=True)

    if args.inspect:
        inspect(ds)
        return

    if args.audit:
        audit(ds, args.n, args.label_col, args.generator_col, args.image_col)
        return

    rng = random.Random(args.seed)
    kept_fake: Counter = Counter()
    kept_real = 0
    skipped = 0
    # reals are capped at the total fake budget so classes stay balanced
    real_budget = args.per_generator * len(set(GENERATOR_SLUGS.values()))

    for row in ds:
        if args.label_col not in row:
            raise SystemExit(
                f"column {args.label_col!r} not found. Available: {list(row.keys())}\n"
                f"Run with --inspect to see the schema, then pass --label-col/--generator-col."
            )
        try:
            is_fake = int(row[args.label_col]) == 1
            if is_fake:
                gen = slugify_generator(row.get(args.generator_col, "unknown"))
                if kept_fake[gen] >= args.per_generator:
                    continue
                dst_dir = args.out / "fake" / gen
            else:
                if kept_real >= real_budget:
                    continue
                dst_dir = args.out / "real" / "mscoco"

            img = row[args.image_col].convert("RGB")
            if args.normalise_geometry:
                img = apply_geometry(img, geometry_params(*img.size, rng))

            dst_dir.mkdir(parents=True, exist_ok=True)
            name = f"{row.get('id', kept_real + sum(kept_fake.values()))}.jpg"
            img.save(dst_dir / name, format="JPEG", quality=args.quality)

            if is_fake:
                kept_fake[gen] += 1
            else:
                kept_real += 1
        except Exception:
            skipped += 1
            continue

        total_kept = kept_real + sum(kept_fake.values())
        if total_kept % 500 == 0:
            print(f"  real={kept_real}  fake={dict(kept_fake)}", flush=True)

        # Stop once reals are full AND every generator SEEN SO FAR is full.
        # Deliberately not checking against a hardcoded generator list -- the
        # dataset's actual label values may differ from the paper's, and a
        # wrong hardcoded name would mean this never fires and the loop runs
        # to the end of the split.
        if kept_real >= real_budget and kept_fake and all(
            v >= args.per_generator for v in kept_fake.values()
        ):
            break

    print(f"\nwrote to {args.out}")
    print(f"  real: {kept_real}")
    for gen, n in sorted(kept_fake.items()):
        print(f"  fake/{gen}: {n}")
    print(f"  skipped: {skipped}")
    if not args.normalise_geometry:
        print("\nNOTE: geometry not normalised. If --audit warned about a size "
             "difference, rerun with --normalise-geometry.")
    print(f"\nGenerator folders preserved -- LOGO testing enabled, e.g.:")
    print(f"  python train.py --train cache/tr --val cache/va --out runs/logo "
         f"--holdout-group {sorted(kept_fake)[0] if kept_fake else 'SDXL'}")


if __name__ == "__main__":
    main()