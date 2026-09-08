"""
Adapter for a fixed local dataset already split into train/ and test/, each
with its own class subfolders (here: "ai" and "real"). Unlike prepare_sid.py
(which streams from the SID_Set HuggingFace dataset), this reads whatever
you already downloaded, sitting on disk.

Two things it does, and you should do them in this order:

1. `audit`  — checks for the SAME class of shortcut that hit SID_Set: does
   one class differ from the other in dimensions, aspect ratio, or file
   format for reasons that have nothing to do with being AI-generated? Run
   this FIRST. If it flags something, `layout --normalise-geometry` fixes it.

2. `layout` — carves a validation split out of train/ (stratified by class,
   images not currently split by generator so val is a random subsample),
   and copies everything into the real/<source>/ + fake/<generator>/
   structure clipfeat.scan_dir() expects. test/ is copied as-is — it's
   already held out, nothing to split there.

    python prepare_custom.py audit  --train-root path/to/downloaded/train

    python prepare_custom.py layout --train-root path/to/downloaded/train \
                                    --test-root  path/to/downloaded/test \
                                    --out data --val-frac 0.15
"""
from __future__ import annotations

import argparse
import os
import random
import shutil
from collections import Counter
from pathlib import Path

from PIL import Image

from _geom import IMG_EXT, apply_geometry, geometry_params, list_images

# your downloaded folder names -> the real/fake vocabulary scan_dir expects
CLASS_MAP = {"ai": "fake", "real": "real"}


# --------------------------------------------------------------------------
# audit — same question as prepare_sid.py's audit, computed from local files
# --------------------------------------------------------------------------


def detect_layout(train_root: Path) -> dict[str, str]:
    """
    audit() needs to know which folder names hold which class. Two different
    layouts show up in practice:
      RAW download:  train_root/ai/*.jpg, train_root/real/*.jpg
      LAID-OUT data: train_root/real/<src>/*.jpg, train_root/fake/<gen>/*.jpg
                     (this is what `layout` itself produces -- e.g. data/train
                     after running the layout command, which you'll often want
                     to re-audit to confirm a fix actually took effect)
    Returns a {folder_name: class_label} map matching whichever is present.
    """
    if (train_root / "real").is_dir() and (train_root / "fake").is_dir():
        return {"real": "real", "fake": "fake"}
    return dict(CLASS_MAP)   # raw layout: {"ai": "fake", "real": "real"}


def audit(train_root: Path, n: int) -> None:
    layout_map = detect_layout(train_root)
    kind = "laid-out (real/fake)" if "fake" in layout_map else "raw download (ai/real)"
    print(f"auditing up to {n} images per class from {train_root}  [detected: {kind}]\n")
    stats = {}
    for folder, label in layout_map.items():
        files = list_images(train_root / folder)[:n]
        if not files:
            print(f"  WARNING: no images found under {train_root / folder}")
            continue
        sizes, exts, fsizes, square = [], Counter(), [], 0
        for p in files:
            exts[p.suffix.lower()] += 1
            fsizes.append(p.stat().st_size)
            try:
                with Image.open(p) as im:
                    w, h = im.size
                sizes.append((w, h))
                square += int(w == h)
            except Exception:
                continue
        stats[label] = {
            "n": len(files), "sizes": Counter(sizes), "exts": exts,
            "fsizes": fsizes, "square": square,
        }

    print(f"{'class':<8}{'n':>6}{'square':>9}{'%square':>9}{'mean KB':>10}   top sizes / formats")
    print("-" * 90)
    for label, s in stats.items():
        pct = 100 * s["square"] / s["n"] if s["n"] else 0
        mean_kb = (sum(s["fsizes"]) / len(s["fsizes"]) / 1024) if s["fsizes"] else 0
        top_sizes = ", ".join(f"{w}x{h}({c})" for (w, h), c in s["sizes"].most_common(3))
        top_ext = ", ".join(f"{e}({c})" for e, c in s["exts"].most_common(3))
        print(f"{label:<8}{s['n']:>6}{s['square']:>9}{pct:>8.1f}%{mean_kb:>9.1f}KB   "
             f"{top_sizes}  |  {top_ext}")

    if len(stats) == 2:
        labels = list(stats)
        real_ext = stats["real"]["exts"] if "real" in stats else None
        fake_ext = stats["fake"]["exts"] if "fake" in stats else None
        if real_ext and fake_ext:
            real_top = real_ext.most_common(1)[0][0]
            fake_top = fake_ext.most_common(1)[0][0]
            if real_top != fake_top:
                print(f"\n  WARNING: dominant file format differs by class "
                     f"(real={real_top}, fake={fake_top}). A model can learn "
                     "format instead of content. Re-encode both classes at a "
                     "shared random JPEG quality before training (augment.py's "
                     "normalise_source does this automatically at embed time).")
        rf, ff = stats.get("real"), stats.get("fake")
        if rf and ff and rf["n"] and ff["n"]:
            rsq, fsq = rf["square"] / rf["n"], ff["square"] / ff["n"]
            if abs(rsq - fsq) > 0.3:
                print(f"\n  WARNING: squareness differs sharply by class "
                     f"(real {rsq:.0%} square vs fake {fsq:.0%} square). "
                     "This is exactly the shortcut that hit SID_Set — use "
                     "`layout --normalise-geometry` to remove it.")
            else:
                print("\n  squareness looks similar across classes — no obvious geometry shortcut.")

    print("\nIf nothing above says WARNING, you're clear to run `layout`.")


# --------------------------------------------------------------------------
# layout — split + copy into the structure scan_dir expects
# --------------------------------------------------------------------------


def place_one(src: Path, dst: Path, mode: str, rng: random.Random,
              normalise_geometry: bool, quality: int) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    final_dst = dst.with_suffix(".jpg") if normalise_geometry else dst
    if final_dst.exists():
        # A previous run got partway through (interrupted, crashed, or
        # ensure_layout's all-three-splits check didn't consider this split
        # "done" yet even though this individual file already was). Without
        # this, a re-run hits FileExistsError on os.link, falls back to
        # shutil.copy2, and THAT fails too -- copying src onto its own
        # existing hardlink is a same-file no-op Python correctly refuses.
        # Skipping an already-placed file is always correct: content is
        # never re-derived per-run (hardlink/copy is byte-identical; even
        # normalise_geometry's random crop is fine to leave as whatever it
        # was on the first successful attempt).
        return

    if normalise_geometry:
        with Image.open(src) as im:
            im = im.convert("RGB")
            params = geometry_params(*im.size, rng)
            im = apply_geometry(im, params)
            im.save(final_dst, format="JPEG", quality=quality)
        return

    if mode == "copy":
        shutil.copy2(src, dst)
    elif mode == "hardlink":
        try:
            os.link(src, dst)
        except OSError:
            shutil.copy2(src, dst)   # cross-device or no permission — fall back safely
    else:
        raise ValueError(mode)


def split_and_place(files: list[Path], val_frac: float, rng: random.Random) -> tuple[list[Path], list[Path]]:
    files = list(files)
    rng.shuffle(files)
    n_val = int(round(len(files) * val_frac))
    return files[n_val:], files[:n_val]   # (train, val)


def layout(train_root: Path, test_root: Path, out: Path, val_frac: float,
          mode: str, normalise_geometry: bool, quality: int, seed: int) -> None:
    rng = random.Random(seed)

    print(f"[layout] scanning {train_root} ...")
    per_class_train = {folder: list_images(train_root / folder) for folder in CLASS_MAP}
    for folder, files in per_class_train.items():
        print(f"  train/{folder}: {len(files)} images")

    for folder, dst_label in CLASS_MAP.items():
        train_files, val_files = split_and_place(per_class_train[folder], val_frac, rng)
        subdir = "src" if dst_label == "real" else "ai"

        for p in train_files:
            place_one(p, out / "train" / dst_label / subdir / p.name, mode, rng,
                     normalise_geometry, quality)
        for p in val_files:
            place_one(p, out / "val" / dst_label / subdir / p.name, mode, rng,
                     normalise_geometry, quality)
        print(f"  {folder}: {len(train_files)} -> train, {len(val_files)} -> val")

    print(f"[layout] scanning {test_root} ...")
    for folder, dst_label in CLASS_MAP.items():
        files = list_images(test_root / folder)
        subdir = "src" if dst_label == "real" else "ai"
        for p in files:
            place_one(p, out / "test" / dst_label / subdir / p.name, mode, rng,
                     normalise_geometry, quality)
        print(f"  test/{folder}: {len(files)} -> test")

    print(f"\n[layout] done. Wrote {out}/train, {out}/val, {out}/test")
    print("Next: python embed.py --data data/train --out cache/train --views 3")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    pa = sub.add_parser("audit")
    pa.add_argument("--train-root", required=True, type=Path)
    pa.add_argument("--n", type=int, default=4000)

    pl = sub.add_parser("layout")
    pl.add_argument("--train-root", required=True, type=Path)
    pl.add_argument("--test-root", required=True, type=Path)
    pl.add_argument("--out", default=Path("data"), type=Path)
    pl.add_argument("--val-frac", type=float, default=0.15)
    pl.add_argument("--mode", choices=["copy", "hardlink"], default="hardlink",
                    help="hardlink avoids duplicating 10k images on disk; "
                         "falls back to copy automatically if that fails")
    pl.add_argument("--normalise-geometry", action="store_true",
                    help="only pass this if `audit` flagged a shape/size shortcut")
    pl.add_argument("--quality", type=int, default=92)
    pl.add_argument("--seed", type=int, default=0)

    args = ap.parse_args()
    if args.cmd == "audit":
        audit(args.train_root, args.n)
    else:
        layout(args.train_root, args.test_root, args.out, args.val_frac,
              args.mode, args.normalise_geometry, args.quality, args.seed)


if __name__ == "__main__":
    main()