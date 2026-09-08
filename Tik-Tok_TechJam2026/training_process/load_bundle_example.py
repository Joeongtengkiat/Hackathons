"""
Minimal example: load an exported bundle and score images with it.

This is the consumer side of export_bundle.py. A bundle is plain data (config
dicts, state_dicts, numbers), so nothing here needs a HuggingFace cache or any
network access -- point it at a bundle.pt and a folder and it prints verdicts.

    python load_bundle_example.py --bundle runs/cvar/bundle.pt --folder some/images

The Detector class in detector.py already wraps the whole load-and-score path
(re-encode -> CLIP embed -> head -> Platt -> threshold), so the "minimal
pattern" really is: construct it once, call predict_folder. Reach for the
lower-level clipfeat.load_clip_from_bundle() only if you need the raw tower.
"""
from __future__ import annotations

import argparse

from detector import Detector


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", required=True, help="a bundle.pt from export_bundle.py")
    ap.add_argument("--folder", required=True, help="a folder of images to score")
    ap.add_argument("--limit", type=int, default=10,
                    help="how many per-image results to print (default: 10)")
    args = ap.parse_args()

    # loads the tower + head from the bundle once; no network access needed
    det = Detector(args.bundle)

    results = det.predict_folder(args.folder)
    if not results:
        print(f"no images found under {args.folder}")
        return

    for r in results[:args.limit]:
        print(f"  {r['verdict']:5s}  p(fake)={r['probability_fake']:.3f}  {r['path']}")
    if len(results) > args.limit:
        print(f"  ... and {len(results) - args.limit} more")

    s = det.summary(results)
    print(f"\n{s['total']} images  ->  {s['fake']} fake, {s['real']} real, "
          f"{s['errors']} errors")


if __name__ == "__main__":
    main()
