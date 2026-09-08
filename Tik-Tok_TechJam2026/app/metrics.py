"""Evaluation metrics. Pure functions over (y_true, scores, threshold).

Label convention: 1 = AI-generated (positive class), 0 = authentic.
A false positive is therefore an authentic image flagged as AI, which is the
error mode that matters most for this problem statement - the shipped operating
point is chosen to hold it near 1%.

Everything here is a pure function of (y_true, scores, threshold), with no
dataset or detector types involved, so the CLI, the GUI and the sweep all get
identical numbers from the same code. sklearn is used when present and each
function carries a numpy fallback, so metrics never become the reason a run
cannot start.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np

try:
    from sklearn.metrics import roc_curve as _sk_roc, precision_recall_curve as _sk_pr
    from sklearn.metrics import roc_auc_score as _sk_auc, average_precision_score as _sk_ap
    HAVE_SKLEARN = True
except Exception:  # pragma: no cover - sklearn is in requirements
    HAVE_SKLEARN = False


@dataclass
class Metrics:
    """One evaluation at one threshold. NaN means "not defined on this data".

    NaN rather than 0 throughout: a precision with no positive predictions is
    undefined, not zero, and fmt() renders it as an em dash so the UI says so
    instead of showing a number nobody should read.
    """

    n: int = 0
    n_real: int = 0
    n_ai: int = 0
    threshold: float = 0.5
    accuracy: float = float("nan")
    balanced_accuracy: float = float("nan")
    precision: float = float("nan")
    recall: float = float("nan")          # = TPR = detection rate on AI images
    specificity: float = float("nan")     # = TNR on authentic images
    f1: float = float("nan")
    fpr: float = float("nan")             # authentic images wrongly flagged
    fnr: float = float("nan")
    auc: float = float("nan")
    ap: float = float("nan")
    tp: int = 0
    fp: int = 0
    tn: int = 0
    fn: int = 0

    @property
    def valid(self) -> bool:
        """False for an empty evaluation - nothing was labeled and scored."""
        return self.n > 0

    def as_dict(self) -> dict:
        """Flat dict for the JSON reports."""
        return asdict(self)


def _safe_div(a: float, b: float) -> float:
    """a / b, or NaN when the denominator is empty (undefined, not zero)."""
    return float(a) / float(b) if b else float("nan")


def compute_metrics(y_true, scores, threshold: float = 0.5) -> Metrics:
    """y_true and scores must be aligned and contain only labeled samples."""
    y = np.asarray(y_true, dtype=int)
    s = np.asarray(scores, dtype=float)
    if y.size == 0:
        return Metrics(threshold=threshold)

    # >= not >: an image sitting exactly on the threshold is called AI, which
    # matches how the threshold is described everywhere ("AI starts here")
    pred = (s >= threshold).astype(int)
    tp = int(np.sum((pred == 1) & (y == 1)))
    fp = int(np.sum((pred == 1) & (y == 0)))
    tn = int(np.sum((pred == 0) & (y == 0)))
    fn = int(np.sum((pred == 0) & (y == 1)))

    recall = _safe_div(tp, tp + fn)
    specificity = _safe_div(tn, tn + fp)
    precision = _safe_div(tp, tp + fp)
    # x == x is a NaN test: F1 is only defined when both parts are, and when
    # they do not sum to zero
    f1 = _safe_div(2 * precision * recall, precision + recall) if (
        precision == precision and recall == recall and (precision + recall) > 0) else float("nan")

    m = Metrics(
        n=int(y.size),
        n_real=int(np.sum(y == 0)),
        n_ai=int(np.sum(y == 1)),
        threshold=float(threshold),
        accuracy=_safe_div(tp + tn, y.size),
        balanced_accuracy=float(np.nanmean([recall, specificity])),
        precision=precision,
        recall=recall,
        specificity=specificity,
        f1=f1,
        fpr=_safe_div(fp, fp + tn),
        fnr=_safe_div(fn, fn + tp),
        auc=roc_auc(y, s),
        ap=average_precision(y, s),
        tp=tp, fp=fp, tn=tn, fn=fn,
    )
    return m


def _both_classes(y) -> bool:
    """AUC, AP and the curves need at least one of each class to mean anything."""
    y = np.asarray(y)
    return y.size > 0 and 0 < int(np.sum(y == 1)) < y.size


def roc_auc(y_true, scores) -> float:
    """Area under the ROC curve, threshold-free. NaN unless both classes exist."""
    y = np.asarray(y_true, dtype=int)
    s = np.asarray(scores, dtype=float)
    if not _both_classes(y):
        return float("nan")
    if HAVE_SKLEARN:
        return float(_sk_auc(y, s))
    # rank-based fallback (Mann-Whitney U): AUC is the probability that a random
    # AI image outranks a random real one, which is computable from ranks alone.
    # mergesort is the stable sort, so ties break the same way every run.
    order = np.argsort(s, kind="mergesort")
    ranks = np.empty_like(order, dtype=float)
    ranks[order] = np.arange(1, s.size + 1)
    n_pos = int(np.sum(y == 1))
    n_neg = s.size - n_pos
    return float((np.sum(ranks[y == 1]) - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def average_precision(y_true, scores) -> float:
    """Area under the precision-recall curve. More honest than AUC when the
    classes are imbalanced, which a real-world image folder usually is."""
    y = np.asarray(y_true, dtype=int)
    s = np.asarray(scores, dtype=float)
    if not _both_classes(y):
        return float("nan")
    if HAVE_SKLEARN:
        return float(_sk_ap(y, s))
    order = np.argsort(-s, kind="mergesort")
    y = y[order]
    tp = np.cumsum(y)
    prec = tp / np.arange(1, y.size + 1)
    return float(np.sum(prec * y) / max(int(np.sum(y)), 1))


def roc_points(y_true, scores):
    """Returns (fpr, tpr) arrays for plotting, or (None, None)."""
    y = np.asarray(y_true, dtype=int)
    s = np.asarray(scores, dtype=float)
    if not _both_classes(y):
        return None, None
    if HAVE_SKLEARN:
        fpr, tpr, _ = _sk_roc(y, s)
        return fpr, tpr
    thresholds = np.unique(np.concatenate([[-np.inf], np.sort(s), [np.inf]]))
    fpr, tpr = [], []
    n_pos, n_neg = int(np.sum(y == 1)), int(np.sum(y == 0))
    for t in thresholds[::-1]:
        pred = s >= t
        tpr.append(np.sum(pred & (y == 1)) / n_pos)
        fpr.append(np.sum(pred & (y == 0)) / n_neg)
    return np.array(fpr), np.array(tpr)


def pr_points(y_true, scores):
    """Returns (recall, precision) arrays for plotting, or (None, None)."""
    y = np.asarray(y_true, dtype=int)
    s = np.asarray(scores, dtype=float)
    if not _both_classes(y):
        return None, None
    if HAVE_SKLEARN:
        prec, rec, _ = _sk_pr(y, s)
        return rec, prec
    order = np.argsort(-s, kind="mergesort")
    ys = y[order]
    tp = np.cumsum(ys)
    prec = tp / np.arange(1, ys.size + 1)
    rec = tp / max(int(np.sum(ys)), 1)
    return rec, prec


def best_threshold(y_true, scores, criterion: str = "f1") -> float:
    """The threshold that maximises F1 (or Youden's J). Backs the Best F1 button.

    Only the observed scores can change the confusion matrix, so those are the
    candidates - not an arbitrary grid. Above 400 of them it switches to
    quantiles, because this is O(candidates x n) and it runs interactively.
    """
    y = np.asarray(y_true, dtype=int)
    s = np.asarray(scores, dtype=float)
    if not _both_classes(y):
        return 0.5
    candidates = np.unique(np.round(s, 4))
    if candidates.size > 400:
        candidates = np.quantile(s, np.linspace(0.0, 1.0, 400))
        candidates = np.unique(np.round(candidates, 4))
    best_t, best_v = 0.5, -np.inf
    for t in candidates:
        m = compute_metrics(y, s, float(t))
        v = m.f1 if criterion == "f1" else (m.recall - m.fpr)
        if v == v and v > best_v:
            best_v, best_t = v, float(t)
    return best_t


def threshold_for_fpr(y_true, scores, target_fpr: float = 0.01) -> float:
    """Lowest threshold whose FPR on authentic images stays within `target_fpr`.

    This is the operating point that matters for this problem: calling a real
    photo fake is the expensive error, so the shipped threshold is chosen for a
    false-positive budget (1% by default), not for accuracy. Raising the
    threshold only ever lowers both FPR and recall together, so the *lowest*
    threshold that already meets the budget is the best trade - it buys the most
    recall the budget allows.

    Returns a threshold just above the highest score (so nothing is flagged)
    when even that cannot be met, which only happens on data with no separation.
    """
    y = np.asarray(y_true, dtype=int)
    s = np.asarray(scores, dtype=float)
    if not _both_classes(y):
        return 0.5

    # candidate thresholds are the observed scores (only those change the
    # confusion matrix), scanned low -> high. FPR is monotonic non-increasing in
    # the threshold, so the first candidate meeting the budget is the smallest.
    for t in np.unique(s):
        if compute_metrics(y, s, float(t)).fpr <= target_fpr:
            return float(t)
    # unreachable in practice: at any t above every score, nothing is flagged
    # and FPR is 0 - but be explicit rather than fall through to None
    return float(np.nextafter(s.max(), np.inf))


def threshold_sweep(y_true, scores, n: int = 101):
    """Accuracy / F1 / FPR across thresholds, for the threshold explorer chart.

    An even grid over [0, 1] here, not the observed scores: this one is drawn as
    a curve against threshold, so the x axis has to be evenly spaced.
    """
    ts = np.linspace(0.0, 1.0, n)
    acc, f1, fpr = [], [], []
    for t in ts:
        m = compute_metrics(y_true, scores, float(t))
        acc.append(m.accuracy)
        f1.append(m.f1)
        fpr.append(m.fpr)
    return ts, np.array(acc), np.array(f1), np.array(fpr)


def fmt(value: float, pct: bool = True, digits: int = 1) -> str:
    """Format a metric for display; NaN renders as an em dash."""
    if value is None or value != value:
        return "—"
    if pct:
        return f"{value * 100:.{digits}f}%"
    return f"{value:.3f}"
