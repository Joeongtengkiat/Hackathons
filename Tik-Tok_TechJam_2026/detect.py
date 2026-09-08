"""THE deliverable: image directory in, predictions.json out.

    python detect.py <image_dir>
    python detect.py <image_dir> --out results.json --weights models/best.pt

The folder decides what you get back. Both cases always write the JSON:

  labeled    a folder holding real/ and ai/ (or a labels.csv, or real_/ai_
             filename prefixes). Both classes are pooled into one evaluation
             set and scored together, then accuracy, AUC, F1, FPR and the
             confusion counts are printed. --best-threshold also reports where
             F1 peaks, which is usually not 0.50.

  unlabeled  any other folder. Scores only - there is no truth to measure
             against, so no metrics. This is not an error; pass
             --require-labels if you expected labels and want it to fail loudly
             when the subfolders turn out to be misnamed.

Output is the required format, plus a readable verdict:

    [
      {"image_path": "C:/data/img_0001.jpg", "pred": 0.8731, "prediction": "fake"},
      ...
    ]

`pred` is P(AI-generated) in [0, 1]. Scores are always raw floats - the
threshold never changes one. `prediction` is the verdict that score reads to at
the threshold in effect (--threshold, else the model's own operating point),
which is the one field the threshold does move.

Visualise any of it with:  python gui.py <dir>   or   python gui.py <out.json>

This file is only argument handling and printing. Every piece of real work -
scanning, label inference, loading the model, scoring - lives in app/runner.py,
which the GUI calls the same way, so the two can never disagree about a number.
"""

from __future__ import annotations

import argparse
import os
import sys

# run from anywhere: `python /some/where/detect.py` must still find app/
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import metrics as M                                      # noqa: E402
from app import runner                                            # noqa: E402
from app.detectors import available_detectors                     # noqa: E402
from app.export import export_predictions_json, export_run_report  # noqa: E402

#: scan, load, score, write, results - the headings printed as [n/5]
TOTAL_STEPS = 5


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        prog="detect.py",
        description="Score an image directory for AI-generated content. "
                    "Reports accuracy too when the folder is labeled.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("directory", nargs="?",
                    help="directory of images, searched recursively")
    ap.add_argument("--out", "-o", default="predictions.json",
                    help="output JSON path (default: predictions.json)")
    ap.add_argument("--detector", "-d", default=None,
                    help="registered detector name (default: best available)")
    ap.add_argument("--weights", "-w", default=None,
                    help="checkpoint to load (default: the backend's own, "
                         "e.g. models/bundle.pt)")
    ap.add_argument("--threshold", "-t", type=float, default=None,
                    help="decision threshold for the printed summary "
                         "(default: the detector's own operating point, or 0.5)")
    ap.add_argument("--tta", type=int, default=1, metavar="N",
                    help="test-time augmentation: average N views per image "
                         "(1 = off; 2 adds a flip; up to 4). Slower, steadier.")
    ap.add_argument("--best-threshold", action="store_true",
                    help="also report the threshold that maximises F1 (labeled only)")
    ap.add_argument("--require-labels", action="store_true",
                    help="fail instead of falling back to scores-only")
    ap.add_argument("--relative", action="store_true",
                    help="write paths relative to the input directory")
    ap.add_argument("--report", default=None,
                    help="also write a metrics/timing report to this JSON path")
    ap.add_argument("--quiet", "-q", action="store_true",
                    help="suppress progress output")
    ap.add_argument("--list-detectors", action="store_true",
                    help="print the registered backends and exit")
    return ap.parse_args(argv)


def _report(exc: SystemExit) -> int:
    """Print a SystemExit raised for bad input and turn it into exit code 2."""
    code = exc.code
    if isinstance(code, str):
        print(code, file=sys.stderr)
        return 2
    return int(code or 0)


def list_detectors() -> int:
    """--list-detectors: every backend, and whether it can currently run."""
    for cls in available_detectors():
        tag = (f"  [no checkpoint at {cls.default_weights}]"
               if cls.requires_weights and not cls.is_ready() else "")
        print(f"{cls.name:12s} {cls.display_name}{tag}")
        print(f"{'':12s} {cls.description}")
    print(f"\ndefault: {runner.default_detector_name()}")
    return 0


def main(argv=None) -> int:
    """Returns a process exit code: 0 on success, 2 for anything user-fixable."""
    args = parse_args(argv)

    if args.list_detectors:
        return list_detectors()

    if not args.directory:
        print("error: an image directory is required "
              "(use --list-detectors to inspect backends)", file=sys.stderr)
        return 2

    runner.QUIET = args.quiet

    def configure(detector):
        """Set TTA on the loaded backend, if it supports it and was asked for."""
        if args.tta and args.tta > 1:
            if hasattr(type(detector), "tta_views"):
                detector.tta_views = args.tta
                runner.log(f"test-time augmentation: {args.tta} views/image",
                           indent=6)
            else:
                runner.warn(f"{detector.display_name} does not support TTA - "
                            "scoring single-view")

    try:
        # steps 1-3: scan, load the detector, score
        dataset, result = runner.run_directory(
            args.directory, args.detector, args.weights,
            total_steps=TOTAL_STEPS, configure=configure)
    except SystemExit as exc:
        # the runner raises SystemExit(message) for bad input; report it as a
        # usage error (2) rather than the 1 a bare raise would give
        return _report(exc)

    if args.require_labels and not dataset.has_labels:
        print("\nerror: --require-labels was set but no ground-truth labels were "
              "found.\n"
              "       use real/ and ai/ subfolders, a labels.csv, or real_/ai_ "
              "prefixes.", file=sys.stderr)
        return 2

    # A calibrated model ships the threshold it was tuned for, and for this one
    # it is nowhere near 0.5. Honour it unless the caller asked for a specific
    # value. Only the printed summary moves - the JSON is always raw scores.
    threshold = args.threshold if args.threshold is not None else result.threshold

    # step 4: the deliverable. Written before the summary, so a slow metrics
    # print can never sit between a finished run and the file it produced.
    runner.step(4, TOTAL_STEPS, "writing")
    n = export_predictions_json(args.out, dataset, result, relative=args.relative,
                                threshold=threshold)
    runner.log(f"{n:,} predictions -> {os.path.abspath(args.out)}", indent=6)
    if args.report:
        export_run_report(args.report, dataset, result, threshold)
        runner.log(f"run report    -> {os.path.abspath(args.report)}", indent=6)

    runner.summarize(dataset, result, threshold,
                     total_steps=TOTAL_STEPS, step_no=5)

    # Informational only, and only where it is meaningful: an unlabeled folder
    # has no F1 to maximise. It does not move the threshold or the JSON.
    if args.best_threshold and dataset.has_labels:
        y, s = result.valid_pairs(dataset)
        best = M.best_threshold(y, s, "f1")
        bm = M.compute_metrics(y, s, best)
        runner.log("")
        runner.log(f"best F1 at threshold {best:.3f}", indent=6)
        runner.log(f"accuracy  {M.fmt(bm.accuracy)}      F1 {M.fmt(bm.f1, pct=False)}"
                   f"      FPR {M.fmt(bm.fpr)}", indent=6)

    runner.log("")
    runner.log(f"see it:  python gui.py {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
