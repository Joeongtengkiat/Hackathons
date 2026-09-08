"""ROBUSTNESS: how far does accuracy fall under post-processing?

    python robustness.py <image_dir>
    python robustness.py <image_dir> --transforms jpeg,blur,rescale --severities 1,3,5
    python robustness.py <image_dir> --official

Each selected transform is applied in memory at the chosen severities and the
sample is re-scored, always against a clean baseline measured through the same
pipeline. Writes robustness_report.json next to the dataset; gui.py picks that
up and draws it.

--official sweeps the challenge's exact transform table instead - JPEG q90/70/
50/30, blur sigma 0.5/1.0/2.0, resize 0.5x/0.25x, noise sigma 0.02/0.05/0.10,
colour jitter +/-20%, centre crop 80% - so the report's cells line up one-to-one
with the spec. It ignores --transforms/--severities and writes to a separate
report file by default so a graded sweep is not clobbered.

Ground-truth labels are required - this measures accuracy, not just scores,
so point it at a folder holding real/ and ai/.

The grid is transforms x severities: --transforms picks which degradations,
--severities picks how hard, and every combination becomes one cell. A clean
baseline cell is always measured on top, through the same pipeline, because a
drop is only meaningful against a comparable reference.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import metrics as M                              # noqa: E402
from app import runner                                    # noqa: E402
from app import sweep as SW                               # noqa: E402
from app.transforms import (                              # noqa: E402
    OFFICIAL_TRANSFORMS, TRANSFORMS, TRANSFORMS_BY_KEY, official_cells)

#: the five worth running by default - the common codecs, the two resizes, and
#: the realistic combo. The rest are opt-in via --transforms.
DEFAULT_TRANSFORMS = "jpeg,blur,rescale,crop,social"

#: --official writes here by default, so it never clobbers a graded sweep's
#: robustness_report.json. Pass --out to send it somewhere else.
OFFICIAL_REPORT = "robustness_report_official.json"


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        prog="robustness.py",
        description="Measure detector robustness under post-processing.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("directory", nargs="?", help="labeled image directory")
    ap.add_argument("--transforms", default=DEFAULT_TRANSFORMS,
                    help=f"comma-separated keys (default: {DEFAULT_TRANSFORMS})")
    ap.add_argument("--severities", default="1,2,3,4,5",
                    help="comma-separated severity levels (default: 1,2,3,4,5)")
    ap.add_argument("--official", action="store_true",
                    help="sweep the challenge's exact transform table instead "
                         "(ignores --transforms/--severities)")
    ap.add_argument("--sample", type=int, default=200,
                    help="images per cell, balanced across classes (default: 200)")
    ap.add_argument("--max-side", type=int, default=768,
                    help="decode cap in pixels (default: 768)")
    ap.add_argument("--detector", "-d", default=None, help="registered detector name")
    ap.add_argument("--weights", "-w", default=None,
                    help="checkpoint to load (default: the backend's own, "
                         "e.g. models/bundle.pt)")
    ap.add_argument("--threshold", "-t", type=float, default=None,
                    help="decision threshold for the reported accuracy "
                         "(default: the detector's own operating point, or 0.5)")
    ap.add_argument("--tta", type=int, default=1, metavar="N",
                    help="test-time augmentation: average N views per image "
                         "(1 = off; 2 adds a flip; up to 4)")
    ap.add_argument("--out", "-o", default=None,
                    help=f"report path (default: <dir>/{SW.DEFAULT_REPORT})")
    ap.add_argument("--quiet", "-q", action="store_true")
    ap.add_argument("--list-transforms", action="store_true")
    return ap.parse_args(argv)


def _report(exc: SystemExit) -> int:
    """Print a SystemExit raised for bad input and turn it into exit code 2."""
    code = exc.code
    if isinstance(code, str):
        print(code, file=sys.stderr)
        return 2
    return int(code or 0)


def main(argv=None) -> int:
    args = parse_args(argv)

    if args.list_transforms:
        def show(spec):
            levels = "  |  ".join(spec.label_for(i)
                                  for i in range(1, spec.n_levels + 1))
            print(f"{spec.key:12s} {spec.display_name}")
            print(f"{'':12s} {spec.description}")
            print(f"{'':12s} severities: {levels}")
        for spec in TRANSFORMS:
            show(spec)
        print("\nofficial grid (--official):")
        for spec in OFFICIAL_TRANSFORMS:
            show(spec)
        return 0

    if not args.directory:
        print("error: a labeled image directory is required "
              "(use --list-transforms to see the options)", file=sys.stderr)
        return 2

    runner.QUIET = args.quiet
    try:
        dataset = runner.scan(args.directory, total_steps=4)
    except SystemExit as exc:
        return _report(exc)
    if not dataset.has_labels:
        print("error: this sweep measures accuracy, so it needs ground-truth labels.\n"
              "       use real/ and ai/ subfolders, a labels.csv, or real_/ai_ prefixes.",
              file=sys.stderr)
        return 2

    if args.official:
        # the spec's exact table: each transform at its own native levels, no
        # cross product with --severities. --transforms/--severities are ignored.
        cells = official_cells()
    else:
        keys = [k.strip() for k in args.transforms.split(",") if k.strip()]
        # "clean" is rejected on purpose: the baseline always runs, and asking
        # for it as a cell would compare it against itself
        unknown = [k for k in keys if k not in TRANSFORMS_BY_KEY or k == "clean"]
        if unknown:
            print(f"error: unknown transform(s): {', '.join(unknown)}\n"
                  f"       available: {', '.join(t.key for t in TRANSFORMS)}",
                  file=sys.stderr)
            return 2
        severities = [int(s) for s in args.severities.split(",") if s.strip()]
        # the full cross product: every transform at every severity asked for
        cells = [(k, sv) for k in keys for sv in severities]

    try:
        detector = runner.prepare_detector(args.detector, args.weights, total_steps=4)
    except SystemExit as exc:
        return _report(exc)
    if args.tta and args.tta > 1:
        if hasattr(type(detector), "tta_views"):
            detector.tta_views = args.tta
            runner.log(f"test-time augmentation: {args.tta} views/image", indent=6)
        else:
            runner.warn(f"{detector.display_name} does not support TTA - "
                        "scoring single-view")
    runner.step(3, 4, f"sweeping {len(cells) + 1} cells x up to {args.sample} images")

    try:
        # A calibrated model carries the threshold it was tuned for; the
        # whole point of this sweep is accuracy at a FIXED threshold, so it
        # has to be the right one.
        threshold = (args.threshold if args.threshold is not None
                     else float(getattr(detector, "default_threshold", 0.5)))
        result = SW.run_sweep(dataset, detector, cells, sample=args.sample,
                              max_side=args.max_side, threshold=threshold)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    default_report = OFFICIAL_REPORT if args.official else SW.DEFAULT_REPORT
    out = args.out or os.path.join(dataset.root, default_report)
    written = result.write(out)

    runner.step(4, 4, "results")
    base = result.baseline
    runner.log(f"clean baseline   accuracy {M.fmt(base.accuracy)}  "
               f"AUC {M.fmt(base.auc, pct=False)}", indent=6)

    worst = result.worst()
    if worst:
        worst_d, key, sv, m = worst
        spec = TRANSFORMS_BY_KEY[key]
        runner.log(f"worst case       {spec.display_name} | {spec.label_for(sv)}"
                   f"   {worst_d * 100:+.1f}pp  (acc {M.fmt(m.accuracy)})", indent=6)
        runner.log(f"mean drop        {result.mean_drop() * 100:+.1f}pp "
                   f"over {len(result.cells)} cells", indent=6)
    runner.log(f"swept {result.n_images} images per cell in {result.elapsed:.1f}s",
               indent=6)
    runner.log(f"report -> {written}", indent=6)
    runner.log("")
    runner.log(f"see it:  python gui.py {args.directory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
