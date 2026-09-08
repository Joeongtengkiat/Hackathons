"""The predictions table: model, delegate, and the labels it renders.

The model holds no copy of anything. It stores a list of *indices* into
app.dataset.items and reads the score and the threshold live on every data()
call, so moving the slider is a viewport repaint rather than a rebuild - and
there is no second copy of the results that could fall out of date.
"""

from __future__ import annotations

import math

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QStyledItemDelegate

from .. import theme as T
from .components import score_color

COLUMNS = ["Image", "Score", "Truth", "Result"]

#: custom roles. UserRole itself is taken by the sort key the proxy reads, so
#: these start one above it.
ROLE_SCORE = Qt.ItemDataRole.UserRole + 1   # the raw float, for the bar delegate
ROLE_INDEX = Qt.ItemDataRole.UserRole + 2   # position in dataset.items


def label_text(label) -> str:
    """Ground truth as a word. An em dash means unlabeled, not "real"."""
    return "—" if label is None else ("AI" if label == 1 else "Real")


def result_text(label, pred) -> str:
    """Correct, false positive or false negative - blank without both halves."""
    if label is None or pred is None:
        return "—"
    if label == pred:
        return "✓"
    return "✗ FP" if pred == 1 else "✗ FN"


class ResultsModel(QAbstractTableModel):
    """Reads live from the app's dataset/result/threshold - no copies."""

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.rows: list = []

    def set_rows(self, rows):
        """Replace the visible set with these dataset indices (filtering)."""
        self.beginResetModel()
        self.rows = list(rows)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        # parent.isValid() means "children of a row" - a flat table has none
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return COLUMNS[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        """One cell, one role. Called constantly - it must stay cheap."""
        if not index.isValid() or self.app.dataset is None:
            return None
        di = self.rows[index.row()]           # view row -> dataset index
        item = self.app.dataset.items[di]
        score = self.app.score_at(di)
        pred = None if score is None else int(score >= self.app.threshold)
        col = index.column()

        if role == Qt.ItemDataRole.UserRole:                    # sort key
            # sorting on the number, not the formatted string: "0.9" must not
            # sort above "0.85". Unscored/unlabeled use -1 so they group at one
            # end instead of interleaving.
            return [item.rel_path, -1.0 if score is None else score,
                    -1 if item.label is None else item.label,
                    result_text(item.label, pred)][col]

        if role == Qt.ItemDataRole.DisplayRole:
            return [item.rel_path,
                    "—" if score is None else f"{score:.4f}",
                    label_text(item.label),
                    result_text(item.label, pred)][col]

        if role == Qt.ItemDataRole.ToolTipRole:
            if col == 3 and item.label is not None and pred is not None:
                return ("correct" if item.label == pred else
                        "false positive — authentic, flagged AI" if pred == 1 else
                        "false negative — AI, missed")
            return item.path

        if role == Qt.ItemDataRole.ForegroundRole:
            if col == 2 and item.label is not None:
                return QBrush(QColor(T.AI_COLOR if item.label else T.REAL_COLOR))
            if col == 3:
                txt = result_text(item.label, pred)
                if txt.startswith("✓"):
                    return QBrush(QColor(T.GOOD))
                if txt.startswith("✗"):
                    return QBrush(QColor(T.BAD))
                return QBrush(QColor(T.TEXT_FAINT))

        if role == Qt.ItemDataRole.TextAlignmentRole and col > 0:
            return int(Qt.AlignmentFlag.AlignCenter)

        if role == ROLE_SCORE:
            return score
        if role == ROLE_INDEX:
            return di
        return None


class ScoreBarDelegate(QStyledItemDelegate):
    """Confidence drawn as a bar behind the number.

    The bar makes a column of four-decimal floats scannable: you see the shape
    of the distribution down the page without reading a single value.
    """

    def paint(self, painter: QPainter, option, index):
        score = index.data(ROLE_SCORE)
        if score is None or (isinstance(score, float) and math.isnan(score)):
            return super().paint(painter, option, index)   # unscored: plain cell
        painter.save()
        r = option.rect.adjusted(6, 8, -6, -8)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(T.TRACK)))
        painter.drawRoundedRect(r, T.R_TINY, T.R_TINY)
        c = QColor(score_color(score))
        c.setAlpha(120)                       # a wash over the track, not a slab
        painter.setBrush(QBrush(c))
        # trim the right edge by the unfilled fraction - a full-width rect
        # scaled down from the left would move the bar instead of shortening it
        painter.drawRoundedRect(
            r.adjusted(0, 0, -int(r.width() * (1.0 - float(score))), 0),
            T.R_TINY, T.R_TINY)
        painter.setPen(QPen(QColor(T.TEXT)))
        f = QFont(painter.font())
        f.setBold(True)
        painter.setFont(f)
        painter.drawText(option.rect, Qt.AlignmentFlag.AlignCenter, f"{score:.4f}")
        painter.restore()
