"""
Prepare the Kaggle "AI vs Human generated" dataset
(alessandrasala79/ai-vs-human-generated-dataset).

Unlike the folder-structured datasets this project has used, labels here live
in a CSV manifest rather than directory names:

    ,file_name,label
    0,train_data/a6dcb93f596a43249135678dfcfc17ea.jpg,1
    1,train_data/041be3153810433ab146bc97d5af505c.jpg,0

`file_name` is relative to the dataset root. Label convention is
**0 = real, 1 = AI-generated**, which matches this project's convention, so no
inversion is applied. That has been confirmed by inspection rather than assumed
-- getting it backwards would train an inverted model that looks mysteriously
bad rather than obviously broken.

    # 1. see the manifest and class balance
    python prepare_kaggle_csv.py --root <kaggle-path> --inspect

    # 2. build a balanced slice in the real/ + fake/ layout
    python prepare_kaggle_csv.py --root <kaggle-path> --out data_kaggle2 \\
                                 --per-class 3000 --val-frac 0.15

    # 3. ALWAYS audit before training on it
    python prepare_custom.py audit --train-root data_kaggle2/train
"""
from __future__ import annotations

import argparse
import csv
import random
import shutil
import sys
from collections import Counter
from pathlib import Path

from PIL import Image


def read_manifest(csv_path: Path) -> list[dict]:
    csv.field_size_limit(min(sys.maxsize, 2**31 - 1))
    with open(csv_path, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def inspect(rows: list[dict], root: Path) -> None:
    print(f"\n{len(rows)} rows")
    if not rows:
        return
    print(f"columns: {list(rows[0].keys())}\n")

    labels = Counter(str(r.get("label", "?")) for r in rows)
    print("label distribution (0 = real, 1 = AI-generated):")
    for k, v in sorted(labels.items()):
        name = {"0": "real", "1": "AI-generated"}.get(k, "?")
        print(f"  {k} ({name:14s}) {v}")

    print("\nsample paths:")
    for lab in ("0", "1"):
        ex = next((r for r in rows if str(r.get("label")) == lab), None)
        if ex:
            p = root / ex["file_name"]
            exists = "OK" if p.exists() else "MISSING"
            print(f"  label={lab}: {ex['file_name']}  [{exists}]")

    # dimensions per class, so the geometry shortcut is visible before building
    print("\nimage dimensions by class (first 40 of each):")
    for lab in ("0", "1"):
        sizes, square, n = Counter(), 0, 0
        for r in rows:
            if str(r.get("label")) != lab:
                continue
            p = root / r["file_name"]
            if not p.exists():
                continue
            try:
                with Image.open(p) as im:
                    w, h = im.size
                sizes[f"{w}x{h}"] += 1
                square += int(w == h)
                n += 1
            except Exception:
                continue
            if n >= 40:
                break
        if n:
            name = "real" if lab == "0" else "AI"
            top = ", ".join(f"{k}({v})" for k, v in sizes.most_common(3))
            print(f"  {name:4s} n={n:3d}  square={square:3d} ({100*square/n:.0f}%)  {top}")

    print("\nIf squareness or sizes differ sharply by class, that is the "
         "shortcut that hit four of five previous datasets -- build, then run "
         "prepare_custom.py audit to confirm.\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, type=Path,
                    help="dataset root (the folder containing train.csv)")
    ap.add_argument("--csv", default="train.csv",
                    help="manifest filename inside --root")
    ap.add_argument("--out", default=Path("data_kaggle2"), type=Path)
    ap.add_argument("--inspect", action="store_true",
                    help="report the manifest, class balance and per-class "
                         "dimensions, then exit")
    ap.add_argument("--per-class", type=int, default=3000,
                    help="cap per class. Keep this modest -- a ~0.7M-param head "
                         "on frozen features saturates early, and the goal is "
                         "coverage, not volume.")
    ap.add_argument("--val-frac", type=float, default=0.15)
    ap.add_argument("--mode", choices=["copy", "hardlink"], default="hardlink",
                    help="hardlink avoids duplicating images on disk; falls back "
                         "to copy automatically if the filesystem refuses")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    rows = read_manifest(args.root / args.csv)

    if args.inspect:
        inspect(rows, args.root)
        return

    rng = random.Random(args.seed)
    by_label: dict[str, list[dict]] = {"0": [], "1": []}
    for r in rows:
        lab = str(r.get("label", "")).strip()
        if lab in by_label:
            by_label[lab].append(r)

    print(f"[manifest] real={len(by_label['0'])}  AI={len(by_label['1'])}")

    # 0 = real, 1 = AI-generated -- confirmed by inspection, not assumed
    dest = {"0": ("real", "kaggle2"), "1": ("fake", "kaggle2_ai")}

    kept = Counter()
    missing = failed = 0

    for lab, group in by_label.items():
        rng.shuffle(group)
        picked = group[: args.per_class]
        n_val = int(round(len(picked) * args.val_frac))
        splits = [("val", picked[:n_val]), ("train", picked[n_val:])]

        cls, sub = dest[lab]
        for split, items in splits:
            out_dir = args.out / split / cls / sub
            out_dir.mkdir(parents=True, exist_ok=True)
            for r in items:
                src = args.root / r["file_name"]
                if not src.exists():
                    missing += 1
                    if missing <= 5:
                        print(f"  [missing] {src}")
                    continue
                dst = out_dir / Path(r["file_name"]).name
                if dst.exists():
                    kept[(split, cls)] += 1
                    continue
                try:
                    if args.mode == "hardlink":
                        try:
                            import os
                            os.link(src, dst)
                        except OSError:
                            shutil.copy2(src, dst)
                    else:
                        shutil.copy2(src, dst)
                    kept[(split, cls)] += 1
                except Exception as e:
                    failed += 1
                    if failed <= 5:
                        print(f"  [fail] {src.name}: {type(e).__name__}: {e}")

    print(f"\nwrote to {args.out}")
    for (split, cls), n in sorted(kept.items()):
        print(f"  {split}/{cls}: {n}")
    if missing:
        print(f"  MISSING (paths did not resolve): {missing} -- check --root")
    if failed:
        print(f"  failed to place: {failed}")

    print("\nNext:")
    print(f"  python prepare_custom.py audit --train-root {args.out}/train")
    print("  ^ do this BEFORE training. Four of five previous datasets carried")
    print("    a geometry, resolution or format shortcut.")


if __name__ == "__main__":
    main()