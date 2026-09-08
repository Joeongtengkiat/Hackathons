"""The unlabeled destination: every image, badged AI or authentic.

There is no truth to measure against here, so there is nothing to plot - the
answer is the verdict on each picture. A QListView in icon mode reflows on
resize and only paints what is on screen, and thumbnails are decoded at tile
size rather than full resolution, so a few thousand images stay responsive.
"""

from __future__ import annotations

import math
import os

from PyQt6.QtCore import QAbstractListModel, QModelIndex, QRect, QSize, Qt, QUrl
from PyQt6.QtGui import (
    QBrush, QColor, QDesktopServices, QFont, QImageReader, QPainter, QPen,
    QPixmap
)
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QListView, QStyle, QStyledItemDelegate,
    QVBoxLayout, QWidget
)

from .. import theme as T
from . import components as C
from .components import Chip

FILTER_ALL, FILTER_AI, FILTER_REAL = range(3)

TILE_W, TILE_H = 168, 186
THUMB_H = 132                       # the picture; the rest is the verdict strip
PAD = 8

ROLE_ITEM = Qt.ItemDataRole.UserRole + 1        # (path, name, score, is_ai)

#: decoded thumbnails, keyed by path. Bounded because a big folder would
#: otherwise pin every image it has ever drawn in memory.
_CACHE: dict = {}
_CACHE_MAX = 512


def thumbnail(path: str, width: int, height: int) -> QPixmap:
    """Decode straight to tile size - never load the full-resolution image.

    QImageReader.setScaledSize lets the decoder do the downscale as it reads,
    so a 24 MP photo never exists at full size in memory. Returns a null pixmap
    for anything undecodable; the delegate draws "no preview" for that.
    """
    hit = _CACHE.get(path)
    if hit is not None:
        return hit

    reader = QImageReader(path)
    reader.setAutoTransform(True)               # honour the EXIF orientation
    size = reader.size()
    if size.isValid() and size.width() > 0 and size.height() > 0:
        scale = min(width / size.width(), height / size.height(), 1.0)
        reader.setScaledSize(QSize(max(int(size.width() * scale), 1),
                                   max(int(size.height() * scale), 1)))
    image = reader.read()
    pix = QPixmap() if image.isNull() else QPixmap.fromImage(image)

    if len(_CACHE) >= _CACHE_MAX:
        # a full clear, not an eviction: an LRU costs bookkeeping on every hit,
        # and re-decoding at tile size after a scroll is a few milliseconds
        _CACHE.clear()
    _CACHE[path] = pix
    return pix


class GalleryModel(QAbstractListModel):
    """Reads live from the app's dataset and threshold - no copies."""

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
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        """One tile's worth of data. ROLE_ITEM carries the whole tuple, so the
        delegate makes one call per tile rather than four."""
        if not index.isValid() or self.app.dataset is None:
            return None
        di = self.rows[index.row()]
        item = self.app.dataset.items[di]
        score = self.app.score_at(di)

        if role == ROLE_ITEM:
            is_ai = None if score is None else score >= self.app.threshold
            return (item.path, item.name, score, is_ai)
        if role == Qt.ItemDataRole.ToolTipRole:
            verdict = ("not scored" if score is None else
                       f"{score:.4f} — "
                       + ("AI-generated" if score >= self.app.threshold
                          else "authentic"))
            return f"{item.rel_path}\n{verdict}\n\nDouble-click to open"
        return None


class TileDelegate(QStyledItemDelegate):
    """One picture, one verdict pill, one score."""

    def sizeHint(self, option, index):
        # fixed, and matched by setUniformItemSizes on the view - that is what
        # lets QListView lay out thousands of tiles without measuring them
        return QSize(TILE_W, TILE_H)

    def paint(self, painter: QPainter, option, index):
        """Draw one card: border, letterboxed thumbnail, verdict pill, score."""
        data = index.data(ROLE_ITEM)
        if data is None:
            return
        path, name, score, is_ai = data
        r = option.rect.adjusted(PAD // 2, PAD // 2, -PAD // 2, -PAD // 2)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        painter.setPen(QPen(QColor(T.ACCENT if selected else T.BORDER)))
        painter.setBrush(QBrush(QColor(T.CARD)))
        painter.drawRoundedRect(r, T.R_CARD, T.R_CARD)

        # -- the picture, letterboxed on the inert track colour
        photo = QRect(r.left() + 1, r.top() + 1, r.width() - 2, THUMB_H)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(T.TRACK)))
        # rounded at the top, square at the bottom, drawn as a rounded rect
        # overlapping a plain one - the picture area meets the verdict strip
        # flush, and only the card's outer corners are round
        painter.drawRoundedRect(photo.adjusted(0, 0, 0, 12),
                                T.R_CARD - 1, T.R_CARD - 1)
        painter.drawRect(photo.adjusted(0, 6, 0, 0))

        pix = thumbnail(path, photo.width() - 8, photo.height() - 8)
        if pix.isNull():
            painter.setPen(QPen(QColor(T.TEXT_FAINT)))
            painter.drawText(photo, Qt.AlignmentFlag.AlignCenter, "no preview")
        else:
            painter.drawPixmap(
                photo.left() + (photo.width() - pix.width()) // 2,
                photo.top() + (photo.height() - pix.height()) // 2, pix)

        # -- the verdict strip
        # the pill carries text, so it takes the fill variant of the accent -
        # the brand red itself is tuned to read as a line against the ground
        # and is too bright to put type on
        strip = QRect(r.left() + 10, photo.bottom() + 8, r.width() - 20, 20)
        if is_ai is None:
            text, fill = "—", T.TEXT_FAINT
        else:
            text, fill = ("AI", T.ACCENT_FILL) if is_ai else ("REAL", T.REAL_COLOR)

        font = QFont(painter.font())
        font.setBold(True)
        font.setPointSizeF(max(font.pointSizeF() - 1.0, 6.0))
        painter.setFont(font)
        pill_w = max(painter.fontMetrics().horizontalAdvance(text) + 16, 34)
        pill = QRect(strip.left(), strip.top(), pill_w, strip.height())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(fill)))
        painter.drawRoundedRect(pill, 9, 9)
        # white reads on the red but not on the teal, so ask the palette
        painter.setPen(QPen(QColor(T.contrast_text(fill))))
        painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, text)

        painter.setPen(QPen(QColor(T.TEXT_DIM)))
        painter.drawText(
            strip, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            "—" if score is None or math.isnan(score) else f"{score:.3f}")

        painter.restore()


class GalleryPage(QWidget):
    """What is AI and what is not, for data with no ground truth."""

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self._filter = FILTER_ALL

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.chips = []
        for i, (text, tip) in enumerate([
            ("All", "Every image"),
            ("AI", "Predicted AI-generated at this threshold"),
            ("Real", "Predicted authentic at this threshold"),
        ]):
            chip = Chip(text)
            chip.setToolTip(tip)
            chip.clicked.connect(lambda _, idx=i: self.set_filter(idx))
            bar.addWidget(chip)
            self.chips.append(chip)
        self.chips[0].setChecked(True)
        bar.addStretch(1)
        self.count_label = QLabel("")
        self.count_label.setStyleSheet(
            f"color: {T.TEXT_FAINT}; font-size: {C.FS_SMALL}px;")
        bar.addWidget(self.count_label)
        lay.addLayout(bar)

        self.model = GalleryModel(app, self)
        self.view = QListView()
        self.view.setModel(self.model)
        self.view.setItemDelegate(TileDelegate(self.view))
        self.view.setViewMode(QListView.ViewMode.IconMode)
        self.view.setResizeMode(QListView.ResizeMode.Adjust)
        self.view.setMovement(QListView.Movement.Static)
        self.view.setUniformItemSizes(True)
        self.view.setSpacing(4)
        self.view.setWordWrap(False)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        # the tiles are cards in their own right, so the view is not a panel -
        # a white surface behind white cards reads as neither, and the columns
        # never divide evenly, which would leave a white band down one side
        self.view.setStyleSheet(
            "QListView { background: transparent; border: none; }")
        self.view.doubleClicked.connect(self._open_file)
        lay.addWidget(self.view, 1)

    def set_filter(self, idx: int):
        """All / AI / Real. The chips are mutually exclusive, enforced here."""
        self._filter = idx
        for i, chip in enumerate(self.chips):
            chip.setChecked(i == idx)
        self.refresh()

    def _open_file(self, index):
        """Double-click opens the image in the system viewer."""
        data = index.data(ROLE_ITEM)
        if data and os.path.isfile(data[0]):
            QDesktopServices.openUrl(QUrl.fromLocalFile(data[0]))

    def refresh(self, charts: bool = False):
        """Re-apply the filter at the current threshold.

        `charts` is unused here and exists so every page presents the same
        refresh() signature to AppWindow. A verdict grid has nothing to redraw.
        """
        app = self.app
        if app.dataset is None:
            self.model.set_rows([])
            self.count_label.setText("")
            return

        # one pass: n_ai counts the whole set, rows collects what passes the
        # filter, so the header can say "23 of 400 flagged" while showing 23
        rows, n_ai = [], 0
        for di in range(len(app.dataset.items)):
            score = app.score_at(di)
            pred = None if score is None else int(score >= app.threshold)
            if pred == 1:
                n_ai += 1
            if self._filter == FILTER_ALL:
                keep = True
            elif self._filter == FILTER_AI:
                keep = pred == 1
            else:
                keep = pred == 0
            if keep:
                rows.append(di)
        self.model.set_rows(rows)

        total = len(app.dataset)
        self.count_label.setText(
            f"{n_ai:,} of {total:,} flagged AI at {app.threshold:.3f}"
            + (f"   ·   showing {len(rows):,}" if len(rows) != total else ""))
