"""The results pages. Each reads state from the AppWindow passed in as `app`.

The upload screen lives in upload.py and the unlabeled verdict grid in
gallery.py; these three are the labeled destinations.

None of them stores results. Each `refresh()` re-reads app.dataset, app.result
and app.threshold, which is what lets the header slider drive every view at once
without any page having to be told what changed.
"""

from __future__ import annotations

import math
import os

from PyQt6.QtCore import QSortFilterProxyModel, Qt
from PyQt6.QtGui import QBrush, QColor, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QPushButton, QScrollArea, QSizePolicy, QSlider, QSpinBox, QSplitter,
    QTableView, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget
)

from .. import metrics as M
from .. import theme as T
from ..transforms import TRANSFORMS
from . import components as C
from .charts import MplCanvas
from .components import Card, Chip, Hint, SectionTitle, StatCard, score_color
from .table import COLUMNS, ROLE_INDEX, ResultsModel, ScoreBarDelegate, label_text

#: the Images page filters. FP and FN need ground truth; the other three are
#: purely a function of the threshold.
FILTER_ALL, FILTER_FP, FILTER_FN, FILTER_AI, FILTER_REAL = range(5)


def delta_text(value: float) -> str:
    """A robustness delta in percentage points, signed. NaN -> em dash."""
    if value is None or value != value:
        return "—"
    return f"{'+' if value >= 0 else '−'}{abs(value) * 100:.1f}pp"


def delta_color(value: float) -> str:
    """Green / amber / red for a drop. The bands are judgements, and they are:
    within 2 points is noise, 10 points is a warning, worse than that is a
    detector you should not rely on under that transform."""
    if value is None or value != value:
        return T.TEXT_FAINT
    if value >= -0.02:
        return T.GOOD
    if value >= -0.10:
        return T.WARN
    return T.BAD


# --------------------------------------------------------------------------- #
# 1. Insights
# --------------------------------------------------------------------------- #

class InsightsPage(QWidget):
    """Metrics, distribution, ROC and confusion - labeled data only.

    Unlabelled data never reaches here; it goes to the verdict gallery,
    which is why nothing on this page has an empty-truth branch.
    """

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)

        cards = QHBoxLayout()
        cards.setSpacing(12)
        self.card_acc = StatCard("Accuracy")
        self.card_auc = StatCard("AUC")
        self.card_f1 = StatCard("F1")
        self.card_fpr = StatCard("False positives")
        for c in (self.card_acc, self.card_auc, self.card_f1, self.card_fpr):
            cards.addWidget(c)
        lay.addLayout(cards)

        self.grid = QGridLayout()
        self.grid.setSpacing(12)
        self.chart_hist = MplCanvas()
        self.chart_roc = MplCanvas()
        self.chart_cm = MplCanvas()
        cards = []
        for canvas, pos in ((self.chart_hist, (0, 0, 1, 2)),
                            (self.chart_roc, (1, 0, 1, 1)),
                            (self.chart_cm, (1, 1, 1, 1))):
            wrapper = Card(padding=8)
            wrapper.layout().addWidget(canvas)
            self.grid.addWidget(wrapper, *pos)
            cards.append(wrapper)
        self.hist_card, self.roc_card, self.cm_card = cards
        self.grid.setRowStretch(0, 5)
        self.grid.setRowStretch(1, 6)
        lay.addLayout(self.grid, 1)

    def refresh(self, charts: bool = True):
        """Re-read the metrics; redraw the figures only when asked.

        `charts=False` is the slider-drag path: the cards are cheap, the three
        matplotlib redraws are not.
        """
        app = self.app
        if app.result is None or not app.dataset.has_labels:
            for card in (self.card_acc, self.card_auc, self.card_f1, self.card_fpr):
                card.set_value("—", "")
            if charts:
                for canvas in (self.chart_hist, self.chart_roc, self.chart_cm):
                    canvas.clear_to_message("Run something first")
            return

        self._update_cards()
        if charts:
            self._draw_charts()

    def _update_cards(self):
        """The four headline numbers at the current threshold."""
        app = self.app
        m = M.compute_metrics(*app.result.valid_pairs(app.dataset), app.threshold)
        self.card_acc.set_value(M.fmt(m.accuracy), f"{m.tp + m.tn} of {m.n}")
        self.card_auc.set_value(M.fmt(m.auc, pct=False), "")
        self.card_f1.set_value(M.fmt(m.f1, pct=False),
                               f"P {m.precision:.2f}   R {m.recall:.2f}")
        # false positives as a count, not a rate: "6 of 100 real images" is the
        # sentence someone acts on, and it turns red past 10%
        self.card_fpr.set_value(
            f"{m.fp}", f"of {m.tn + m.fp} real",
            color=T.BAD if (m.fpr == m.fpr and m.fpr > 0.10) else T.TEXT)

    def _draw_charts(self):
        """Histogram, ROC and confusion matrix from the current state."""
        app = self.app
        y, s = app.result.valid_pairs(app.dataset)
        # a partly labeled folder still has scores for the rest; they get their
        # own grey hump on the histogram rather than being dropped
        unlabeled = [sc for it, sc in zip(app.dataset.items, app.result.scores)
                     if it.label is None and not math.isnan(sc)]
        self.chart_hist.plot_score_histogram(y, s, app.threshold, unlabeled)
        self.chart_roc.plot_roc(y, s)
        self.chart_cm.plot_confusion(
            M.compute_metrics(y, s, app.threshold) if y else M.Metrics())


# --------------------------------------------------------------------------- #
# 2. Images
# --------------------------------------------------------------------------- #

class ImagesPage(QWidget):
    """Every prediction as a sortable table, with a preview of the selected row.

    The FP and FN filters are the reason this page exists: they turn "86%
    accurate" into the fourteen specific pictures the model got wrong.
    """

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self._filter = FILTER_ALL
        self._selected = -1               # dataset index shown in the preview

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        chips = QHBoxLayout()
        chips.setSpacing(6)
        self.chips = []
        for i, (text, tip) in enumerate([
            ("All", "Every image"),
            ("FP", "False positives — authentic images flagged as AI"),
            ("FN", "False negatives — AI images that slipped through"),
            ("AI", "Everything predicted AI at this threshold"),
            ("Real", "Everything predicted authentic at this threshold"),
        ]):
            chip = Chip(text)
            chip.setToolTip(tip)
            chip.clicked.connect(lambda _, idx=i: self.set_filter(idx))
            chips.addWidget(chip)
            self.chips.append(chip)
        self.chips[0].setChecked(True)
        chips.addStretch(1)
        self.count_label = QLabel("")
        self.count_label.setStyleSheet(
            f"color: {T.TEXT_FAINT}; font-size: {C.FS_SMALL}px;")
        chips.addWidget(self.count_label)
        lay.addLayout(chips)

        split = QSplitter(Qt.Orientation.Horizontal)
        self.model = ResultsModel(app)
        # the proxy sorts only. Filtering is done by rebuilding the model's row
        # list, because the filters depend on the live threshold and a proxy
        # would have to be invalidated on every slider step anyway.
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setSortRole(Qt.ItemDataRole.UserRole)   # the numeric sort key

        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.setMouseTracking(True)
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.setItemDelegateForColumn(1, ScoreBarDelegate(self.table))
        self.table.setWordWrap(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in range(1, len(COLUMNS)):
            header.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self.table.clicked.connect(self._on_row_clicked)
        split.addWidget(self.table)
        split.addWidget(self._build_preview())
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        split.setSizes([800, 320])
        lay.addWidget(split, 1)

    def _build_preview(self) -> QWidget:
        """The right-hand pane: the picture, its score, its name, its metadata."""
        card = Card(padding=16)
        card.setMinimumWidth(250)
        lay = card.layout()

        self.preview = QLabel("Select a row")
        self.preview.setMinimumHeight(280)
        self.preview.setSizePolicy(QSizePolicy.Policy.Preferred,
                                   QSizePolicy.Policy.Expanding)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setStyleSheet(
            f"background-color: {T.TRACK}; border: none;"
            f" border-radius: {T.R_CARD}px; color: {T.TEXT_FAINT};"
            f" font-size: {C.FS_SMALL}px;")

        self.preview_score = QLabel("")
        self.preview_score.setStyleSheet(
            f"color: {T.TEXT_FAINT}; font-size: 22px; font-weight: 700;")
        self.preview_name = QLabel("")
        self.preview_name.setWordWrap(True)
        self.preview_name.setStyleSheet(f"color: {T.TEXT}; font-size: {C.FS_BODY}px;")
        self.preview_meta = QLabel("")
        self.preview_meta.setWordWrap(True)
        self.preview_meta.setStyleSheet(
            f"color: {T.TEXT_FAINT}; font-size: {C.FS_SMALL}px;")

        lay.addWidget(self.preview, 1)
        lay.addSpacing(4)
        lay.addWidget(self.preview_score)
        lay.addWidget(self.preview_name)
        lay.addWidget(self.preview_meta)
        return card

    def set_filter(self, idx: int):
        """All / FP / FN / AI / Real. The chips are mutually exclusive."""
        self._filter = idx
        for i, chip in enumerate(self.chips):
            chip.setChecked(i == idx)
        self.refresh()

    def _on_row_clicked(self, index):
        # the click arrives in proxy coordinates; map back before asking the
        # model which dataset item this row is
        src = self.proxy.mapToSource(index)
        di = self.model.data(self.model.index(src.row(), 0), ROLE_INDEX)
        if di is not None:
            self.show_preview(int(di))

    def refresh(self, charts: bool = False):
        """Re-apply the filter at the current threshold and rebuild the rows."""
        app = self.app
        if app.dataset is None:
            self.model.set_rows([])
            self.count_label.setText("")
            return

        # FP/FN need both a label and a score; AI/Real need only a score. An
        # unlabeled or unscored image therefore drops out of the error filters
        # but still appears under All.
        rows = []
        for di, item in enumerate(app.dataset.items):
            score = app.score_at(di)
            pred = None if score is None else int(score >= app.threshold)
            f = self._filter
            if f == FILTER_ALL:
                keep = True
            elif f == FILTER_AI:
                keep = pred == 1
            elif f == FILTER_REAL:
                keep = pred == 0
            elif item.label is None or pred is None:
                keep = False
            elif f == FILTER_FP:
                keep = pred == 1 and item.label == 0
            else:
                keep = pred == 0 and item.label == 1
            if keep:
                rows.append(di)
        self.model.set_rows(rows)
        self.count_label.setText(f"{len(rows):,} of {len(app.dataset):,}")
        if self._selected >= 0:
            self.show_preview(self._selected)

    def show_preview(self, di: int):
        """Load dataset item `di` into the preview pane.

        Full resolution here, unlike the gallery: it is one image at a time and
        scaled to fit, so decoding it properly costs nothing noticeable.
        """
        app = self.app
        if app.dataset is None or di >= len(app.dataset.items):
            return
        self._selected = di
        item = app.dataset.items[di]
        score = app.score_at(di)

        pix = QPixmap(item.path)
        if pix.isNull():
            self.preview.setText("no preview")
            self.preview.setPixmap(QPixmap())
        else:
            self.preview.setPixmap(pix.scaled(
                max(self.preview.width() - 16, 32), max(self.preview.height() - 16, 32),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))

        if score is None:
            text, color = "—", T.TEXT_FAINT
        else:
            verdict = "AI" if score >= app.threshold else "real"
            text, color = f"{score:.3f}  {verdict}", score_color(score)
        self.preview_score.setText(text)
        self.preview_score.setStyleSheet(
            f"color: {color}; font-size: 22px; font-weight: 700;")

        self.preview_name.setText(item.name)
        self.preview_name.setToolTip(item.path)
        dims = f"{pix.width()}×{pix.height()}" if not pix.isNull() else "?"
        meta = f"{dims}   ·   {item.size_bytes / 1024:.0f} KB"
        if item.label is not None:
            meta += f"   ·   truth: {label_text(item.label)}"
        self.preview_meta.setText(meta)


# --------------------------------------------------------------------------- #
# 3. Robustness
# --------------------------------------------------------------------------- #

#: severity is one axis, not ten. The CLI already treats it that way
#: (--severities applies to every --transforms entry), and a grid of fifty
#: numbered checkboxes never said what the numbers meant.
SEVERITY_WORDS = ["barely touched", "light", "moderate", "heavy", "brutal"]


class SeverityScale(QWidget):
    """How hard to push every selected transform: one slider, 1 to 5.

    The slider sets the ceiling and the sweep runs 1..N, because the whole
    output of this page is a curve - picking a single severity would flatten
    it to one point per transform.
    """

    def __init__(self, on_change=None, parent=None):
        super().__init__(parent)
        self._on_change = on_change

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(7)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        self.value_label = QLabel("")
        self.value_label.setStyleSheet(
            f"color: {T.TEXT}; font-size: {C.FS_BODY}px; font-weight: 600;")
        head.addWidget(SectionTitle("Severity"))
        head.addStretch(1)
        head.addWidget(self.value_label)
        lay.addLayout(head)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(1, 5)
        self.slider.setValue(5)
        self.slider.setPageStep(1)
        self.slider.valueChanged.connect(self._on_slider)
        lay.addWidget(self.slider)

        # only the two ends get labelled. Marks for 2, 3 and 4 cannot be placed
        # honestly - the handle's margins mean an evenly divided row does not
        # sit under the positions the slider actually stops at - and the exact
        # value is already spelled out in the readout above.
        ends = QHBoxLayout()
        ends.setContentsMargins(0, 0, 0, 0)
        for text in ("1  mild", "5  harsh"):
            end = QLabel(text)
            end.setStyleSheet(
                f"color: {T.TEXT_FAINT}; font-size: {C.FS_MICRO}px;")
            if text.endswith("harsh"):
                ends.addStretch(1)
            ends.addWidget(end)
        lay.addLayout(ends)

        self.example = Hint("")
        lay.addWidget(self.example)

    @property
    def value(self) -> int:
        return self.slider.value()

    def set_example(self, spec):
        """Name what the top severity actually does, for a concrete transform."""
        n = self.value
        if spec is None:
            self.example.setText("Pick a transform to see what that means.")
            return
        span = (f"{spec.label_for(1)} to {spec.label_for(n)}" if n > 1
                else spec.label_for(1))
        self.example.setText(
            f"{spec.display_name}: {span}   ({SEVERITY_WORDS[n - 1]})")

    def _on_slider(self, _value: int):
        self.sync()
        if self._on_change is not None:
            self._on_change()

    def sync(self):
        """Repaint this widget's own readout. Never calls back out - the
        owner's callback is wired to the slider, and a callback that also
        called sync() would recurse."""
        n = self.value
        self.value_label.setText("1 only" if n == 1 else f"1 to {n}")


class RobustnessPage(QWidget):
    """Controls on the left, the degradation curve and cell table on the right.

    Displays whatever is in app.robustness, which is either a sweep this page
    just ran or a robustness_report.json that was sitting next to the data.
    """

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        lay.addWidget(self._build_controls())
        lay.addWidget(self._build_results(), 1)

    def _build_controls(self) -> QWidget:
        """Transform checkboxes, the severity scale, sample options, Run/Cancel."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFixedWidth(326)

        holder = QWidget()
        lay = QVBoxLayout(holder)
        lay.setContentsMargins(0, 0, 10, 0)
        lay.setSpacing(12)

        group = QGroupBox("Transforms")
        gl = QVBoxLayout(group)
        gl.setSpacing(4)
        self.transform_boxes = []
        for spec in TRANSFORMS:
            box = QCheckBox(spec.display_name)
            box.spec = spec
            levels = "   ".join(f"{i}: {spec.label_for(i)}" for i in range(1, 6))
            box.setToolTip(f"{spec.description}\n\nSeverities:   {levels}")
            # the three most informative defaults: the codec everything goes
            # through, the filter that kills high frequencies, and the resize
            box.setChecked(spec.key in ("jpeg", "blur", "rescale"))
            box.toggled.connect(self._sync_scale)
            gl.addWidget(box)
            self.transform_boxes.append(box)
        lay.addWidget(group)

        scale_card = Card(padding=14)
        self.severity = SeverityScale(on_change=self._sync_scale)
        scale_card.layout().addWidget(self.severity)
        lay.addWidget(scale_card)

        opts = Card(padding=14)
        ol = opts.layout()

        self.sample_spin = QSpinBox()
        self.sample_spin.setRange(10, 100000)
        self.sample_spin.setValue(200)
        self.sample_spin.setSingleStep(50)
        self.sample_spin.setFixedWidth(88)
        self.sample_spin.setToolTip(
            "Images per cell, balanced across classes. Smaller = faster sweeps.")

        self.side_spin = QSpinBox()
        self.side_spin.setRange(128, 4096)
        self.side_spin.setValue(768)
        self.side_spin.setSingleStep(128)
        self.side_spin.setFixedWidth(88)
        self.side_spin.setToolTip("Images are decoded and capped to this size.")

        for label, widget in (("Sample size", self.sample_spin),
                              ("Max image side", self.side_spin)):
            row = QHBoxLayout()
            row.addWidget(QLabel(label), 1)
            row.addWidget(widget)
            ol.addLayout(row)

        self.cost_label = Hint("")
        ol.addWidget(self.cost_label)

        self.run_btn = QPushButton("Run sweep")
        self.run_btn.setProperty("accent", True)
        self.run_btn.clicked.connect(self.app.start_sweep)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.app.cancel_sweep)
        btns = QHBoxLayout()
        btns.addWidget(self.run_btn, 2)
        btns.addWidget(self.cancel_btn, 1)
        ol.addLayout(btns)

        self.status = Hint("")
        ol.addWidget(self.status)
        lay.addWidget(opts)
        lay.addStretch(1)

        scroll.setWidget(holder)
        self._sync_scale()
        return scroll

    def _sync_scale(self):
        """Keep the severity readout and the cell count honest."""
        chosen = [b.spec for b in self.transform_boxes if b.isChecked()]
        self.severity.sync()
        self.severity.set_example(chosen[0] if chosen else None)
        cells = len(chosen) * self.severity.value
        if not chosen:
            self.cost_label.setText("No transforms selected.")
        else:
            self.cost_label.setText(
                f"{len(chosen)} transform{'s' if len(chosen) > 1 else ''}"
                f" x {self.severity.value} severit"
                f"{'ies' if self.severity.value > 1 else 'y'}"
                f"  =  {cells} cells, plus the clean baseline.")

    def _build_results(self) -> QWidget:
        """Summary cards, the degradation chart, and the per-cell table."""
        panel = QWidget()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        cards = QHBoxLayout()
        cards.setSpacing(12)
        self.card_base = StatCard("Clean baseline")
        self.card_worst = StatCard("Worst case")
        self.card_mean = StatCard("Mean drop")
        self.card_cells = StatCard("Cells")
        for c in (self.card_base, self.card_worst, self.card_mean, self.card_cells):
            cards.addWidget(c)
        lay.addLayout(cards)

        chart_card = Card(padding=8)
        self.chart = MplCanvas(height=3.0)
        chart_card.layout().addWidget(self.chart)
        lay.addWidget(chart_card, 3)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Transform", "Severity", "Accuracy", "Δ", "AUC"])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.setShowGrid(False)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        lay.addWidget(self.table, 2)
        return panel

    # -- api ---------------------------------------------------------------
    def selected_cells(self) -> list:
        """Every ticked transform, at every severity up to the slider."""
        levels = range(1, self.severity.value + 1)
        return [(box.spec.key, severity)
                for box in self.transform_boxes if box.isChecked()
                for severity in levels]

    def set_busy(self, busy: bool, message: str = ""):
        """Swap Run for Cancel and show the sweep's live status line.

        This page gets no loading overlay - it is the one cancellable job, and a
        scrim over the window would bury the Cancel button.
        """
        self.run_btn.setEnabled(not busy and self.app.dataset is not None)
        self.cancel_btn.setEnabled(busy)
        self.status.setText(message)

    def refresh(self, charts: bool = True):
        """Draw whatever sweep data exists, or explain that there is none yet."""
        app = self.app
        view = app.robustness or {}
        series = view.get("series", {})

        if not series:
            self.chart.clear_to_message(
                "No sweep yet\n\nSelect transforms and press Run sweep")
            self.table.setRowCount(0)
            for card in (self.card_base, self.card_worst, self.card_mean, self.card_cells):
                card.set_value("—", "")
            if app.dataset is None:
                self.status.setText("Upload a folder first.")
            else:
                self.status.setText(
                    f"{len(app.dataset.labeled_indices()):,} labelled images ready.")
            return

        baseline = view.get("baseline", float("nan"))
        self.chart.plot_degradation(series, baseline, "Accuracy")
        self._fill_table(view.get("cells", []), baseline)

    def _fill_table(self, cells, baseline):
        """Fill the cell table and the four summary cards from one pass."""
        # sorting off while filling: with it on, each inserted row would be
        # re-sorted and the ones still being written would move under the writer
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(cells))
        deltas = []
        for r, cell in enumerate(cells):
            acc = cell.get("accuracy", float("nan"))
            delta = acc - baseline if (acc == acc and baseline == baseline) else float("nan")
            deltas.append((delta, cell))
            values = [cell.get("name", "?"), cell.get("severity_label", ""),
                      M.fmt(acc), delta_text(delta), M.fmt(cell.get("auc"), pct=False)]
            for c, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                if c > 0:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if c == 3:
                    item.setForeground(QBrush(QColor(delta_color(delta))))
                self.table.setItem(r, c, item)
        self.table.setSortingEnabled(True)

        self.card_base.set_value(M.fmt(baseline), "untouched images")
        self.card_cells.set_value(str(len(cells)), "transform x severity")

        # NaN cells are shown in the table but cannot be ranked or averaged
        valid = [(d, c) for d, c in deltas if d == d]
        if not valid:
            return
        valid.sort(key=lambda t: t[0])      # most negative delta first = worst
        worst_d, worst = valid[0]
        self.card_worst.set_value(
            M.fmt(worst.get("accuracy")),
            f"{worst.get('name', '')} {worst.get('severity_label', '')}".strip(),
            color=delta_color(worst_d))
        mean = sum(d for d, _ in valid) / len(valid)
        self.card_mean.set_value(delta_text(mean), f"over {len(valid)} cells",
                                 color=delta_color(mean))
