"""Robustness sweep core, shared by robustness.py (CLI) and the GUI.

Applies each (transform, severity) cell in memory and re-scores the sample,
always against a clean baseline measured through the same pipeline.

The baseline is not optional and is not taken from the main run: a cell's number
is only meaningful next to a clean number produced by the same detector, the
same sample and the same decode cap. Reporting a drop against a differently
measured baseline is the easiest way to publish a wrong figure here.
"""

from __future__ import annotations

import json
import os
import random
import time
from dataclasses import dataclass, field
from datetime import datetime

from PIL import Image

from . import metrics as M
from . import runner
from .transforms import TRANSFORMS_BY_KEY

#: written next to the dataset, and picked up automatically by the GUI
DEFAULT_REPORT = "robustness_report.json"

#: fixed, so two sweeps of the same folder compare like with like. A sweep that
#: resampled every run would show noise as robustness.
SAMPLE_SEED = 20260829


@dataclass
class SweepResult:
    """One sweep: a clean baseline plus a metrics block per (transform, severity).

    `cells` is keyed by that pair, and every delta anywhere in the app is
    computed against `baseline` - never cell against cell.
    """

    threshold: float = 0.5
    baseline: M.Metrics = None
    cells: dict = field(default_factory=dict)      # (key, severity) -> Metrics
    n_images: int = 0
    elapsed: float = 0.0
    detector_display: str = ""
    dataset_root: str = ""

    # -- views -------------------------------------------------------------
    def to_view(self) -> dict:
        """The shape the charts and the robustness table want."""
        series, rows = {}, []
        for (key, severity), m in sorted(self.cells.items()):
            spec = TRANSFORMS_BY_KEY.get(key)
            name = spec.display_name if spec else key
            rows.append({
                "name": name,
                "severity": severity,
                "severity_label": spec.label_for(severity) if spec else str(severity),
                "accuracy": m.accuracy,
                "auc": m.auc,
            })
            # x == x is a NaN test: a cell where every image failed to decode
            # has no accuracy, and plotting it would put a false zero on the
            # curve. It stays in the table, and out of the chart.
            if m.accuracy == m.accuracy:
                series.setdefault(name, {})[severity] = m.accuracy
        base = self.baseline.accuracy if self.baseline else float("nan")
        return {"series": series, "cells": rows, "baseline": base, "metric": "Accuracy"}

    def to_report(self) -> dict:
        """The full JSON report: every cell, its metrics, and its delta."""
        cells = []
        for (key, severity), m in sorted(self.cells.items()):
            spec = TRANSFORMS_BY_KEY.get(key)
            entry = {
                "transform": key,
                "transform_name": spec.display_name if spec else key,
                "severity": severity,
                "severity_label": spec.label_for(severity) if spec else str(severity),
                "metrics": m.as_dict(),
            }
            if self.baseline:
                entry["delta_accuracy"] = _delta(m.accuracy, self.baseline.accuracy)
                entry["delta_auc"] = _delta(m.auc, self.baseline.auc)
            cells.append(entry)
        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "dataset_root": self.dataset_root,
            "detector": self.detector_display,
            "threshold": self.threshold,
            "sample_size": self.n_images,
            "elapsed_seconds": round(self.elapsed, 2),
            "baseline": self.baseline.as_dict() if self.baseline else None,
            "cells": cells,
        }

    def write(self, path: str) -> str:
        """Write the report and return the absolute path written."""
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_report(), f, indent=2)
        return os.path.abspath(path)

    # -- summary -----------------------------------------------------------
    def worst(self):
        """(delta, key, severity, metrics) of the harshest accuracy drop."""
        if not self.baseline:
            return None
        drops = [(m.accuracy - self.baseline.accuracy, k, sv, m)
                 for (k, sv), m in self.cells.items() if m.accuracy == m.accuracy]
        return min(drops, key=lambda t: t[0]) if drops else None

    def mean_drop(self) -> float:
        """Average accuracy change across every scored cell, in points."""
        if not self.baseline:
            return float("nan")
        drops = [m.accuracy - self.baseline.accuracy
                 for m in self.cells.values() if m.accuracy == m.accuracy]
        return sum(drops) / len(drops) if drops else float("nan")


def _delta(a, b):
    """a - b, or None if either is NaN. None serialises; NaN does not."""
    if a != a or b != b:
        return None
    return round(a - b, 6)


# --------------------------------------------------------------------------- #
# sampling and image loading
# --------------------------------------------------------------------------- #

def build_sample(dataset, n: int):
    """Balanced, deterministic subset of the labeled images.

    Balanced because accuracy on a lopsided sample is not comparable across
    cells, and deterministic because the same images have to be used in every
    cell of the grid - otherwise a drop could just be a harder sample.
    """
    labeled = [i for i, it in enumerate(dataset.items) if it.label is not None]
    if not labeled:
        return [], []
    rng = random.Random(SAMPLE_SEED)
    reals = [i for i in labeled if dataset.items[i].label == 0]
    ais = [i for i in labeled if dataset.items[i].label == 1]
    rng.shuffle(reals)
    rng.shuffle(ais)
    # n images total, so n//2 per class - capped by whichever class is smaller.
    # The `or n` handles a single-class folder: take what there is rather than
    # returning nothing.
    half = max(1, min(n // 2, min(len(reals), len(ais)) or n))
    picked = sorted(reals[:half] + ais[:half])
    return ([dataset.items[i].path for i in picked],
            [dataset.items[i].label for i in picked])


def load_image(path: str, max_side: int) -> Image.Image:
    """Decode with a size cap. The cap is what makes a 50-cell sweep finish.

    A sweep decodes the same image once per cell, so full-resolution decoding
    dominates the runtime. draft() lets libjpeg do the downscale during decode,
    which is far cheaper than decoding and then resizing.
    """
    img = Image.open(path)
    try:
        img.draft("RGB", (max_side, max_side))
    except Exception:
        pass
    img = img.convert("RGB")
    if max(img.size) > max_side:
        s = max_side / max(img.size)
        img = img.resize((max(1, int(img.width * s)), max(1, int(img.height * s))),
                         Image.BILINEAR)
    return img


def score_cell(detector, paths, labels, spec, severity: int, max_side: int):
    """Transform and re-score one cell. Returns (labels, scores).

    Labels are returned rather than assumed, because an image that fails to
    decode drops out of the batch - the two lists have to stay aligned for the
    metrics to mean anything.
    """
    bs = max(1, int(getattr(detector, "batch_size", 16)))
    scores, kept = [], []
    for start in range(0, len(paths), bs):
        chunk = paths[start:start + bs]
        images, chunk_labels = [], []
        for j, p in enumerate(chunk):
            try:
                img = load_image(p, max_side)
                # before the transform, not after: training conditioned the
                # source first and degraded second
                img = detector.prepare_source(img)
                img = spec.apply(img, severity) if severity else img
                images.append(img)
                chunk_labels.append(labels[start + j])
            except Exception as exc:
                # one bad file must not lose the cell; it is dropped from both
                # lists together, so labels and scores stay in step
                runner.warn(f"skipped {os.path.basename(p)}: {exc}")
        if not images:
            continue
        scores.extend(float(s) for s in detector.predict_images(images))
        kept.extend(chunk_labels)
    return kept, scores


# --------------------------------------------------------------------------- #
# the sweep
# --------------------------------------------------------------------------- #

def run_sweep(dataset, detector, cells: list, sample: int = 200,
              max_side: int = 768, threshold: float = 0.5,
              on_cell=None, should_stop=None) -> SweepResult:
    """cells: [(transform_key, severity)]. A clean baseline is always measured.

    on_cell(index, total, name, metrics) is called after each cell so a GUI can
    fill in progressively; should_stop() lets a caller cancel between cells.
    """
    paths, labels = build_sample(dataset, sample)
    if not paths:
        raise ValueError("no labeled images to sweep")

    # the baseline always runs, always first, and cannot be requested twice
    all_cells = [("clean", 0)] + [c for c in cells if c[0] != "clean"]
    out = SweepResult(
        threshold=threshold, n_images=len(paths),
        detector_display=detector.display_name,
        dataset_root=dataset.root,
    )
    started = time.perf_counter()

    for i, (key, severity) in enumerate(all_cells, 1):
        if should_stop is not None and should_stop():
            break
        spec = TRANSFORMS_BY_KEY.get(key)
        if spec is None:
            continue
        name = ("clean baseline" if key == "clean"
                else f"{spec.display_name}  |  {spec.label_for(severity)}")
        t0 = time.perf_counter()
        kept, scores = score_cell(detector, paths, labels, spec, severity, max_side)
        m = M.compute_metrics(kept, scores, threshold)

        if key == "clean":
            out.baseline = m
            delta = ""
        else:
            out.cells[(key, severity)] = m
            d = m.accuracy - out.baseline.accuracy if out.baseline else float("nan")
            delta = (f"  d {'+' if d >= 0 else '-'}{abs(d) * 100:4.1f}pp"
                     if d == d else "")

        runner.log(f"  [{i:>2}/{len(all_cells)}] {name:<44} "
                   f"acc {M.fmt(m.accuracy):>6}  auc {M.fmt(m.auc, pct=False):>5}"
                   f"{delta}   ({time.perf_counter() - t0:.1f}s)")
        if on_cell is not None:
            on_cell(i, len(all_cells), name, m)

    out.elapsed = time.perf_counter() - started
    return out


# --------------------------------------------------------------------------- #
# reading a report back for display
# --------------------------------------------------------------------------- #

def load_report_view(report_path: str) -> dict:
    """Parse a robustness_report.json into the shape the charts want.

    Returns {} for a missing or unreadable file rather than raising: this runs
    speculatively after every run, on a report that usually is not there.
    """
    if not os.path.isfile(report_path):
        return {}
    try:
        with open(report_path, "r", encoding="utf-8") as f:
            report = json.load(f)
    except Exception as exc:
        runner.warn(f"could not read {report_path}: {exc}")
        return {}

    series, rows = {}, []
    for cell in report.get("cells", []):
        spec = TRANSFORMS_BY_KEY.get(cell.get("transform"))
        name = spec.display_name if spec else cell.get("transform", "?")
        metrics = cell.get("metrics") or {}
        acc = metrics.get("accuracy")
        rows.append({
            "name": name,
            "severity": cell.get("severity", 0),
            "severity_label": cell.get("severity_label", ""),
            "accuracy": acc if acc is not None else float("nan"),
            "auc": metrics.get("auc", float("nan")),
        })
        if acc is not None and acc == acc:
            series.setdefault(name, {})[cell.get("severity", 0)] = acc

    baseline = (report.get("baseline") or {}).get("accuracy", float("nan"))
    return {"series": series, "cells": rows, "baseline": baseline,
            "metric": "Accuracy", "source": os.path.abspath(report_path)}
