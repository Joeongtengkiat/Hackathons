"""CALIBRATION: pick the operating point that holds false positives in budget.

    python calibrate.py <labeled_dir>
    python calibrate.py <labeled_dir> --fpr 0.01 --out calibration.json
    python calibrate.py <labeled_dir> --weights models/bundle.pt

The problem your friend hit: a threshold calibrated on one distribution can
drift on another, and when it drifts the way that hurts is FPR - real photos
flagged as AI. This scores a labeled folder and reports, side by side, the
operating points that matter:

  deployed        the threshold the bundle ships with
  FPR budgets     the lowest threshold that keeps FPR within 0.5 / 1 / 2 / 5 %
  best F1         the threshold that maximises F1
  best balanced   the threshold that maximises balanced accuracy

It does NOT touch the scores or refit the model - only the decision boundary,
which is the only lever that changes false positives without retraining. Pass
the winning number to `detect.py --threshold` or the GUI slider.

IMPORTANT: calibrate on a HELD-OUT set that resembles your deployment traffic -
never on the challenge's validation subset (COCO val2017 + DALL-E Advanced),
which the rules say not to train or tune on. A threshold is a tuned parameter.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import metrics as M                                # noqa: E402
from app import runner                                      # noqa: E402

#: the false-positive budgets reported by default - the rates a platform would
#: actually pick an operating point for. 1% is the shipped model's own target.
DEFAULT_FPRS = "0.005,0.01,0.02,0.05"


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        prog="calibrate.py",
        description="Recommend a decision threshold from a labeled folder, for a "
                    "target false-positive rate.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("directory", nargs="?", help="labeled image directory")
    ap.add_argument("--fpr", default=DEFAULT_FPRS,
                    help=f"comma-separated FPR budgets to report (default: {DEFAULT_FPRS})")
    ap.add_argument("--detector", "-d", default=None, help="registered detector name")
    ap.add_argument("--weights", "-w", default=None,
                    help="checkpoint to load (default: the backend's own)")
    ap.add_argument("--out", "-o", default=None,
                    help="also write the recommendations to this JSON path")
    ap.add_argument("--quiet", "-q", action="store_true")
    return ap.parse_args(argv)


def _report(exc: SystemExit) -> int:
    code = exc.code
    if isinstance(code, str):
        print(code, file=sys.stderr)
        return 2
    return int(code or 0)


def _row(label: str, threshold: float, m: M.Metrics) -> str:
    """One line of the comparison table, aligned with the header below."""
    return (f"  {label:<16} {threshold:>8.4f}   "
            f"{M.fmt(m.accuracy):>7}  {M.fmt(m.balanced_accuracy):>7}  "
            f"{M.fmt(m.recall):>7}  {M.fmt(m.fpr):>7}  {M.fmt(m.f1, pct=False):>5}")


def build_recommendations(y, s, deployed: float, fprs: list) -> list:
    """Every operating point worth comparing, as (label, threshold, Metrics).

    Pure over (y, s): the same list backs both the printed table and the JSON,
    so what you read is what gets written.
    """
    rows = [("deployed", float(deployed), M.compute_metrics(y, s, deployed))]
    for target in fprs:
        t = M.threshold_for_fpr(y, s, target)
        rows.append((f"FPR<={target * 100:g}%", t, M.compute_metrics(y, s, t)))
    for label, crit in (("best F1", "f1"), ("best balanced", "youden")):
        t = M.best_threshold(y, s, crit)
        rows.append((label, t, M.compute_metrics(y, s, t)))
    return rows


def main(argv=None) -> int:
    args = parse_args(argv)

    if not args.directory:
        print("error: a labeled image directory is required", file=sys.stderr)
        return 2

    runner.QUIET = args.quiet
    try:
        dataset, result = runner.run_directory(
            args.directory, args.detector, args.weights, total_steps=3)
    except SystemExit as exc:
        return _report(exc)

    if not dataset.has_labels:
        print("\nerror: calibration measures FPR, so it needs ground-truth labels.\n"
              "       use real/ and ai/ subfolders, a labels.csv, or real_/ai_ "
              "prefixes.", file=sys.stderr)
        return 2

    y, s = result.valid_pairs(dataset)
    if not _both_classes(y):
        print("\nerror: need at least one authentic and one AI image to calibrate.",
              file=sys.stderr)
        return 2

    fprs = [float(x) for x in args.fpr.split(",") if x.strip()]
    rows = build_recommendations(y, s, result.threshold, fprs)

    runner.step(3, 3, "calibration")
    runner.log(f"scored {len(y):,} labeled images "
               f"({int(sum(1 for v in y if v == 0)):,} real / "
               f"{int(sum(1 for v in y if v == 1)):,} AI)", indent=6)
    runner.log("")
    runner.log(f"  {'operating point':<16} {'thresh':>8}   "
               f"{'acc':>7}  {'bal-acc':>7}  {'recall':>7}  {'FPR':>7}  {'F1':>5}")
    for label, t, m in rows:
        runner.log(_row(label, t, m))
    runner.log("")

    # the headline recommendation: the tightest budget that still catches
    # something. This is the one to quote for a false-positive-sensitive deploy.
    tightest = next((r for r in rows if r[0].startswith("FPR")), None)
    if tightest:
        label, t, m = tightest
        runner.log(f"recommended for low false positives:  --threshold {t:.4f}  "
                   f"({label} -> recall {M.fmt(m.recall)}, FPR {M.fmt(m.fpr)})",
                   indent=6)
    runner.log("apply with:  python detect.py <dir> --threshold <value>", indent=6)

    if args.out:
        payload = {
            "directory": os.path.abspath(args.directory),
            "detector": result.detector_display,
            "n_labeled": len(y),
            "deployed_threshold": float(result.threshold),
            "recommendations": [
                {"label": label, "threshold": round(t, 6), "metrics": m.as_dict()}
                for label, t, m in rows
            ],
        }
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        runner.log(f"recommendations -> {os.path.abspath(args.out)}", indent=6)

    return 0


def _both_classes(y) -> bool:
    """At least one of each label - calibration is meaningless otherwise."""
    return any(v == 0 for v in y) and any(v == 1 for v in y)


if __name__ == "__main__":
    raise SystemExit(main())
