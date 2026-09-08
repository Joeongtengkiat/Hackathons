"""Scoring pipeline with terminal logging, shared by every entry point.

All the work - scanning, loading the model, scoring, timing - happens here and
reports to stdout. The GUI never runs any of it in the background; it opens on
a finished result.

Two listeners, deliberately: log()/step() write to the terminal for everyone,
while an optional `progress` callback lets the GUI's working screen narrate the
same run. The callback is a plain argument rather than module state, so two
runs can never narrate into each other.

Step numbering (`total_steps`) differs per entry point - detect.py takes 5 steps
because it also writes files, robustness.py takes 4 - which is why every
function takes it rather than assuming.
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
from dataclasses import dataclass, field

from .dataset import Dataset, ImageItem, LabelMode, apply_labels, scan_directory
from .detectors import available_detectors, get_detector, weights_detectors


# --------------------------------------------------------------------------- #
# logging
# --------------------------------------------------------------------------- #

#: set by --quiet. Only silences log()/step(); warn() still goes to stderr,
#: because a caller that asked for quiet still wants to know what broke.
QUIET = False


def _make_console_utf8_safe() -> None:
    """Stop a legacy console codepage from killing a run.

    A Windows console defaults to cp1252, which cannot encode a sigma in a
    transform label - or, far worse, a CJK/emoji character in someone's
    filename. Without this, printing such a path raises UnicodeEncodeError
    mid-run and loses the work.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_make_console_utf8_safe()


def log(message: str = "", indent: int = 0) -> None:
    """A line of terminal output. flush=True so a piped run stays live."""
    if not QUIET:
        print(" " * indent + message, flush=True)


def step(n: int, total: int, message: str) -> None:
    """A numbered heading: [2/5] loading detector."""
    log(f"[{n}/{total}] {message}")


def warn(message: str) -> None:
    """A problem that did not stop the run. Ignores QUIET, and goes to stderr."""
    print("  ! " + message, file=sys.stderr, flush=True)


def _noop_progress(phase: str, detail: str = "", done: int = 0,
                   total: int = 0, note: str = "") -> None:
    """The default listener: nothing is watching, so nothing is reported.

    Every entry point calls the pipeline the same way; only the GUI passes a
    real callback. Keeping it a plain argument rather than module state means
    two runs can never narrate into each other.
    """


def _eta_text(done: int, total: int, started: float) -> str:
    """Rate and time-remaining, in the form the working screen shows them."""
    elapsed = time.perf_counter() - started
    if done <= 0 or elapsed <= 0:
        return ""
    rate = done / elapsed
    remaining = (total - done) / rate if rate > 0 else 0.0
    if remaining < 1:
        return f"{rate:.1f} img/s"
    minutes, seconds = divmod(int(remaining), 60)
    left = f"{minutes}:{seconds:02d}" if minutes else f"{seconds}s"
    return f"{rate:.1f} img/s · {left} left"


def _progress(done: int, total: int, started: float) -> None:
    """The terminal progress bar. Redraws in place until the final line."""
    if QUIET:
        return
    rate = done / max(time.perf_counter() - started, 1e-6)
    pct = 100.0 * done / total
    bar_len = 24
    filled = int(bar_len * done / total)
    bar = "#" * filled + "-" * (bar_len - filled)
    # carriage return until the last update, so one line is rewritten
    # instead of thousands being appended
    end = "\n" if done >= total else "\r"
    print(f"      [{bar}] {done:>6,}/{total:,} ({pct:3.0f}%)  {rate:6.1f} img/s",
          end=end, flush=True)


# --------------------------------------------------------------------------- #
# result container
# --------------------------------------------------------------------------- #

@dataclass
class RunResult:
    """Everything one scoring pass produced, aligned with a Dataset.

    `scores[i]` belongs to `dataset.items[i]`; an image that could not be scored
    holds NaN here and is written as 0.5 by the exporter, so the two lists never
    drift out of step.
    """

    detector_name: str = ""
    detector_display: str = ""
    scores: list = field(default_factory=list)      # aligned with dataset.items
    elapsed: float = 0.0
    failures: list = field(default_factory=list)    # (path, message)
    source: str = ""                                # directory or json file
    threshold: float = 0.5                          # the detector's own operating point

    @property
    def n_scored(self) -> int:
        """How many images actually produced a number (NaN = failed to score)."""
        return sum(1 for s in self.scores if s is not None and not math.isnan(s))

    def valid_pairs(self, dataset: Dataset):
        """(y_true, score) for items that are both labeled and scored."""
        y, s = [], []
        for item, score in zip(dataset.items, self.scores):
            if item.label is None or score is None or math.isnan(score):
                continue
            y.append(item.label)
            s.append(score)
        return y, s


# --------------------------------------------------------------------------- #
# detector selection
# --------------------------------------------------------------------------- #

def default_detector_name(weights: str = None) -> str:
    """The best backend available right now.

    available_detectors() ranks a backend whose checkpoint is present above
    one whose checkpoint is missing, so this returns the model that can
    actually run. A bare --weights picks the backend that takes one, since that
    is unambiguously what was meant.
    """
    if weights:
        takers = weights_detectors()
        if takers:
            return takers[0].name
    detectors = available_detectors()
    if not detectors:
        raise RuntimeError("no detectors registered")
    return detectors[0].name


def load_detector(name: str = None, weights: str = None, progress_cb=None):
    """Instantiate a backend and load it. Raises SystemExit on bad input.

    SystemExit rather than a custom exception because every caller - the two CLI
    scripts and the Qt workers - already handles it, and its payload is the
    message a user should read.
    """
    name = name or default_detector_name(weights)
    try:
        detector = get_detector(name, weights)
    except KeyError:
        names = ", ".join(c.name for c in available_detectors())
        raise SystemExit(f"error: unknown detector {name!r}. Available: {names}")

    if not type(detector).is_ready(detector.weights):
        expected = detector.resolve_weights(detector.weights)
        raise SystemExit(
            f"error: {detector.display_name} needs a checkpoint, and none is at "
            f"{os.path.abspath(expected)}\n"
            f"       put one there, pass --weights <file>, or pick another "
            f"backend with --detector.")

    # Set on the instance, not the class: ensure_loaded() is where the slow
    # work happens, so the listener has to be in place before it is called.
    detector.progress_cb = progress_cb
    detector.ensure_loaded()
    return detector


# --------------------------------------------------------------------------- #
# the run
# --------------------------------------------------------------------------- #

def scan(directory: str, total_steps: int = 4,
         label_mode: LabelMode = LabelMode.AUTO, progress=None) -> Dataset:
    """Scan a folder. LabelMode.NONE deliberately ignores any labels present.

    The GUI passes NONE when you said you were uploading plain images, so a
    folder that happens to hold real/ and ai/ is still scored as unlabeled -
    the screen you land on is the one you asked for.
    """
    progress = progress or _noop_progress
    step(1, total_steps, f"scanning  {os.path.abspath(directory)}")
    if not os.path.isdir(directory):
        raise SystemExit(f"error: not a directory: {directory}")

    progress("scan", f"walking {os.path.abspath(directory)}")
    started = time.perf_counter()
    ds = scan_directory(directory, mode=label_mode)
    took = time.perf_counter() - started

    if not ds.items:
        raise SystemExit(f"error: no images found under {directory}")

    log(f"{len(ds):,} images"
        + (f"  ({ds.skipped} non-image files skipped)" if ds.skipped else "")
        + f"  in {took:.1f}s", indent=6)
    if ds.has_labels:
        log(f"{ds.n_real:,} authentic   |   {ds.n_ai:,} AI"
            + (f"   |   {ds.n_unlabeled:,} unlabeled" if ds.n_unlabeled else ""), indent=6)
        log(f"labels from {ds.label_source_detail}", indent=6)
    elif label_mode == LabelMode.NONE:
        log("labels ignored - scoring as unlabeled", indent=6)
    else:
        log("no labels found - scores only, no accuracy metrics", indent=6)

    found = f"{len(ds):,} images"
    if ds.has_labels:
        found += f" · {ds.n_real:,} real / {ds.n_ai:,} AI"
    elif label_mode == LabelMode.NONE:
        found += " · labels ignored, scoring blind"
    else:
        found += " · no labels, scores only"
    progress("scan", found, len(ds), len(ds))
    return ds


def score(ds: Dataset, detector, total_steps: int = 4, progress=None) -> RunResult:
    """Score every item in `ds`, batch by batch, reporting as it goes.

    Batching is the detector's call (`batch_size`); a smaller batch costs a
    little throughput and buys a smoother progress bar.
    """
    progress = progress or _noop_progress
    step(3, total_steps, "scoring")
    paths = [it.path for it in ds.items]
    scores, failures = [], []
    bs = max(1, int(getattr(detector, "batch_size", 16)))
    n_batches = (len(paths) + bs - 1) // bs
    started = time.perf_counter()
    progress("score", f"0 of {len(paths):,} images · {bs} per batch",
             0, len(paths))

    for start in range(0, len(paths), bs):
        chunk = paths[start:start + bs]
        try:
            batch = [float(s) for s in detector.predict_batch(chunk)]
            # a backend that returns the wrong count would silently shift every
            # later score onto the wrong image - pad or truncate instead
            if len(batch) != len(chunk):
                batch = (batch + [float("nan")] * len(chunk))[:len(chunk)]
        except Exception as exc:                    # a bad batch must not kill the run
            failures.extend((p, str(exc)) for p in chunk)
            batch = [float("nan")] * len(chunk)
        scores.extend(batch)
        done = min(start + bs, len(paths))
        _progress(done, len(paths), started)
        progress("score",
                 f"batch {start // bs + 1:,} of {n_batches:,}"
                 f" · {bs} images per batch"
                 + (f" · {len(failures)} failed" if failures else ""),
                 done, len(paths), _eta_text(done, len(paths), started))

    elapsed = time.perf_counter() - started
    result = RunResult(
        detector_name=detector.name,
        detector_display=detector.display_name,
        scores=scores, elapsed=elapsed, failures=failures,
        source=ds.root,
        threshold=float(getattr(detector, "default_threshold", 0.5)),
    )
    nan_count = len(scores) - result.n_scored
    log(f"scored {result.n_scored:,} images in {elapsed:.1f}s", indent=6)
    if nan_count:
        warn(f"{nan_count} image(s) could not be scored - written as 0.5")
        for path, msg in failures[:5]:
            log(f"- {os.path.basename(path)}: {msg}", indent=8)
    return result


def prepare_detector(name: str = None, weights: str = None, total_steps: int = 4,
                     progress=None):
    """Resolve, announce and load a backend. Returns the loaded detector."""
    progress = progress or _noop_progress
    step(2, total_steps, "loading detector")

    # Name the backend before loading it, not after. Loading is the slow part
    # and now narrates itself, and that narration reads as noise under a
    # heading that has not been printed yet.
    resolved = name or default_detector_name(weights)
    cls = next((c for c in available_detectors() if c.name == resolved), None)
    if cls is not None:
        log(cls.display_name, indent=6)
        progress("model", cls.display_name)
        if cls.requires_weights:
            log(f"weights {os.path.abspath(cls.resolve_weights(weights))}",
                indent=6)

    def relay(message: str) -> None:
        """Anything the detector says while loading goes to both listeners."""
        log(message, indent=6)
        progress("model", message)

    return load_detector(resolved, weights, progress_cb=relay)


def run_directory(directory: str, detector_name: str = None, weights: str = None,
                  total_steps: int = 4, label_mode: LabelMode = LabelMode.AUTO,
                  progress=None, configure=None):
    """scan -> load detector -> score. Returns (Dataset, RunResult).

    `configure(detector)` runs after the backend is loaded and before scoring -
    the seam a caller uses to set an inference option such as TTA on the loaded
    instance, without this function having to know about it.
    """
    ds = scan(directory, total_steps, label_mode, progress=progress)
    detector = prepare_detector(detector_name, weights, total_steps,
                                progress=progress)
    if configure is not None:
        configure(detector)
    result = score(ds, detector, total_steps, progress=progress)
    return ds, result


# --------------------------------------------------------------------------- #
# reading a finished predictions.json back in (GUI visualisation path)
# --------------------------------------------------------------------------- #

def load_predictions(json_path: str, total_steps: int = 2, progress=None):
    """Rebuild (Dataset, RunResult) from a predictions.json written earlier."""
    progress = progress or _noop_progress
    step(1, total_steps, f"reading  {os.path.abspath(json_path)}")
    progress("read", os.path.basename(json_path))
    with open(json_path, "r", encoding="utf-8") as f:
        records = json.load(f)
    if not isinstance(records, list) or not records:
        raise SystemExit(f"error: {json_path} is not a non-empty list of records")

    paths, scores = [], []
    for rec in records:
        if not isinstance(rec, dict) or "image_path" not in rec or "pred" not in rec:
            raise SystemExit('error: every record needs "image_path" and "pred"')
        paths.append(os.path.abspath(rec["image_path"]))
        try:
            scores.append(float(rec["pred"]))
        except (TypeError, ValueError):
            scores.append(float("nan"))

    root = _common_root(paths)
    items = []
    for p in paths:
        try:
            size = os.path.getsize(p)
        except OSError:
            size = 0
        rel = os.path.relpath(p, root).replace("\\", "/") if root else os.path.basename(p)
        items.append(ImageItem(path=p, rel_path=rel, size_bytes=size))

    ds = Dataset(root=root, items=items)
    apply_labels(ds)

    log(f"{len(items):,} predictions", indent=6)
    if ds.has_labels:
        log(f"{ds.n_real:,} authentic   |   {ds.n_ai:,} AI"
            f"   |   labels from {ds.label_source_detail}", indent=6)
    else:
        log("no labels recoverable from the paths - scores only", indent=6)

    missing = sum(1 for p in paths if not os.path.isfile(p))
    if missing:
        warn(f"{missing} image file(s) referenced in the JSON no longer exist "
             "- previews will be blank")

    result = RunResult(detector_name="(from file)",
                       detector_display=os.path.basename(json_path),
                       scores=scores, source=os.path.abspath(json_path))
    return ds, result


def _common_root(paths: list) -> str:
    """Deepest folder containing every path, for rebuilding rel_path from a JSON.

    Returns "" when the paths span drives, which is not an error - it just means
    the table shows basenames instead of relative paths.
    """
    try:
        root = os.path.commonpath(paths)
    except ValueError:                       # different drives
        return ""
    return root if os.path.isdir(root) else os.path.dirname(root)


def summarize(ds: Dataset, result: RunResult, threshold: float = 0.5,
              total_steps: int = 4, step_no: int = 4) -> None:
    """Print the final metrics block."""
    from . import metrics as M

    step(step_no, total_steps, "results")
    if not ds.has_labels:
        flagged = sum(1 for s in result.scores
                      if not math.isnan(s) and s >= threshold)
        log(f"{flagged:,} of {result.n_scored:,} images flagged as AI "
            f"at threshold {threshold:.3f}", indent=6)
        log("no ground-truth labels - accuracy cannot be measured", indent=6)
        return

    y, s = result.valid_pairs(ds)
    m = M.compute_metrics(y, s, threshold)
    log(f"threshold {threshold:.3f}", indent=6)
    log(f"accuracy  {M.fmt(m.accuracy)}      AUC {M.fmt(m.auc, pct=False)}      "
        f"F1 {M.fmt(m.f1, pct=False)}", indent=6)
    log(f"precision {M.fmt(m.precision)}      recall {M.fmt(m.recall)}      "
        f"FPR {M.fmt(m.fpr)}", indent=6)
    log(f"TP {m.tp}   FP {m.fp}   TN {m.tn}   FN {m.fn}", indent=6)
