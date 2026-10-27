"""compare_tab.py - the Compare tab: a benchmark of the two approaches against Neon.

Made-up farms (farmgen) in batches of 1, 10, 100 and 1,000 are checked by both approaches, one
question at a time ("is the point in forest of 31 Dec 2020?", "does the outline overlap it?"). Every run is
its own transaction, rolled back, so nothing is written; each number is the median of 3 runs.
The sentences under the charts are worked out from the numbers, never written in advance.
"""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QCheckBox, QGridLayout, QHBoxLayout, QLabel, QProgressBar, QPushButton,
                             QScrollArea, QVBoxLayout, QWidget)

from charts import ChartCard, LineChart, PairBars, fmt_bytes, fmt_number
from legend import METHODS
from results_panel import QUESTIONS, SERIES, comparison_metrics, fmt_ms

SIZES = (1, 10, 100, 1000)
REPEAT = 3

LINES = [  # (title, value from one run's stats, y-axis label, formatter)
    ("Time over Neon", lambda r: 1000.0 * r["seconds"], "milliseconds (log scale)", fmt_ms),
    ("Data returned", lambda r: r["bytes"], "bytes (log scale)", fmt_bytes),
    ("Server execution (EXPLAIN ANALYZE)",
     lambda r: None if r.get("server") is None else r["server"]["server_exec_ms"], "milliseconds (log scale)", fmt_ms),
    ("Round trips to Neon", lambda r: r["round_trips"], "round trips (log scale)", fmt_number),
]


def line_data(result, question, value):
    """{approach: [(n, value)]} for one question, skipping missing values."""
    data = {}
    for method, _, _ in SERIES:
        points = []
        for stats in result["series"][method][question]:
            v = value(stats)
            if v is not None:
                points.append((stats["n"], v))
        data[method] = points
    return data


def speed_story(result, question):
    """Which approach was faster at which batch sizes, e.g.
    'Stencils faster for 1 to 10 farms; Outlines faster for 100 to 1,000 (1.9 s vs 5.0 s at 1,000).'"""
    s = {r["n"]: r["seconds"] for r in result["series"]["stencils"][question]}
    p = {r["n"]: r["seconds"] for r in result["series"]["polygons"][question]}
    sizes = [n for n in result["sizes"] if n in s and n in p]
    if not sizes:
        return ""
    groups = []  # [winner, [sizes]]
    for n in sizes:
        winner = "stencils" if s[n] < p[n] else "polygons"
        if groups and groups[-1][0] == winner:
            groups[-1][1].append(n)
        else:
            groups.append([winner, [n]])
    parts = []
    for winner, ns in groups:
        span = f"{ns[0]:,}" if len(ns) == 1 else f"{ns[0]:,} to {ns[-1]:,}"
        parts.append(f"{METHODS[winner][0]} faster for {span} farm{'' if ns == [1] else 's'}")
    biggest = sizes[-1]

    def secs(v):
        return f"{v:.2f} s" if v < 10 else f"{v:.1f} s"
    win = "stencils" if s[biggest] < p[biggest] else "polygons"
    lose = "polygons" if win == "stencils" else "stencils"
    at = {"stencils": s[biggest], "polygons": p[biggest]}
    return "; ".join(parts) + f" ({secs(at[win])} vs {secs(at[lose])} at {biggest:,})."


def agreement_text(result, question):
    rows = result["agreement"][question]
    same, of = sum(r["same"] for r in rows), sum(r["of"] for r in rows)
    pct = 100.0 * same / of if of else 0.0
    return f"Same verdict from both approaches on {same:,} of {of:,} farms ({pct:.1f}%)."


class CompareTab(QScrollArea):
    def __init__(self, window):
        super().__init__()
        self.w = window
        self.result = None
        self.job = None
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.NoFrame)
        body = QWidget()
        body.setObjectName("panel")
        self.setWidget(body)
        outer = QVBoxLayout(body)
        outer.setContentsMargins(24, 18, 24, 24)
        outer.setSpacing(12)

        outer.addWidget(self._label("Stencils vs outlines: speed and query efficiency", "heading"))
        intro = self._label(
            f"Checks made-up farms in batches of {', '.join(f'{n:,}' for n in SIZES)} with both approaches, "
            f"one question at a time. Every run is its own transaction against Neon and is rolled back, "
            f"so nothing is written. Each number is the median of {REPEAT} runs, timed over Neon from this "
            f"computer. Server numbers come from EXPLAIN (ANALYZE, BUFFERS) on the median run's queries.",
            "secondary")
        intro.setWordWrap(True)
        outer.addWidget(intro)

        controls = QHBoxLayout()
        self.run_btn = QPushButton("Run benchmark")
        self.run_btn.clicked.connect(self.run)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(lambda: self.w.worker.cancel())
        self.cancel_btn.hide()
        self.explain_box = QCheckBox("Measure server work (EXPLAIN ANALYZE)")
        self.explain_box.setChecked(True)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setMaximumWidth(220)
        self.bar.hide()
        self.status = self._label("", "secondary")
        for widget in (self.run_btn, self.cancel_btn, self.explain_box, self.bar, self.status):
            controls.addWidget(widget)
        controls.addStretch(1)
        outer.addLayout(controls)

        legend = [(label, colour) for _, label, colour in SERIES]
        self.sections = {}
        for question, title in QUESTIONS.items():
            outer.addSpacing(8)
            outer.addWidget(self._label(title, "heading"))
            story = self._label("Run the benchmark to compare the approaches.", "secondary")
            story.setWordWrap(True)
            outer.addWidget(story)
            grid = QGridLayout()
            grid.setSpacing(12)
            lines = []
            for i, (chart_title, value, y_label, fmt) in enumerate(LINES):
                chart = LineChart(SERIES, y_label, fmt)
                grid.addWidget(ChartCard(chart_title, chart, legend, chart.table), i // 2, i % 2)
                lines.append((chart, value))
            bars = PairBars(SERIES)
            biggest = ChartCard(f"Where the time goes at {SIZES[-1]:,} farms", bars, legend, bars.table,
                                note="Waiting on Neon = time in queries (network + database); computing "
                                     "locally = unpacking pages, rasterising outlines and adding up areas.")
            grid.addWidget(biggest, 2, 0, 1, 2)
            outer.addLayout(grid)
            self.sections[question] = (story, lines, bars, biggest)
        outer.addStretch(1)
        window.listen("map_switching", self.on_map_switching)

    @staticmethod
    def _label(text, role):
        label = QLabel(text)
        label.setTextFormat(Qt.PlainText)
        label.setProperty("role", role)
        return label

    def run(self):
        if self.w.map_info is None:
            self.status.setText("Wait for the map to load from Neon first.")
            return
        self.run_btn.setEnabled(False)
        self.cancel_btn.show()
        self.bar.show()
        self.bar.setRange(0, 0)
        self.status.setText("Starting…")
        self.job = self.w.run_job("benchmark", "benchmark", sizes=SIZES, repeat=REPEAT,
                                  explain=self.explain_box.isChecked(),
                                  done=self.finished, failed=self.failed, progress=self.progress)

    def progress(self, done, total, text):
        self.bar.setRange(0, max(total, 1))
        self.bar.setValue(done)
        self.status.setText(text)

    def _idle(self):
        self.job = None
        self.run_btn.setEnabled(True)
        self.run_btn.setText("Run again")
        self.cancel_btn.hide()
        self.bar.hide()

    def failed(self, message):
        self._idle()
        self.status.setText("Benchmark cancelled." if message == "Cancelled." else f"Benchmark failed: {message}")
        return True

    def on_map_switching(self, old, new):
        """A benchmark belongs to one map: stop a run and clear the charts when the map changes."""
        if self.job is not None:
            self.w.worker.cancel()
        self.result = None
        for story, lines, bars, card in self.sections.values():
            story.setText("Run the benchmark to compare the approaches.")
            for chart, _ in lines:
                chart.set_data({})
            bars.set_metrics([])
        self.run_btn.setText("Run benchmark")
        self.status.setText(f"Map: {new}" if new else "")

    def finished(self, result):
        self._idle()
        self.result = result
        self.status.setText(f"Done · map {result['map']} · median of {result['repeat']} runs · over Neon"
                            if result.get("map") else f"Done · median of {result['repeat']} runs · over Neon")
        biggest = result["sizes"][-1]
        for question, (story, lines, bars, card) in self.sections.items():
            for chart, value in lines:
                chart.set_data(line_data(result, question, value))
            story.setText(f"{speed_story(result, question)}  {agreement_text(result, question)}")
            runs = {m: {question: next(r for r in result["series"][m][question] if r["n"] == biggest)}
                    for m, _, _ in SERIES}
            bars.set_metrics(comparison_metrics(runs, question))
            if card.stack.currentIndex() == 1:
                card.refresh_table()
