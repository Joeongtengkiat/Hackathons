"""Matplotlib canvases embedded in Qt (QtCharts is not installed).

One canvas class with a plot method per chart. Each method fully rebuilds its
figure from the data it is handed and holds no state, so a threshold change can
simply call it again; that redraw is what the 140 ms timer in AppWindow debounces.

Every method degrades to clear_to_message() rather than drawing an empty axis,
because a chart with no data and a chart of all-zeros look identical and only
one of them is honest.
"""

from __future__ import annotations

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

from .. import metrics as M
from .. import theme as T


class MplCanvas(FigureCanvasQTAgg):
    """A single-axis figure sized to sit inside a Card."""

    def __init__(self, title: str = "", height: float = 2.6, parent=None):
        # constrained layout copes with the small panels these canvases live in;
        # tight_layout warns and gives up below a certain size
        self.figure = Figure(figsize=(4.0, height), dpi=100, layout="constrained")
        super().__init__(self.figure)
        self.setParent(parent)
        self.ax = self.figure.add_subplot(111)
        self._title = title
        self.setMinimumHeight(180)
        self.setStyleSheet("background-color: transparent;")
        self.clear_to_message("No data yet")

    # -- helpers -----------------------------------------------------------
    def _reset(self):
        """Blank the figure and re-apply the chrome every plot shares.

        A full clear rather than ax.cla(): the confusion matrix sets ticks and
        turns the grid off, and those would survive into the next plot.
        """
        self.figure.clear()
        self.ax = self.figure.add_subplot(111)
        for spine in ("top", "right"):
            self.ax.spines[spine].set_visible(False)
        self.ax.spines["left"].set_color(T.BORDER_STRONG)
        self.ax.spines["bottom"].set_color(T.BORDER_STRONG)

    def _finish(self):
        """Schedule the repaint. draw_idle coalesces, draw() would not."""
        self.draw_idle()

    def clear_to_message(self, message: str):
        """Say why there is no chart, in the space the chart would occupy."""
        self._reset()
        self.ax.axis("off")
        self.ax.text(0.5, 0.5, message, ha="center", va="center",
                     color=T.TEXT_FAINT, fontsize=10, transform=self.ax.transAxes)
        self._finish()

    # -- plots -------------------------------------------------------------
    def plot_score_histogram(self, y_true, scores, threshold: float, unlabeled_scores=None):
        """Both classes' score distributions, with the threshold marked.

        The most diagnostic chart in the app: how far the two humps are apart is
        the model's separability, and where the dashed line falls between them
        is everything the threshold is doing.
        """
        self._reset()
        y = np.asarray(y_true, dtype=int)
        s = np.asarray(scores, dtype=float)
        bins = np.linspace(0, 1, 31)    # shared bins, or the humps cannot compare

        drew = False
        if s.size and y.size:
            if np.any(y == 0):
                self.ax.hist(s[y == 0], bins=bins, color=T.REAL_COLOR, alpha=0.82,
                             label="Authentic", edgecolor="none")
                drew = True
            if np.any(y == 1):
                self.ax.hist(s[y == 1], bins=bins, color=T.AI_COLOR, alpha=0.82,
                             label="AI-generated", edgecolor="none")
                drew = True
        if unlabeled_scores is not None and len(unlabeled_scores):
            self.ax.hist(np.asarray(unlabeled_scores, dtype=float), bins=bins,
                         color=T.TEXT_FAINT, alpha=0.65, label="Unlabelled",
                         edgecolor="none")
            drew = True

        if not drew:
            return self.clear_to_message("Run a detector to see the score distribution")

        self.ax.axvline(threshold, color=T.TEXT_DIM, linestyle="--", linewidth=1.1,
                        label=f"threshold {threshold:.3f}")
        self.ax.set_title("Score distribution")
        self.ax.set_xlabel("P(AI)")
        self.ax.set_ylabel("images")
        self.ax.legend(loc="upper center", ncol=2)
        self._finish()

    def plot_roc(self, y_true, scores):
        """ROC with the diagonal for reference. Threshold-free, so it does not
        move with the slider - which is the point of showing it."""
        self._reset()
        fpr, tpr = M.roc_points(y_true, scores)
        if fpr is None:
            return self.clear_to_message("ROC needs both classes labelled")
        auc = M.roc_auc(y_true, scores)
        self.ax.plot([0, 1], [0, 1], color=T.BORDER_STRONG, linestyle="--",
                     linewidth=1)
        self.ax.plot(fpr, tpr, color=T.ACCENT, linewidth=2)
        self.ax.fill_between(fpr, tpr, color=T.ACCENT, alpha=0.07)
        self.ax.set_title(f"ROC · AUC {auc:.3f}")
        self.ax.set_xlabel("false positive rate")
        self.ax.set_ylabel("true positive rate")
        self.ax.set_xlim(0, 1)
        self.ax.set_ylim(0, 1.02)
        self._finish()

    def plot_pr(self, y_true, scores):
        """Precision-recall. Not currently on a page; kept for imbalanced sets,
        where it says more than ROC does."""
        self._reset()
        rec, prec = M.pr_points(y_true, scores)
        if rec is None:
            return self.clear_to_message("PR curve needs both classes labelled")
        ap = M.average_precision(y_true, scores)
        self.ax.plot(rec, prec, color=T.SECONDARY, linewidth=2)
        self.ax.fill_between(rec, prec, color=T.SECONDARY, alpha=0.10)
        self.ax.set_title(f"Precision–Recall · AP {ap:.3f}")
        self.ax.set_xlabel("recall")
        self.ax.set_ylabel("precision")
        self.ax.set_xlim(0, 1)
        self.ax.set_ylim(0, 1.02)
        self._finish()

    def plot_confusion(self, m: M.Metrics):
        """The four counts at the current threshold, laid out as the matrix."""
        self._reset()
        if not m.valid:
            return self.clear_to_message("Confusion matrix needs labels")
        # rows are truth, columns are prediction - the conventional layout,
        # which puts the correct answers on the diagonal
        mat = np.array([[m.tn, m.fp], [m.fn, m.tp]], dtype=float)
        norm = mat / max(mat.max(), 1)      # max(_, 1) guards an all-zero matrix

        # theme colours instead of a loud colormap: green = correct, red = error,
        # opacity carries the count so the diagonal reads at a glance
        rgba = np.zeros((2, 2, 4))
        for i in range(2):
            for j in range(2):
                hex_color = T.GOOD if i == j else T.BAD
                r, g, b = (int(hex_color[k:k + 2], 16) / 255 for k in (1, 3, 5))
                rgba[i, j] = (r, g, b, 0.08 + 0.30 * norm[i, j])
        self.ax.imshow(rgba, aspect="auto")     # fill the panel, don't float in it

        labels = [["TN", "FP"], ["FN", "TP"]]
        for i in range(2):
            for j in range(2):
                self.ax.text(j, i - 0.13, labels[i][j], ha="center", va="center",
                             color=T.TEXT_FAINT, fontsize=9, fontweight="700")
                self.ax.text(j, i + 0.13, f"{int(mat[i, j])}", ha="center", va="center",
                             color=T.TEXT, fontsize=16, fontweight="700")
        self.ax.set_xticks([0, 1], ["pred: real", "pred: AI"])
        self.ax.set_yticks([0, 1], ["true: real", "true: AI"])
        self.ax.set_title("Confusion matrix")
        self.ax.grid(False)
        self._finish()

    def plot_threshold_sweep(self, y_true, scores, threshold: float):
        """Accuracy, F1 and FPR as functions of the threshold. Not currently on
        a page; the header slider plus the histogram cover the same ground."""
        self._reset()
        y = np.asarray(y_true)
        if y.size == 0 or len(set(y.tolist())) < 2:
            return self.clear_to_message("Threshold sweep needs both classes labelled")
        ts, acc, f1, fpr = M.threshold_sweep(y_true, scores)
        self.ax.plot(ts, acc, color=T.ACCENT, linewidth=1.8, label="accuracy")
        self.ax.plot(ts, f1, color=T.SECONDARY, linewidth=1.6, label="F1")
        self.ax.plot(ts, fpr, color=T.WARN, linewidth=1.4, label="FPR")
        self.ax.axvline(threshold, color=T.TEXT_DIM, linestyle="--", linewidth=1.0)
        self.ax.set_title("Metrics vs threshold")
        self.ax.set_xlabel("threshold")
        self.ax.set_xlim(0, 1)
        self.ax.set_ylim(0, 1.02)
        self.ax.legend(loc="lower left", ncol=3)
        self._finish()

    def plot_degradation(self, series: dict, baseline: float, metric_name: str,
                         severity_labels: dict = None):
        """One line per transform, severity on x. series: {name: {severity: val}}.

        The clean baseline is a horizontal rule rather than a series, because it
        has no severity axis - it is the level every line is falling away from.
        """
        self._reset()
        if not series:
            return self.clear_to_message("Run a robustness sweep to see degradation")

        # a categorical set for a dark ground: every hue is light enough to
        # hold as a 1.8px line, where the deeper tones tuned for white vanished
        colors = [T.ACCENT, T.SECONDARY, T.WARN, T.GOOD, "#A78BFA",
                  "#FB923C", "#60A5FA", "#F472B6", "#A3E635", "#94A3B8"]
        if baseline == baseline:
            self.ax.axhline(baseline, color=T.TEXT_DIM, linestyle="--", linewidth=1.2,
                            label="clean baseline")
        for i, (name, points) in enumerate(sorted(series.items())):
            xs = sorted(points.keys())
            ys = [points[x] for x in xs]
            self.ax.plot(xs, ys, marker="o", markersize=4, linewidth=1.8,
                         color=colors[i % len(colors)], label=name)
        self.ax.set_title("Robustness")
        self.ax.set_xlabel("severity")
        self.ax.set_ylabel(metric_name.lower())
        self.ax.set_xticks([1, 2, 3, 4, 5])
        self.ax.set_ylim(0, 1.02)
        self.ax.legend(loc="lower left", fontsize=7, ncol=2)
        self._finish()
