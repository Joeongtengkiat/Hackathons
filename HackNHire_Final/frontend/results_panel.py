"""results_panel.py - the panel beside the map: each farm's verdict from both approaches, and how
the two approaches compared on the last check, for each question:

  "Is the point in forest of 31 Dec 2020?"          the points of the check, as one batch per approach
  "Does the outline overlap forest of 31 Dec 2020?" the outlines of the check, as one batch per approach

Forest of 31 Dec 2020 (the EUDR cut-off date) includes forest cleared since: the backend's
common.FOREST_2020. The table's "2020 forest" column answers it for each farm.

Numbers come from NeonService.check()["runs"] (see metering.py). Lower is better for all of them.
"""

import json

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import (QAbstractItemView, QHeaderView, QLabel, QScrollArea, QTableWidget,
                             QTableWidgetItem, QVBoxLayout, QWidget)

from charts import ChartCard, PairBars, fmt_bytes, fmt_number
from eudr_layer import colour_icon
from legend import DEFORESTATION, METHODS, VERDICT_STYLE
from report import verdict_detail

APPROACHES = ("stencils", "polygons")
SERIES = [(key, METHODS[key][0], METHODS[key][1]) for key in APPROACHES]
APPROACH_OF_COLUMN = {2: "stencils", 3: "polygons"}
QUESTIONS = {"point": "Is the point in forest of 31 Dec 2020?",
             "outline": "Does the outline overlap forest of 31 Dec 2020?"}
COLUMNS = ["Farm", "Verdict", "Stencils", "Outlines", "2020 forest", "Deforested ha", "Protected"]


def fmt_ms(v):
    return "–" if v is None else f"{fmt_number(v)} ms"


def comparison_metrics(runs, question):
    """[(label, {approach: value}, formatter)] for one question, or [] if it wasn't asked."""
    per = {m: runs.get(m, {}).get(question) for m in APPROACHES}
    if not any(per.values()):
        return []

    def pick(fn):
        return {m: (fn(r) if r else None) for m, r in per.items()}

    def server(key):
        return lambda r: None if r.get("server") is None else r["server"][key]

    metrics = [
        ("Time over Neon", pick(lambda r: 1000.0 * r["seconds"]), fmt_ms),
        ("  waiting on Neon", pick(lambda r: 1000.0 * r["client_s"]), fmt_ms),
        ("  computing locally", pick(lambda r: 1000.0 * max(r["seconds"] - r["client_s"], 0.0)), fmt_ms),
        ("Round trips", pick(lambda r: r["round_trips"]), fmt_number),
        ("Rows returned", pick(lambda r: r["rows"]), fmt_number),
        ("Data returned", pick(lambda r: r["bytes"]), fmt_bytes),
    ]
    if any(r and r.get("server") for r in per.values()):
        metrics += [
            ("Server execution", pick(server("server_exec_ms")), fmt_ms),
            ("8 KB blocks touched", pick(lambda r: None if r.get("server") is None
                                         else r["server"]["shared_hit"] + r["server"]["shared_read"]),
             fmt_number),
        ]
    return metrics


def winners_sentence(metrics):
    """'Faster: Stencils (120 ms vs 340 ms) · Fewer round trips: …' - lower wins; ties say so."""
    names = {"Time over Neon": "Faster", "Round trips": "Fewer round trips",
             "Data returned": "Less data", "Server execution": "Less server time",
             "8 KB blocks touched": "Fewer blocks"}
    parts = []
    for label, values, fmt in metrics:
        if label not in names:
            continue
        a, b = values.get("stencils"), values.get("polygons")
        if a is None or b is None:
            continue
        if a == b:
            parts.append(f"{names[label]}: tie ({fmt(a)})")
            continue
        win, lose = ("stencils", "polygons") if a < b else ("polygons", "stencils")
        parts.append(f"{names[label]}: {METHODS[win][0]} ({fmt(values[win])} vs {fmt(values[lose])})")
    return " · ".join(parts)


def agreement(rows, kind):
    """(same verdict, of) for the rows of one kind."""
    picked = [r for r in rows if r["kind"] == kind]
    return sum(1 for r in picked if r["agree"]["stencils_polygons"]), len(picked)


def fmt_table_ha(ha):
    return "0" if ha == 0 else ("<0.01" if ha < 0.005 else f"{ha:,.2f}")


def ha_text(row, code):
    if row["kind"] == "point":
        return "–"
    return fmt_table_ha(float(row["results"]["polygons"]["ha"].get(str(code), 0.0)))


def forest_2020_text(row):
    """The two key questions for one farm: a point is in 2020 forest or not; an outline overlaps
    this many hectares of it (from the outlines' exact areas)."""
    res = row["results"]["polygons"]
    if res["outside"]:
        return "–"
    if row["kind"] == "point":
        return "yes" if res["in_forest_2020"] else "no"
    return fmt_table_ha(float(res["forest_2020_ha"])) + " ha"


def protected_text(row):
    """In a protected area? A point: yes; an outline: the hectares inside one; '–' if not."""
    res = row["results"]["polygons"]
    if not res.get("protected_areas"):
        return "–"
    return "yes" if row["kind"] == "point" else fmt_table_ha(float(res["protected_ha"])) + " ha"


def table_cells(row):
    """The texts of one table row (the verdict cell's colour is added by the panel)."""
    def verdict_text(verdict):
        _, icon, headline = VERDICT_STYLE[verdict]
        source = "polygons" if row["results"]["polygons"]["verdict"] == verdict else "stencils"
        why = "" if verdict in ("FAIL", "PASS") else f" · {verdict_detail(row['results'][source])}"
        return f"{icon} {headline}{why}"

    name = row["name"] + (" (point)" if row["kind"] == "point" else "")
    if row.get("stale"):
        name += " - edited, check again"
    return [name, verdict_text(row["verdict"]),
            VERDICT_STYLE[row["results"]["stencils"]["verdict"]][1],
            VERDICT_STYLE[row["results"]["polygons"]["verdict"]][1],
            forest_2020_text(row), ha_text(row, DEFORESTATION), protected_text(row)]


class ResultsPanel(QScrollArea):
    def __init__(self, window):
        super().__init__()
        self.w = window
        self.rows = []
        self.setWidgetResizable(True)
        self.setMinimumWidth(440)
        self.setFrameShape(QScrollArea.NoFrame)
        body = QWidget()
        body.setObjectName("panel")
        self.setWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(10)

        layout.addWidget(self._label("Results", "heading"))
        self.hint = self._label("Draw a polygon or drop a point, then press Check. Each farm is checked "
                                "with both approaches against Neon.", "secondary")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setMinimumSectionSize(64)
        self.table.setMinimumHeight(150)
        self.table.cellClicked.connect(self.row_clicked)
        layout.addWidget(self.table)

        layout.addSpacing(6)
        layout.addWidget(self._label("Stencils vs outlines · last check", "heading"))
        note = self._label("Times are over Neon, from this computer, for one batch per approach. "
                           "Server numbers come from EXPLAIN (ANALYZE, BUFFERS): the same queries run "
                           "again inside PostgreSQL, so the cache is warm. Shorter bars are better.",
                           "muted")
        note.setWordWrap(True)
        layout.addWidget(note)

        self.blocks = {}
        for question, title in QUESTIONS.items():
            summary = self._label("", "secondary")
            summary.setWordWrap(True)
            bars = PairBars(SERIES)
            card = ChartCard(title, bars, [(label, colour) for _, label, colour in SERIES], bars.table)
            layout.addWidget(card)
            layout.addWidget(summary)
            card.hide()
            summary.hide()
            self.blocks[question] = (card, bars, summary)
        layout.addStretch(1)

        window.listen("results", self.set_rows)
        window.listen("checked", self.set_check)
        window.listen("map_switching", lambda old, new: self.set_check({"rows": [], "runs": {}}))

    @staticmethod
    def _label(text, role):
        label = QLabel(text)
        label.setTextFormat(Qt.PlainText)
        label.setProperty("role", role)
        return label

    # ----- the farms table -----
    def set_rows(self, rows):
        self.rows = rows
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            colour, _, _ = VERDICT_STYLE[row["verdict"]]
            for c, text in enumerate(table_cells(row)):
                item = QTableWidgetItem(text)
                if c == 1:
                    item.setIcon(colour_icon(colour))
                    font = QFont(item.font())
                    font.setBold(True)
                    item.setFont(font)
                if c in (2, 3):  # each approach's verdict as its icon; the words are in the tooltip
                    item.setTextAlignment(Qt.AlignCenter)
                    res = row["results"][APPROACH_OF_COLUMN[c]]
                    item.setToolTip(f"{VERDICT_STYLE[res['verdict']][2]}: {verdict_detail(res)}")
                if c >= 4:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if c == 4:
                    item.setToolTip("Forest on 31 Dec 2020, the EUDR cut-off date (including forest cleared "
                                    "since): is the point in it, or how much of the outline overlaps it")
                if c == 6:
                    names = row["results"]["polygons"].get("protected_areas")
                    item.setToolTip("In a protected area (WDPA): " + ", ".join(names) + ". A farm in one needs "
                                    "review: check that farming is allowed there" if names
                                    else "Not in any protected area stored with this map")
                if c == 0:
                    item.setToolTip(text)
                if row.get("stale"):
                    item.setForeground(QColor("#898781"))
                self.table.setItem(r, c, item)
        self.hint.setVisible(not rows)

    def row_clicked(self, r, _c):
        if 0 <= r < len(self.rows):
            key = json.dumps(self.rows[r]["key"])
            self.w.js(f"selectShape({key}, true); zoomToShape({key});")

    # ----- the comparison -----
    def set_check(self, result):
        runs, rows = result.get("runs") or {}, result["rows"]
        for question, (card, bars, summary) in self.blocks.items():
            metrics = comparison_metrics(runs, question)
            if not metrics:
                card.hide()
                summary.hide()
                continue
            same, of = agreement(rows, question)
            noun = "point" if question == "point" else "outline"
            card.title.setText(f"{QUESTIONS[question]}  ·  {of} {noun}{'' if of == 1 else 's'}")
            bars.set_metrics(metrics)
            if card.stack.currentIndex() == 1:
                card.refresh_table()
            summary.setText(f"Same verdict from both approaches: {same} of {of}.  {winners_sentence(metrics)}")
            card.show()
            summary.show()
