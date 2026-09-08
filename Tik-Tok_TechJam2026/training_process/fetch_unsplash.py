"""
Fetch Unsplash reals to broaden the training real-image distribution.

WHY UNSPLASH. Four content categories are currently weak (people 0.62,
food 0.78, city 0.83, animals 0.87) against 0.96 on the in-distribution test
set. A previous attempt at fixing this by adding MS COCO + OpenImages reals
made EVERY category worse -- both are conventional web photography, much like
the original training reals, so it deepened coverage of a region already
occupied rather than broadening it. Unsplash is professionally-shot,
deliberately styled stock photography: a genuinely different kind of real
image, which is the untested part of the hypothesis.

BE HONEST ABOUT THE ODDS. This is an experiment, not a fix. The last
corpus-addition attempt backfired. It is entirely possible this adds another
distribution the model overfits to instead. Judge it on the four category
benchmarks, not on the in-distribution test set.

HOW UNSPLASH DISTRIBUTES DATA. Not as an image archive. You get TSV metadata
from https://github.com/unsplash/datasets (Lite: ~25k photos, free; Full:
~3M, by application), and each row carries a photo URL you fetch yourself.
So: download + unzip the TSVs first, then point this script at that folder.

    # 1. get the metadata (manual step)
    #    https://github.com/unsplash/datasets -> download Lite -> unzip
    #
    # 2. see what the TSVs contain
    python fetch_unsplash.py --tsv-dir path/to/unsplash-lite --inspect
    #
    # 3. download images (resumable -- rerun safely if interrupted)
    python fetch_unsplash.py --tsv-dir path/to/unsplash-lite \\
                             --out data_unsplash --limit 3000 \\
                             --keywords portrait,person,food,city,animal
"""
from __future__ import annotations

import argparse
import csv
import random
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path

USER_AGENT = "aigc-detector-research/1.0"


def load_photos_tsv(tsv_dir: Path) -> list[dict]:
    """
    Unsplash ships photos.tsv000 (Lite) or photos.tsv000..00N (Full).
    csv.field_size_limit is raised because some description fields are long.
    """
    csv.field_size_limit(min(sys.maxsize, 2**31 - 1))
    files = sorted(tsv_dir.glob("photos.tsv*"))
    if not files:
        raise SystemExit(
            f"no photos.tsv* found in {tsv_dir}\n"
            "Download the dataset from https://github.com/unsplash/datasets "
            "and unzip it first -- this script reads the TSV metadata, it "
            "cannot fetch it for you."
        )
    rows: list[dict] = []
    for f in files:
        with open(f, encoding="utf-8") as fh:
            rows.extend(csv.DictReader(fh, delimiter="\t"))
    return rows


def inspect(rows: list[dict]) -> None:
    print(f"\n{len(rows)} photo rows")
    if not rows:
        return
    print(f"\ncolumns: {list(rows[0].keys())}\n")
    print("--- first row ---")
    for k, v in rows[0].items():
        print(f"  {k:32s} = {str(v)[:70]}")

    # which column holds a usable image URL
    url_cols = [k for k in rows[0] if "url" in k.lower()]
    print(f"\nURL-ish columns: {url_cols}")

    # what keywords exist, for --keywords filtering
    kw_col = next((k for k in rows[0] if "keyword" in k.lower()
                   or "description" in k.lower() or "tag" in k.lower()), None)
    if kw_col:
        c = Counter()
        for r in rows[:20000]:
            for tok in str(r.get(kw_col, "")).lower().replace(",", " ").split():
                if len(tok) > 3:
                    c[tok] += 1
        print(f"\ncommon terms in '{kw_col}' (use with --keywords):")
        for k, v in c.most_common(30):
            print(f"  {k:20s} {v}")


def pick_url(row: dict, width: int) -> str | None:
    """
    Unsplash URLs accept imgix params, so ?w= gives a resized copy. Asking for
    a moderate width keeps the download sane and roughly matches the
    resolution profile of the existing training data.
    """
    for key in ("photo_image_url", "photo_url", "urls_regular", "url"):
        if row.get(key):
            base = str(row[key])
            sep = "&" if "?" in base else "?"
            return f"{base}{sep}w={width}&fm=jpg&q=85"
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tsv-dir", required=True, type=Path,
                    help="folder containing the unzipped photos.tsv* files")
    ap.add_argument("--out", default=Path("data_unsplash"), type=Path)
    ap.add_argument("--inspect", action="store_true",
                    help="print the TSV schema and common keywords, then exit")
    ap.add_argument("--limit", type=int, default=3000,
                    help="how many images to download. Keep this modest -- the "
                         "goal is a DIFFERENT kind of real image, not more volume. "
                         "Match it roughly to your existing per-source counts.")
    ap.add_argument("--keywords", default="",
                    help="comma-separated terms; a row is kept if any appears in "
                         "its description/keyword fields. Target the weak "
                         "categories, e.g. portrait,person,food,city,animal")
    ap.add_argument("--width", type=int, default=1024,
                    help="request this pixel width from Unsplash's resizer")
    ap.add_argument("--sleep", type=float, default=0.15,
                    help="seconds between requests -- be considerate to their "
                         "servers, and avoid being rate-limited mid-run")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    rows = load_photos_tsv(args.tsv_dir)

    if args.inspect:
        inspect(rows)
        return

    if args.keywords:
        terms = [t.strip().lower() for t in args.keywords.split(",") if t.strip()]
        text_cols = [k for k in rows[0]
                     if any(s in k.lower() for s in ("description", "keyword", "tag", "alt"))]
        before = len(rows)
        rows = [r for r in rows
                if any(t in " ".join(str(r.get(c, "")) for c in text_cols).lower()
                       for t in terms)]
        print(f"[filter] {before} -> {len(rows)} rows matching {terms}")
        if not rows:
            raise SystemExit("no rows matched -- run --inspect to see real keyword values")

    random.Random(args.seed).shuffle(rows)

    dst = args.out / "real" / "unsplash"
    dst.mkdir(parents=True, exist_ok=True)

    existing = {p.stem for p in dst.glob("*.jpg")}
    if existing:
        print(f"[resume] {len(existing)} already downloaded, skipping those")

    got = len(existing)
    failed = 0
    opener = urllib.request.build_opener()
    opener.addheaders = [("User-Agent", USER_AGENT)]

    for row in rows:
        if got >= args.limit:
            break
        pid = str(row.get("photo_id") or row.get("id") or "").strip()
        if not pid or pid in existing:
            continue
        url = pick_url(row, args.width)
        if not url:
            continue
        try:
            with opener.open(url, timeout=20) as resp:
                data = resp.read()
            if len(data) < 5000:            # too small to be a real photo
                failed += 1
                continue
            (dst / f"{pid}.jpg").write_bytes(data)
            got += 1
        except Exception as e:
            failed += 1
            if failed <= 5:
                print(f"  [fail] {pid}: {type(e).__name__}: {e}")
        if got % 100 == 0 and got:
            print(f"  {got}/{args.limit}", flush=True)
        time.sleep(args.sleep)

    print(f"\nwrote {got} images to {dst}  ({failed} failures)")
    print("\nNext -- add ALONGSIDE your existing reals, do not replace them:")
    print(f"  Copy-Item -Recurse {dst} data\\train\\real\\")
    print("  then trim counts so the classes stay balanced, re-embed, and")
    print("  retrain with --init-from runs/merged/head.pt")
    print("\nJudge the result on the four CATEGORY benchmarks (people/food/")
    print("city/animals), not on data/test -- the previous corpus addition")
    print("improved data/test while making every category worse.")


if __name__ == "__main__":
    main()