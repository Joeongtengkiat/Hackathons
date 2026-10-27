"""charts.py - small native charts (QPainter) for comparing the two approaches.

  ChartCard  a title, a legend and a "Show table" switch: every chart has a table twin
  PairBars   small multiples: one row per metric, a bar per approach, each metric on its own
             scale (never two measures on one axis); lower is better, so the shorter bar wins
  LineChart  values against number of farms on log-log axes, with a crosshair tooltip

Marks follow the dataviz rules: thin bars with rounded data ends, 2px lines, 8px markers with a
2px surface ring, recessive grid, text always in ink colours (the series colour is only on marks).
"""

import math

from PyQt5.QtCore import QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import (QFrame, QHBoxLayout, QHeaderView, QLabel, QPushButton, QStackedWidget,
                             QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e5e4df"


def bar_length(value, vmax, width):
    """Pixels for a bar: proportional to value, from a zero baseline; a non-zero value is never
    drawn thinner than 2 px, so it can't vanish next to a big one."""
    if not value or vmax <= 0 or value <= 0:
        return 0.0
    return max(2.0, width * value / vmax)


def log_position(value, lo, hi, length):
    """Where value falls between lo and hi on a log scale of the given length (pixels)."""
    a, b = math.log10(lo), math.log10(hi)
    return length * (math.log10(max(value, lo)) - a) / (b - a) if b > a else 0.0


def decades(lo, hi):
    """Powers of ten covering lo..hi: (10^floor(log lo), 10^ceil(log hi))."""
    lo = max(lo, 1e-9)
    low, high = math.floor(math.log10(lo)), math.ceil(math.log10(max(hi, lo)))
    return 10.0 ** low, 10.0 ** max(high, low + 1)


def fmt_number(v):
    if v is None:
        return "–"
    if v >= 100 or float(v).is_integer():
        return f"{v:,.0f}"
    return f"{v:,.1f}" if v >= 10 else f"{v:,.2f}"


def fmt_bytes(n):
    if n is None:
        return "–"
    for unit in ("B", "kB", "MB"):  # decimal units, so log-axis ticks read 1 kB, 10 kB, 1 MB
        if n < 1000 or unit == "MB":
            if unit == "B":
                return f"{n:,.0f} B"
            return f"{n:,.0f} {unit}" if float(n).is_integer() else f"{n:,.1f} {unit}"
        n /= 1000.0


def swatch(colour, size=10):
    box = QLabel()
    box.setFixedSize(size, size)
    box.setStyleSheet(f"background: {colour}; border-radius: 2px;")
    return box


class ChartCard(QFrame):
    """A chart with its title, legend and a table twin (Show table / Show chart)."""

    def __init__(self, title, chart, series, table_fn, note=None, parent=None):
        """series = [(label, colour)]; table_fn() -> (headers, rows of strings)."""
        super().__init__(parent)
        self.setObjectName("card")
        self.chart, self.table_fn = chart, table_fn
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        head = QHBoxLayout()
        self.title = QLabel(title)
        self.title.setProperty("role", "title")
        head.addWidget(self.title, 1)
        self.toggle = QPushButton("Show table")
        self.toggle.setFlat(True)
        self.toggle.setCursor(Qt.PointingHandCursor)
        self.toggle.clicked.connect(self.flip)
        head.addWidget(self.toggle)
        layout.addLayout(head)

        if len(series) >= 2:  # one series needs no legend: the title names it
            legend = QHBoxLayout()
            legend.setSpacing(6)
            for label, colour in series:
                legend.addWidget(swatch(colour))
                text = QLabel(label)
                text.setProperty("role", "secondary")
                legend.addWidget(text)
                legend.addSpacing(10)
            legend.addStretch(1)
            layout.addLayout(legend)

        self.stack = QStackedWidget()
        self.table = QTableWidget()
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.stack.addWidget(chart)
        self.stack.addWidget(self.table)
        layout.addWidget(self.stack)
        if note:
            foot = QLabel(note)
            foot.setWordWrap(True)
            foot.setProperty("role", "muted")
            layout.addWidget(foot)

    def flip(self):
        showing_table = self.stack.currentIndex() == 1
        if not showing_table:
            self.refresh_table()
        self.stack.setCurrentIndex(0 if showing_table else 1)
        self.toggle.setText("Show table" if showing_table else "Show chart")

    def refresh_table(self):
        headers, rows = self.table_fn()
        self.table.clear()
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, text in enumerate(row):
                item = QTableWidgetItem(text)
                if c:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(r, c, item)
        self.table.setMinimumHeight(min(28 * (len(rows) + 1) + 4, 320))


class PairBars(QWidget):
    """One row per metric; inside it one bar per series on that metric's own scale.
    metrics = [(label, {series_key: value or None}, formatter)], series = [(key, label, colour)]."""

    LABEL_W, ROW_GAP, BAR_H, BAR_GAP = 150, 14, 12, 6

    def __init__(self, series, parent=None):
        super().__init__(parent)
        self.series = series
        self.metrics = []
        self._hits = []
        self.setMouseTracking(True)

    def set_metrics(self, metrics):
        self.metrics = metrics
        self.setMinimumHeight(self.sizeHint().height())
        if self.parentWidget() is not None:  # a QStackedWidget (ChartCard) doesn't follow on its own
            self.parentWidget().setMinimumHeight(self.minimumHeight())
        self.updateGeometry()
        self.update()

    def row_height(self):
        return len(self.series) * (self.BAR_H + self.BAR_GAP) + self.ROW_GAP

    def sizeHint(self):
        return QSize(420, max(40, len(self.metrics) * self.row_height() + 4))

    def minimumSizeHint(self):
        return QSize(300, self.sizeHint().height())

    def table(self):
        headers = ["Metric"] + [label for _, label, _ in self.series]
        return headers, [[label] + [fmt(values.get(key)) for key, _, _ in self.series]
                         for label, values, fmt in self.metrics]

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        font = QFont(self.font())
        p.setFont(font)
        fm = p.fontMetrics()
        value_w = 78
        plot_w = max(self.width() - self.LABEL_W - value_w - 8, 20)
        self._hits = []
        y = 2
        for label, values, fmt in self.metrics:
            p.setPen(QColor(INK_2))
            p.drawText(QRectF(0, y, self.LABEL_W - 8, self.row_height() - self.ROW_GAP),
                       Qt.AlignLeft | Qt.AlignVCenter | Qt.TextWordWrap, label)
            present = [v for v in values.values() if v is not None]
            vmax = max(present) if present else 0
            # zero baseline
            p.setPen(QPen(QColor(GRID), 1))
            p.drawLine(QPointF(self.LABEL_W, y - 1), QPointF(self.LABEL_W, y + self.row_height() - self.ROW_GAP))
            by = y
            for key, name, colour in self.series:
                value = values.get(key)
                length = bar_length(value, vmax, plot_w)
                rect = QRectF(self.LABEL_W + 1, by, length, self.BAR_H)
                if length:
                    path = QPainterPath()
                    r = min(4.0, length / 2, self.BAR_H / 2)
                    path.addRoundedRect(rect, r, r)
                    # square the end on the baseline, round only the data end
                    path.addRect(QRectF(rect.left(), rect.top(), min(r, length), rect.height()))
                    p.fillPath(path.simplified(), QColor(colour))
                p.setPen(QColor(INK if value == vmax and len(present) > 1 else INK_2))
                text = fmt(value)
                p.drawText(QRectF(self.LABEL_W + 6 + length, by, value_w + plot_w - length, self.BAR_H),
                           Qt.AlignLeft | Qt.AlignVCenter, text)
                hit = QRectF(self.LABEL_W, by - 1, max(length, fm.horizontalAdvance(text) + 10), self.BAR_H + 2)
                self._hits.append((hit, f"{label}\n{name}: {text}"))
                by += self.BAR_H + self.BAR_GAP
            y += self.row_height()
        p.end()

    def mouseMoveEvent(self, event):
        for rect, tip in self._hits:
            if rect.contains(QPointF(event.pos())):
                self.setToolTip(tip)
                return
        self.setToolTip("")


class LineChart(QWidget):
    """Lines of value against number of farms, both on log scales, with a crosshair tooltip.
    series = [(key, label, colour)]; data = {key: [(x, y), ...]} with the same x values."""

    hovered = pyqtSignal(object)
    PAD_L, PAD_R, PAD_T, PAD_B = 96, 16, 10, 34

    def __init__(self, series, y_label, y_fmt=fmt_number, parent=None):
        super().__init__(parent)
        self.series, self.y_label, self.y_fmt = series, y_label, y_fmt
        self.data = {}
        self.hover_x = None
        self.setMouseTracking(True)
        self.setMinimumHeight(220)

    def set_data(self, data):
        self.data = data
        self.hover_x = None
        self.update()

    def xs(self):
        values = sorted({x for points in self.data.values() for x, _ in points})
        return values

    def ranges(self):
        xs = self.xs()
        ys = [y for points in self.data.values() for _, y in points if y and y > 0]
        if not xs or not ys:
            return None
        return decades(min(xs), max(xs)), decades(min(ys), max(ys))

    def table(self):
        headers = ["Farms"] + [label for _, label, _ in self.series]
        rows = []
        for x in self.xs():
            row = [f"{x:,}"]
            for key, _, _ in self.series:
                y = dict(self.data.get(key, [])).get(x)
                row.append(self.y_fmt(y))
            rows.append(row)
        return headers, rows

    def _plot_rect(self):
        return QRectF(self.PAD_L, self.PAD_T, max(self.width() - self.PAD_L - self.PAD_R, 10),
                      max(self.height() - self.PAD_T - self.PAD_B, 10))

    def _point(self, plot, x, y, xr, yr):
        px = plot.left() + log_position(x, xr[0], xr[1], plot.width())
        py = plot.bottom() - log_position(y, yr[0], yr[1], plot.height())
        return QPointF(px, py)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        plot = self._plot_rect()
        ranges = self.ranges()
        if ranges is None:
            p.setPen(QColor(MUTED))
            p.drawText(self.rect(), Qt.AlignCenter, "Run the benchmark to fill this chart.")
            p.end()
            return
        xr, yr = ranges
        # grid and axis labels at powers of ten
        p.setPen(QColor(MUTED))
        exp = math.log10(yr[0])
        while 10 ** exp <= yr[1] * 1.0001:
            yy = plot.bottom() - log_position(10 ** exp, yr[0], yr[1], plot.height())
            p.setPen(QPen(QColor(GRID), 1))
            p.drawLine(QPointF(plot.left(), yy), QPointF(plot.right(), yy))
            p.setPen(QColor(INK_2))
            p.drawText(QRectF(24, yy - 8, self.PAD_L - 30, 16), Qt.AlignRight | Qt.AlignVCenter,
                       self.y_fmt(10 ** exp))
            exp += 1
        for x in self.xs():
            xx = plot.left() + log_position(x, xr[0], xr[1], plot.width())
            p.setPen(QColor(INK_2))
            p.drawText(QRectF(xx - 30, plot.bottom() + 4, 60, 16), Qt.AlignHCenter | Qt.AlignTop, f"{x:,}")
        p.setPen(QColor(MUTED))
        p.drawText(QRectF(plot.left(), plot.bottom() + 18, plot.width(), 16), Qt.AlignHCenter,
                   "farms in one check (log scale)")
        p.save()
        p.translate(12, plot.center().y())
        p.rotate(-90)
        p.drawText(QRectF(-plot.height() / 2, -10, plot.height(), 16), Qt.AlignCenter, self.y_label)
        p.restore()

        # crosshair
        if self.hover_x is not None:
            xx = plot.left() + log_position(self.hover_x, xr[0], xr[1], plot.width())
            p.setPen(QPen(QColor(MUTED), 1, Qt.DashLine))
            p.drawLine(QPointF(xx, plot.top()), QPointF(xx, plot.bottom()))

        # lines, then markers with a surface ring
        for key, _, colour in self.series:
            points = [self._point(plot, x, y, xr, yr) for x, y in self.data.get(key, []) if y and y > 0]
            if len(points) > 1:
                path = QPainterPath(points[0])
                for pt in points[1:]:
                    path.lineTo(pt)
                p.setPen(QPen(QColor(colour), 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                p.setBrush(Qt.NoBrush)
                p.drawPath(path)
            p.setPen(QPen(QColor(SURFACE), 2))
            p.setBrush(QColor(colour))
            for pt in points:
                p.drawEllipse(pt, 4.5, 4.5)

        if self.hover_x is not None:
            self._draw_tooltip(p, plot, xr)
        p.end()

    def _draw_tooltip(self, p, plot, xr):
        lines = [(None, f"{self.hover_x:,} farms")]
        for key, label, colour in self.series:
            y = dict(self.data.get(key, [])).get(self.hover_x)
            lines.append((colour, f"{label}: {self.y_fmt(y)}"))
        fm = p.fontMetrics()
        w = max(fm.horizontalAdvance(t) for _, t in lines) + 30
        h = len(lines) * 18 + 10
        xx = plot.left() + log_position(self.hover_x, xr[0], xr[1], plot.width())
        x = xx + 10 if xx + 10 + w < self.width() else xx - 10 - w
        box = QRectF(x, plot.top() + 4, w, h)
        p.setPen(QPen(QColor(GRID), 1))
        p.setBrush(QColor(SURFACE))
        p.drawRoundedRect(box, 6, 6)
        for i, (colour, text) in enumerate(lines):
            ty = box.top() + 5 + i * 18
            if colour:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(colour))
                p.drawRoundedRect(QRectF(box.left() + 8, ty + 4, 10, 10), 2, 2)
            p.setPen(QColor(INK if colour is None else INK_2))
            p.drawText(QRectF(box.left() + (24 if colour else 8), ty, w, 18), Qt.AlignLeft | Qt.AlignVCenter, text)

    def mouseMoveEvent(self, event):
        xs, ranges = self.xs(), self.ranges()
        if not xs or ranges is None:
            return
        plot = self._plot_rect()
        positions = [plot.left() + log_position(x, ranges[0][0], ranges[0][1], plot.width()) for x in xs]
        nearest = min(range(len(xs)), key=lambda i: abs(positions[i] - event.pos().x()))
        if xs[nearest] != self.hover_x:
            self.hover_x = xs[nearest]
            self.hovered.emit(self.hover_x)
            self.update()

    def leaveEvent(self, event):
        self.hover_x = None
        self.update()
