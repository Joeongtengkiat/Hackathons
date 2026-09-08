"""
Split a flat, generator-separated image pool into train/val/test.

Built for a layout like:
    images/real/                 (70000 files, flat)
    images/fake/SDXL/            (53087)
    images/fake/FLUX_DEV/        (7273)
    images/fake/FLUX_PRO/        (3209)

Two things this handles that a naive copy would get wrong:

1. EVEN SAMPLING ACROSS GENERATORS. Using everything would give a fake set
   that is 83% SDXL, so the model would overwhelmingly learn SDXL's
   fingerprint and barely see FLUX. --per-generator caps each family to the
   same count, which is far more useful for generalisation than raw volume
   skewed toward one source.

2. PRESERVING GENERATOR IDENTITY. Each generator keeps its own subfolder in
   the output, so clipfeat.scan_dir assigns it a distinct `group` -- which is
   what makes `train.py --holdout-group SDXL` (leave-one-generator-out) an
   actual, meaningful test rather than a no-op.

Reals are sampled to match the total fake count, keeping classes balanced.

    python split_pool.py --pool path/to/images --out data_pool \
                         --per-generator 3000 --normalise-geometry
"""
from __future__ import annotations

import argparse
import random
import shutil
from pathlib import Path

from PIL import Image

from _geom import IMG_EXT, geometry_params, list_images


def place(src: Path, dst: Path, rng: random.Random, normalise_geometry: bool,
          quality: int, mode: str) -> bool:
    dst.parent.mkdir(parents=True, exist_ok=True)
    final = dst.with_suffix(".jpg") if normalise_geometry else dst
    if final.exists():
        return True                      # idempotent: safe to re-run
    try:
        if normalise_geometry:
            with Image.open(src) as im:
                im = im.convert("RGB")
                left, top, s, side = geometry_params(*im.size, rng)
                im = im.crop((left, top, left + s, top + s)).resize(
                    (side, side), Image.Resampling.BICUBIC)
                im.save(final, format="JPEG", quality=quality)
        elif mode == "hardlink":
            try:
                import os
                os.link(src, dst)
            except OSError:
                shutil.copy2(src, dst)
        else:
            shutil.copy2(src, dst)
        return True
    except Exception as e:
        print(f"  [skip] {src.name}: {type(e).__name__}: {e}")
        return False


def three_way(files: list[Path], val_frac: float, test_frac: float,
              rng: random.Random) -> tuple[list, list, list]:
    files = list(files)
    rng.shuffle(files)
    n = len(files)
    n_test = int(round(n * test_frac))
    n_val = int(round(n * val_frac))
    return files[n_test + n_val:], files[n_test:n_test + n_val], files[:n_test]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", required=True, type=Path,
                    help="root containing real/ and fake/<generator>/ subfolders")
    ap.add_argument("--out", default=Path("data_pool"), type=Path)
    ap.add_argument("--per-generator", type=int, default=3000,
                    help="cap per fake generator -- keeps families balanced")
    ap.add_argument("--val-frac", type=float, default=0.15)
    ap.add_argument("--test-frac", type=float, default=0.15)
    ap.add_argument("--normalise-geometry", action="store_true",
                    help="run `audit` first; use this if it flags a shape shortcut")
    ap.add_argument("--mode", choices=["copy", "hardlink"], default="hardlink")
    ap.add_argument("--quality", type=int, default=92)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    fake_root = args.pool / "fake"
    real_root = args.pool / "real"
    if not fake_root.is_dir() or not real_root.is_dir():
        raise SystemExit(f"expected {fake_root} and {real_root} to exist")

    generators = sorted(d for d in fake_root.iterdir() if d.is_dir())
    if not generators:
        raise SystemExit(f"no generator subfolders found under {fake_root}")

    print(f"[pool] found {len(generators)} generators: {[g.name for g in generators]}\n")

    total_fake = 0
    for g in generators:
        files = list_images(g)
        picked = files if len(files) <= args.per_generator else rng.sample(files, args.per_generator)
        tr, va, te = three_way(picked, args.val_frac, args.test_frac, rng)
        print(f"  {g.name:12s} {len(files):6d} available -> {len(picked):5d} sampled "
             f"({len(tr)} train / {len(va)} val / {len(te)} test)")
        for split, group in (("train", tr), ("val", va), ("test", te)):
            for p in group:
                place(p, args.out / split / "fake" / g.name / p.name,
                     rng, args.normalise_geometry, args.quality, args.mode)
        total_fake += len(picked)

    real_files = list_images(real_root)
    n_real = min(len(real_files), total_fake)     # keep classes balanced
    picked_real = rng.sample(real_files, n_real)
    tr, va, te = three_way(picked_real, args.val_frac, args.test_frac, rng)
    print(f"\n  {'real':12s} {len(real_files):6d} available -> {n_real:5d} sampled "
         f"({len(tr)} train / {len(va)} val / {len(te)} test)")
    for split, group in (("train", tr), ("val", va), ("test", te)):
        for p in group:
            place(p, args.out / split / "real" / "pool" / p.name,
                 rng, args.normalise_geometry, args.quality, args.mode)

    print(f"\n[pool] wrote {args.out}/train, {args.out}/val, {args.out}/test")
    print(f"[pool] {total_fake} fake ({len(generators)} generators) + {n_real} real")
    print(f"\nGenerator groups preserved -- LOGO testing is now possible, e.g.:")
    print(f"  python train.py --train cache/train --val cache/val --out runs/logo "
         f"--holdout-group {generators[0].name}")


if __name__ == "__main__":
    main()