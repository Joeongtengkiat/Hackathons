"""
Set up COCO as (a) training reals and (b) a held-out benchmark.

WHY. Five experiments here established that the REAL-image corpus dominates
transfer far more than the synthetic side does. Every external dataset whose
reals came from a different corpus failed: pool 0.555, COCOAI 0.762/0.787,
SID_Set 0.637. The one merge that worked kept the original reals and borrowed
only fakes. The organisers' reference benchmark uses COCO val2017 reals -- the
same corpus that scored 0.787 in the COCOAI run. So COCO-style photography is
a known blind spot, and covering it is the direct fix.

THE SPLIT DISCIPLINE THAT MATTERS. COCO ships two disjoint sets:
    train2017  118k images  -> use for TRAINING reals
    val2017      5k images  -> hold out as the BENCHMARK

These share no images. Training on train2017 and evaluating on val2017 is
legitimate and is exactly what the organisers' "do not train on the
validation data" instruction requires. This script refuses to put val2017
into a training folder, and checks filename overlap between whatever it
writes, so the boundary cannot be crossed by accident.

    # 1. download (train2017 is ~18GB; val2017 ~1GB)
    python setup_coco.py --download-val --out-bench bench_coco
    python setup_coco.py --download-train --out-train data_coco

    # 2. or, if you already have COCO extracted somewhere
    python setup_coco.py --val-dir D:/coco/val2017   --out-bench bench_coco
    python setup_coco.py --train-dir D:/coco/train2017 --out-train data_coco --limit 6000

    # 3. verify the two never overlap
    python setup_coco.py --verify --out-train data_coco --out-bench bench_coco
"""
from __future__ import annotations

import argparse
import shutil
import urllib.request
import zipfile
from pathlib import Path

COCO_URLS = {
    "val2017": "http://images.cocodataset.org/zips/val2017.zip",
    "train2017": "http://images.cocodataset.org/zips/train2017.zip",
}

IMG_EXT = {".jpg", ".jpeg", ".png"}


def download_and_extract(which: str, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    zip_path = dest / f"{which}.zip"
    target = dest / which

    if target.is_dir() and any(target.glob("*.jpg")):
        print(f"[coco] {target} already populated, skipping download")
        return target

    url = COCO_URLS[which]
    if not zip_path.exists():
        print(f"[coco] downloading {which} from {url}")
        print("       (train2017 is ~18GB and will take a while)")

        def hook(block, block_size, total):
            if total > 0:
                pct = 100.0 * block * block_size / total
                print(f"\r       {min(pct,100):.1f}%", end="", flush=True)

        urllib.request.urlretrieve(url, zip_path, reporthook=hook)
        print()

    print(f"[coco] extracting {zip_path}")
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(dest)
    return target


def place_images(src_dir: Path, dst_dir: Path, limit: int = 0) -> int:
    dst_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in src_dir.iterdir()
                   if p.is_file() and p.suffix.lower() in IMG_EXT)
    if limit:
        files = files[:limit]
    n = 0
    for p in files:
        target = dst_dir / p.name
        if target.exists():
            n += 1
            continue
        try:
            shutil.copy2(p, target)
            n += 1
        except Exception as e:
            print(f"  [skip] {p.name}: {e}")
    return n


def verify_disjoint(train_root: Path, bench_root: Path) -> None:
    """
    Hard check that no image appears in both. If the training reals and the
    benchmark reals overlap, every benchmark number is meaningless -- the
    model would be scored on images it trained on.
    """
    tr = {p.name for p in train_root.rglob("*") if p.suffix.lower() in IMG_EXT}
    be = {p.name for p in bench_root.rglob("*") if p.suffix.lower() in IMG_EXT}
    overlap = tr & be

    print(f"\n[verify] training images: {len(tr)}")
    print(f"[verify] benchmark images: {len(be)}")
    if overlap:
        print(f"[verify] *** {len(overlap)} OVERLAPPING FILENAMES ***")
        for name in list(overlap)[:10]:
            print(f"           {name}")
        raise SystemExit(
            "\nTRAIN/BENCHMARK OVERLAP DETECTED. Any benchmark score computed\n"
            "against this would be invalid -- the model would be evaluated on\n"
            "images it trained on. Rebuild with train2017 for training and\n"
            "val2017 for the benchmark; they are disjoint by construction."
        )
    print("[verify] PASS -- no overlap. train2017 and val2017 are disjoint.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--download-val", action="store_true", help="fetch val2017 (~1GB)")
    ap.add_argument("--download-train", action="store_true", help="fetch train2017 (~18GB)")
    ap.add_argument("--val-dir", type=Path, help="existing extracted val2017 dir")
    ap.add_argument("--train-dir", type=Path, help="existing extracted train2017 dir")
    ap.add_argument("--cache", default=Path("coco_raw"), type=Path,
                    help="where downloads land")
    ap.add_argument("--out-train", type=Path,
                    help="write TRAINING reals here, as <out>/real/coco_train2017/")
    ap.add_argument("--out-bench", type=Path,
                    help="write BENCHMARK reals here, as <out>/real/coco_val2017/")
    ap.add_argument("--limit", type=int, default=0,
                    help="cap training reals (val2017 is never capped -- it is the "
                         "benchmark and should stay complete)")
    ap.add_argument("--verify", action="store_true",
                    help="check --out-train and --out-bench share no images, then exit")
    args = ap.parse_args()

    if args.verify:
        if not (args.out_train and args.out_bench):
            raise SystemExit("--verify needs both --out-train and --out-bench")
        verify_disjoint(args.out_train, args.out_bench)
        return

    # ---- benchmark reals: val2017, NEVER used for training ----
    if args.download_val or args.val_dir:
        src = args.val_dir or download_and_extract("val2017", args.cache)
        if not args.out_bench:
            raise SystemExit("--out-bench is required when handling val2017")
        dst = args.out_bench / "real" / "coco_val2017"
        n = place_images(src, dst)
        print(f"[bench] wrote {n} COCO val2017 reals -> {dst}")
        print("[bench] DO NOT train on these. Pair with AI fakes under "
             f"{args.out_bench}/fake/<name>/ then run benchmark_official.py")

    # ---- training reals: train2017, disjoint from the benchmark ----
    if args.download_train or args.train_dir:
        src = args.train_dir or download_and_extract("train2017", args.cache)
        if not args.out_train:
            raise SystemExit("--out-train is required when handling train2017")
        dst = args.out_train / "real" / "coco_train2017"
        n = place_images(src, dst, limit=args.limit)
        print(f"[train] wrote {n} COCO train2017 reals -> {dst}")
        print("[train] merge into your training data as an ADDITIONAL real source, "
             "keeping your existing reals -- the goal is corpus diversity, not "
             "replacement.")

    if args.out_train and args.out_bench and \
            args.out_train.exists() and args.out_bench.exists():
        verify_disjoint(args.out_train, args.out_bench)


if __name__ == "__main__":
    main()