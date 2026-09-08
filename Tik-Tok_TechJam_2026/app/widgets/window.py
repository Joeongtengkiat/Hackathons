"""The application shell: an upload screen, then a results screen.

There is no navigation rail. You start by saying what you are uploading, and
what comes back is decided by that: labelled data opens on Insights with
Images and Robustness behind header tabs, while unlabelled data opens on the
verdict gallery with no tabs at all - there is nothing else to look at when
there is no ground truth.

The pipeline itself lives in runner.py / sweep.py and logs to the terminal.
This owns the widgets and moves the work onto a thread so the window stays
responsive; it never reimplements the pipeline.

AppWindow is also the single source of truth the pages read from: `dataset`,
`result`, `threshold` and `robustness` live here, and every page is handed
`self` and pulls what it needs. That is why moving the slider only has to call
refresh() - no page holds a copy that could go stale.
"""

from __future__ import annotations

import math
import os

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import (
    QButtonGroup, QFileDialog, QFrame, QHBoxLayout, QLabel, QMainWindow,
    QMessageBox, QPushButton, QSlider, QStackedWidget, QVBoxLayout, QWidget
)

from .. import export as EX
from .. import metrics as M
from .. import runner
from .. import sweep as SW
from .. import theme as T
from ..dataset import LabelMode
from ..workers import LoadWorker, ScanWorker, ScoreWorker, SweepWorker
from . import components as C
from .components import Dot, Tab
from .gallery import GalleryPage
from .loading import LoadingOverlay
from .pages import ImagesPage, InsightsPage, RobustnessPage
from .upload import UploadPage

#: the steps a scoring run takes, in order, as the working screen lists them
RUN_PHASES = [("scan", "Scanning the folder"),
              ("model", "Loading the detector"),
              ("score", "Scoring images")]

#: reading a finished predictions.json back in - one step, but the screen is
#: the same one, so a fast path never looks like a different app
READ_PHASES = [("read", "Reading predictions")]

#: The sweep gets no overlay - it is cancellable, and a scrim would bury the
#: Cancel button - so it reports into the robustness page's own status line.

SCREEN_UPLOAD, SCREEN_RESULTS = range(2)
TAB_INSIGHTS, TAB_IMAGES, TAB_ROBUSTNESS, VIEW_GALLERY = range(4)

#: the three tabs a labeled run gets, in order. The gallery is not one of them:
#: it is the whole of the unlabeled screen, so it never needs a tab.
TABS = [
    ("Insights", "How the detector performed"),
    ("Images", "Every prediction, filterable"),
    ("Robustness", "Accuracy under post-processing"),
]

#: the threshold means something on any page that renders a verdict
THRESHOLD_VIEWS = (TAB_INSIGHTS, TAB_IMAGES, VIEW_GALLERY)


class AppWindow(QMainWindow):
    """The window, and the state every page reads from."""

    def __init__(self, start_dir: str = None, start_json: str = None,
                 threshold: float = 0.5):
        super().__init__()

        # -- state
        self.dataset = None
        self.result = None
        self.robustness = {}
        self.threshold = threshold
        # What Reset goes back to. A run replaces it with the detector's own
        # operating point, so Reset means "the model's answer", not "0.50".
        self.base_threshold = threshold
        # one slot per kind of job. Non-None means "in flight", which is also
        # how a second click on Run is refused.
        self.worker = None            # scoring, or reading a predictions.json
        self.sweep_worker = None      # the robustness sweep
        self.peek_worker = None       # the cheap background scan of a folder
        self.view = TAB_INSIGHTS

        self.setWindowTitle("AIGC Detector")
        self.resize(1400, 900)
        self.setMinimumSize(1080, 700)

        self.upload_page = UploadPage(self)

        self.screens = QStackedWidget()
        self.screens.addWidget(self.upload_page)
        self.screens.addWidget(self._build_results())
        self.setCentralWidget(self.screens)

        # Sits above every screen and covers the whole window. Created last so
        # it stacks on top, and kept sized to the window by resizeEvent.
        self.overlay = LoadingOverlay(self)
        self.overlay.setGeometry(self.rect())

        # Redrawing three matplotlib figures per slider step would stutter, so a
        # drag repaints the cheap widgets immediately and defers the charts
        # until it pauses. Single-shot and restarted on every step: only the
        # last position ever draws.
        self._chart_timer = QTimer(self)
        self._chart_timer.setSingleShot(True)
        self._chart_timer.timeout.connect(lambda: self.refresh(charts=True))

        self.go_upload()

        if start_json:
            self.load_predictions_file(start_json)
        elif start_dir:
            # the command line already said what it wanted by naming a folder,
            # so let the labels decide rather than demanding the tile be clicked
            self.upload_page.set_directory(start_dir)
            self.start_run(LabelMode.AUTO)

    # -- results screen ----------------------------------------------------
    def _build_results(self) -> QWidget:
        """Header plus a stack holding all four result views.

        All four are built up front and switched between, rather than created on
        demand: the threshold has to reach every one of them, and a page that
        did not exist yet would come back stale.
        """
        screen = QWidget()
        outer = QVBoxLayout(screen)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_header())

        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(28, 20, 28, 22)

        self.insights_page = InsightsPage(self)
        self.images_page = ImagesPage(self)
        self.robustness_page = RobustnessPage(self)
        self.gallery_page = GalleryPage(self)

        self.stack = QStackedWidget()
        for w in (self.insights_page, self.images_page, self.robustness_page,
                  self.gallery_page):
            self.stack.addWidget(w)
        lay.addWidget(self.stack, 1)
        outer.addWidget(body, 1)
        return screen

    def _build_header(self) -> QWidget:
        """Two rows: what is loaded and what you can do with it, then tabs and
        the threshold."""
        bar = QFrame()
        bar.setObjectName("header")
        lay = QVBoxLayout(bar)
        lay.setContentsMargins(28, 12, 28, 0)
        lay.setSpacing(8)

        # -- row 1: what is loaded, and what you can do with it
        top = QHBoxLayout()
        top.setSpacing(12)

        mark = QLabel()                       # the app mark, drawn not typeset
        mark.setFixedSize(18, 18)
        mark.setStyleSheet(f"background-color: {T.ACCENT}; border-radius: 5px;")

        self.source_label = QLabel("")
        self.source_label.setStyleSheet(
            f"color: {T.TEXT}; font-size: 16px; font-weight: 700;"
            " letter-spacing: -0.3px;")
        self.counts_label = QLabel("")
        self.counts_label.setStyleSheet(
            f"color: {T.TEXT_FAINT}; font-size: {C.FS_SMALL}px;")

        self.detector_dot = Dot(T.TEXT_FAINT)
        self.detector_label = QLabel("")
        self.detector_label.setStyleSheet(
            f"color: {T.TEXT_FAINT}; font-size: {C.FS_SMALL}px;")

        self.new_btn = QPushButton("New upload")
        self.new_btn.setToolTip("Back to the upload screen")
        self.new_btn.clicked.connect(self.go_upload)
        self.export_btn = QPushButton("Export JSON")
        self.export_btn.setProperty("accent", True)
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self.export_json)

        top.addWidget(mark)
        top.addWidget(self.source_label)
        top.addWidget(self.counts_label)
        top.addSpacing(6)
        top.addWidget(self.detector_dot)
        top.addWidget(self.detector_label)
        top.addStretch(1)
        top.addWidget(self.new_btn)
        top.addWidget(self.export_btn)
        lay.addLayout(top)

        # -- row 2: tabs on the left, the threshold on the right
        bottom = QHBoxLayout()
        bottom.setSpacing(0)

        self.tab_bar = QWidget()
        self.tab_bar.setProperty("bare", True)     # it sits on the header
        tabs = QHBoxLayout(self.tab_bar)
        tabs.setContentsMargins(0, 0, 0, 0)
        tabs.setSpacing(0)
        self.tabs = []
        group = QButtonGroup(self)
        group.setExclusive(True)
        for i, (name, hint) in enumerate(TABS):
            btn = Tab(name)
            btn.setToolTip(hint)
            btn.clicked.connect(lambda _, idx=i: self.go(idx))
            group.addButton(btn)
            tabs.addWidget(btn)
            self.tabs.append(btn)
        tabs.addStretch(1)

        # an explicit stretch, not the tab strip's - the strip is hidden for
        # unlabeled data, and a hidden widget hands its space to whatever is
        # left, which would stretch the threshold controls across the header
        bottom.addWidget(self.tab_bar)
        bottom.addStretch(1)
        bottom.addWidget(self._build_threshold())
        lay.addLayout(bottom)
        return bar

    def _build_threshold(self) -> QWidget:
        """The threshold control: a readout, a slider, Best F1 and Reset.

        The slider is an integer widget, so it works in thousandths - three
        decimals is the resolution the readout shows and enough to sit exactly
        on a calibrated operating point like 0.954.
        """
        self.threshold_box = QWidget()
        self.threshold_box.setProperty("bare", True)
        row = QHBoxLayout(self.threshold_box)
        row.setContentsMargins(0, 0, 0, 6)
        row.setSpacing(10)

        label = QLabel("Threshold")
        label.setStyleSheet(
            f"color: {T.TEXT_FAINT}; font-size: {C.FS_SMALL}px; font-weight: 500;")
        self.thr_label = QLabel(f"{self.threshold:.3f}")
        self.thr_label.setFixedWidth(48)
        self.thr_label.setStyleSheet(
            f"color: {T.TEXT}; font-size: {C.FS_TITLE}px; font-weight: 600;")
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setFixedWidth(160)
        self.slider.setRange(0, 1000)
        self.slider.setValue(int(self.threshold * 1000))
        self.slider.valueChanged.connect(self._on_slider)

        self.best_btn = QPushButton("Best F1")
        self.best_btn.setToolTip("Move the threshold to the value that maximises F1")
        self.best_btn.clicked.connect(self._best_threshold)
        self.reset_btn = QPushButton("Reset")
        self.reset_btn.clicked.connect(
            lambda: self.set_threshold(self.base_threshold))
        self._sync_reset_tip()

        for w in (label, self.thr_label, self.slider, self.best_btn,
                  self.reset_btn):
            row.addWidget(w)
        return self.threshold_box

    # -- navigation --------------------------------------------------------
    def go_upload(self):
        """Back to the start, with the folder and the choice still in place."""
        self.screens.setCurrentIndex(SCREEN_UPLOAD)

    def go(self, index: int):
        """Switch result view, sync the tabs, and refresh what is now visible."""
        self.view = index
        self.stack.setCurrentIndex(index)
        for i, btn in enumerate(self.tabs):
            btn.setChecked(i == index)
        self.threshold_box.setVisible(index in THRESHOLD_VIEWS)
        page = self.stack.currentWidget()
        if hasattr(page, "refresh"):
            page.refresh()

    def show_results(self):
        """Route to the screen the data actually supports."""
        labeled = self.dataset is not None and self.dataset.has_labels
        self.tab_bar.setVisible(labeled)
        self.screens.setCurrentIndex(SCREEN_RESULTS)
        self.go(TAB_INSIGHTS if labeled else VIEW_GALLERY)

    # -- state helpers -----------------------------------------------------
    def score_at(self, di: int):
        """The score for dataset index `di`, or None if missing or NaN.

        Every page goes through this rather than indexing result.scores, so
        "could not be scored" is handled in exactly one place.
        """
        if self.result is None or di < 0 or di >= len(self.result.scores):
            return None
        s = self.result.scores[di]
        return None if s is None or math.isnan(s) else s

    def set_threshold(self, t: float):
        """Move the slider, which is what actually updates the threshold.

        Deliberately routed through the widget: valueChanged then drives the
        readout and every refresh, so there is one path and no way for the
        number and the slider position to disagree.
        """
        self.slider.setValue(int(round(min(max(t, 0.0), 1.0) * 1000)))

    def _sync_reset_tip(self):
        why = (", the detector's own operating point"
               if self.result is not None else "")
        self.reset_btn.setToolTip(f"Back to {self.base_threshold:.3f}{why}")

    def _on_slider(self, value: int):
        """Live update: cheap views now, charts after the drag settles."""
        self.threshold = value / 1000.0
        self.thr_label.setText(f"{self.threshold:.3f}")
        self.refresh(charts=False)
        self._chart_timer.start(140)

    def _best_threshold(self):
        """Jump to the F1-optimal threshold. Needs both classes present."""
        if self.result is None or self.dataset is None:
            return
        y, s = self.result.valid_pairs(self.dataset)
        if len(set(y)) < 2:
            return
        self.set_threshold(M.best_threshold(y, s, "f1"))

    def refresh(self, charts: bool = True):
        """Re-read state into every page. `charts=False` skips the redraws.

        All pages, not just the visible one, so switching tabs never shows a
        view that is one threshold behind.
        """
        self.insights_page.refresh(charts=charts)
        self.images_page.refresh()
        self.gallery_page.refresh()
        if charts:
            self.robustness_page.refresh()

    def _sync_header(self):
        """Retitle the header for whatever is currently loaded."""
        if self.dataset is None:
            self.source_label.setText("")
            self.counts_label.setText("")
            self.detector_label.setText("")
            self.detector_dot.set_color(T.TEXT_FAINT)
            self.export_btn.setEnabled(False)
            self.best_btn.setEnabled(False)
            return

        source = self.dataset.root or (self.result.source if self.result else "")
        self.source_label.setText(os.path.basename(source.rstrip("\\/")) or source)
        self.source_label.setToolTip(source)

        counts = f"{len(self.dataset):,} images"
        counts += (f"   ·   {self.dataset.n_real:,} real / {self.dataset.n_ai:,} AI"
                   if self.dataset.has_labels else "   ·   unlabeled")
        self.counts_label.setText(counts)

        if self.result is not None:
            self.detector_label.setText(self.result.detector_display)
            self.detector_dot.set_color(T.GOOD)
            tip = "Model backend"
            if self.result.elapsed:
                tip += f"\nScored in {self.result.elapsed:.1f}s"
            self.detector_dot.setToolTip(tip)
            self.detector_label.setToolTip(tip)

        self.export_btn.setEnabled(self.result is not None and self.result.n_scored > 0)
        self.best_btn.setEnabled(self.dataset.has_labels)

    # -- the working screen ------------------------------------------------
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.overlay.setGeometry(self.rect())

    def _on_progress(self, phase: str, detail: str, done: int, total: int,
                     note: str):
        """One slot for every worker: they all report in the same shape."""
        self.overlay.set_phase(phase, detail)
        if total > 0 or note:
            self.overlay.set_progress(done, total, note)

    # -- running -----------------------------------------------------------
    def start_run(self, label_mode: LabelMode = None):
        """Score the chosen folder on a worker thread.

        `label_mode` defaults to what the upload screen declared - NONE for
        "Unlabelled data", which ignores labels that are present.
        """
        if self.worker is not None:
            return                    # already running; ignore the second click
        directory = self.upload_page.directory
        if not directory or not os.path.isdir(directory):
            QMessageBox.information(self, "No folder",
                                    "Pick an image folder to score.")
            return
        if label_mode is None:
            label_mode = self.upload_page.label_mode

        runner.log("")
        runner.log(f"=== Detect {directory} ===")

        self.upload_page.set_busy(True, "Working — detailed progress in the terminal.")
        self.overlay.begin(RUN_PHASES, "Scanning the folder")
        self.worker = ScoreWorker(directory, self.upload_page.detector_name,
                                  self.upload_page.weights, label_mode, parent=self)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_run_done)
        self.worker.failed.connect(self._on_run_failed)
        self.worker.start()

    def peek_directory(self, directory: str):
        """Scan a folder in the background so the upload screen can describe it.

        Scanning is cheap next to scoring, and it is what tells you whether the
        folder can support the kind of upload you just declared - before you
        spend a run finding out.
        """
        if self.peek_worker is not None:
            self.peek_worker.wait(0)      # a stale scan just loses the race
        self.peek_worker = ScanWorker(directory, parent=self)
        self.peek_worker.finished_ok.connect(self._on_peek_done)
        self.peek_worker.failed.connect(lambda _msg: setattr(self, "peek_worker", None))
        self.peek_worker.start()

    def _on_peek_done(self, dataset):
        self.peek_worker = None
        self.upload_page.set_peek(dataset)

    def load_predictions_file(self, path: str):
        if self.worker is not None:
            return
        runner.log("")
        runner.log(f"=== Open {path} ===")
        self.upload_page.set_busy(True, "Reading predictions…")
        self.overlay.begin(READ_PHASES, "Reading predictions")
        self.worker = LoadWorker(path, parent=self)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_run_done)
        self.worker.failed.connect(self._on_run_failed)
        self.worker.start()

    def _on_run_done(self, dataset, result):
        """A run finished: adopt its results, its threshold, and route to a screen."""
        self.worker = None
        self.overlay.finish()
        self.dataset = dataset
        self.result = result
        self.robustness = {}

        # Adopt the detector's operating point. The calibrated model's is not
        # 0.5, and leaving the slider there would call every image AI.
        self.base_threshold = float(result.threshold)
        self._sync_reset_tip()
        self.set_threshold(self.base_threshold)

        runner.summarize(dataset, result, self.threshold, total_steps=4, step_no=4)

        # a sweep report sitting next to the data is picked up for free
        report = os.path.join(dataset.root or "", SW.DEFAULT_REPORT)
        view = SW.load_report_view(report) if dataset.root else {}
        if view and dataset.has_labels:
            self.robustness = view
            runner.log(f"      robustness report loaded from "
                       f"{os.path.basename(report)}", indent=0)

        self.upload_page.set_busy(False, "")
        self._sync_header()
        self.refresh(charts=True)
        self.show_results()

    def _on_run_failed(self, message: str):
        """Bad input or an unexpected error - say so, stay on the upload screen."""
        self.worker = None
        self.overlay.finish()
        self.upload_page.set_busy(False, "Failed.")
        QMessageBox.critical(self, "Run failed", message)

    # -- sweep -------------------------------------------------------------
    def start_sweep(self):
        """Run the selected transform grid. Refuses politely rather than failing.

        Three preconditions, each with its own message: something loaded, ground
        truth to be right or wrong about, and at least one transform ticked.
        """
        if self.sweep_worker is not None:
            return
        if self.dataset is None:
            QMessageBox.information(self, "Nothing loaded",
                                    "Upload a folder first.")
            return
        if not self.dataset.has_labels:
            QMessageBox.information(
                self, "Labels required",
                "The sweep measures accuracy, so it needs ground-truth labels.\n\n"
                "Use real/ and ai/ subfolders, a labels.csv, or real_/ai_ prefixes.")
            return
        cells = self.robustness_page.selected_cells()
        if not cells:
            QMessageBox.information(self, "Nothing selected",
                                    "Tick at least one transform.")
            return

        runner.log("")
        runner.log(f"=== Robustness sweep: {len(cells) + 1} cells ===")
        self.robustness_page.set_busy(True, "Sweeping — progress in the terminal.")
        self.sweep_worker = SweepWorker(
            self.dataset, self.upload_page.detector_name, cells,
            self.robustness_page.sample_spin.value(),
            self.robustness_page.side_spin.value(),
            self.threshold, self.upload_page.weights, parent=self)
        # Deliberately not the overlay: the sweep is the one job that can be
        # cancelled, and a scrim over the window would bury the Cancel button.
        # It reports into the page's own status line instead.
        self.sweep_worker.progress.connect(self._on_sweep_progress)
        self.sweep_worker.finished_ok.connect(self._on_sweep_done)
        self.sweep_worker.failed.connect(self._on_sweep_failed)
        self.sweep_worker.start()

    def _on_sweep_progress(self, phase: str, detail: str, done: int, total: int,
                           note: str):
        text = f"{done}/{total}  {detail}" if phase == "sweep" and total else detail
        if note:
            text += f"   ·   {note}"
        self.robustness_page.set_busy(True, text)

    def cancel_sweep(self):
        if self.sweep_worker is not None:
            self.sweep_worker.cancel()
            self.robustness_page.set_busy(True, "Cancelling…")

    def _on_sweep_done(self, result):
        """Adopt the sweep and write the report next to the data.

        Writing it here rather than in the worker means the GUI leaves behind
        exactly the file robustness.py would have, which gui.py then picks up
        automatically on the next run over that folder.
        """
        self.sweep_worker = None
        self.robustness = result.to_view()
        path = os.path.join(self.dataset.root, SW.DEFAULT_REPORT)
        try:
            written = result.write(path)
            runner.log(f"      report -> {written}")
        except Exception as exc:
            runner.warn(f"could not write the report: {exc}")
        self.robustness_page.set_busy(
            False, f"{len(result.cells)} cells in {result.elapsed:.1f}s")
        self.robustness_page.refresh()

    def _on_sweep_failed(self, message: str):
        self.sweep_worker = None
        self.robustness_page.set_busy(False, "Failed.")
        QMessageBox.critical(self, "Sweep failed", message)

    # -- export ------------------------------------------------------------
    def export_json(self):
        """Write predictions.json - the same file, from the same code, as the CLI."""
        if self.result is None:
            return
        default = os.path.join(self.dataset.root or os.path.expanduser("~"),
                               "predictions.json")
        path, _ = QFileDialog.getSaveFileName(self, "Export predictions", default,
                                              "JSON (*.json)")
        if not path:
            return
        try:
            # the live slider value, so the exported verdicts match what the
            # window is currently showing rather than the model's default point
            n = EX.export_predictions_json(path, self.dataset, self.result,
                                           threshold=self.threshold)
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        runner.log(f"      wrote {n:,} predictions -> {os.path.abspath(path)}")
        QMessageBox.information(self, "Exported", f"Wrote {n:,} records to:\n{path}")

    # -- shutdown ----------------------------------------------------------
    def closeEvent(self, event):
        """Ask every live worker to stop and give it a moment before quitting.

        A QThread still running when the interpreter tears down its parent is a
        crash on exit, so this waits rather than trusting the timing.
        """
        for w in (self.worker, self.sweep_worker, self.peek_worker):
            if w is not None:
                if hasattr(w, "cancel"):
                    w.cancel()
                w.wait(3000)
        super().closeEvent(event)
