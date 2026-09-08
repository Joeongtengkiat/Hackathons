"""The working screen: a spinner, the phase list, and what is happening now.

The rest of this app deliberately shows finished results and sends progress to
the terminal. A run is the one place that breaks down: loading the bundle alone
is over a gigabyte off disk and can sit for half a minute before a single image
is scored. A window with a greyed-out button and no other movement is
indistinguishable from a hang, and the terminal is not always the thing being
looked at.

So this narrates. Three things, in descending size:

  * the phase list      where the run is, of the steps it will take
  * the detail line     what it is doing inside that phase, right now
  * the bar             how far through, when that number honestly exists

Nothing here computes anything. It renders what runner.py reports, and the
same text still goes to the terminal.

It is a child widget of the window, kept sized to it by AppWindow.resizeEvent,
rather than a dialog - see LoadingOverlay for why.
"""

from __future__ import annotations

import math
import time

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QBrush, QColor, QConicalGradient, QPainter, QPen
from PyQt6.QtWidgets import (
    QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QVBoxLayout, QWidget
)

from .. import theme as T
from . import components as C

#: phase states
PENDING, ACTIVE, DONE = range(3)


def fmt_duration(seconds: float) -> str:
    """Seconds as 4.2s under a minute, m:ss above it."""
    if seconds != seconds or seconds < 0:            # NaN or negative
        return "--"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, secs = divmod(int(round(seconds)), 60)
    return f"{minutes}:{secs:02d}"


class Spinner(QWidget):
    """Two counter-rotating comet arcs and a breathing core.

    Drawn rather than played from frames, so it stays crisp at any DPI. The
    fade is a conical gradient used as the pen's brush: the stroke is a full
    circle whose alpha falls off around it, which is what turns one
    drawEllipse into a head and a tail.

    The two rings turn in opposite directions at unrelated speeds, so the
    figure never repeats on a short cycle and never reads as a frozen image -
    the specific failure of a single smooth ring, where a stall and a spin
    look identical.
    """

    def __init__(self, size: int = 76, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        # decoration only - clicks belong to whatever is behind it
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._angle = 0.0
        self._t0 = time.perf_counter()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    # -- lifecycle: never burn a core animating an invisible widget --------
    def start(self):
        if not self._timer.isActive():
            self._t0 = time.perf_counter()
            self._timer.start(16)                    # ~60fps

    def stop(self):
        self._timer.stop()

    def showEvent(self, event):
        super().showEvent(event)
        self.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.stop()

    def _tick(self):
        """Advance the rotation. update() requests a paint, it does not paint."""
        self._angle = (self._angle + 3.0) % 360.0
        self.update()

    # -- painting ----------------------------------------------------------
    def _comet(self, painter, rect: QRectF, angle: float, color: str,
               width: float, tail: float):
        """One ring: a full-circle stroke whose alpha falls off behind the head."""
        head = QColor(color)
        mid = QColor(color)
        mid.setAlpha(90)
        gone = QColor(color)
        gone.setAlpha(0)

        grad = QConicalGradient(rect.center(), angle)
        grad.setColorAt(0.0, head)
        grad.setColorAt(tail * 0.45, mid)
        grad.setColorAt(tail, gone)
        grad.setColorAt(1.0, gone)

        pen = QPen(QBrush(grad), width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(rect)

    def paintEvent(self, event):
        """Two comet rings and the breathing core, back to front."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        side = min(self.width(), self.height())
        cx, cy = self.width() / 2.0, self.height() / 2.0

        # outer ring, clockwise
        r1 = side / 2.0 - 4.0
        self._comet(painter, QRectF(cx - r1, cy - r1, r1 * 2, r1 * 2),
                    -self._angle, T.ACCENT, 3.4, 0.78)

        # inner ring, counter-clockwise and slower - the two never sync up
        r2 = r1 * 0.62
        self._comet(painter, QRectF(cx - r2, cy - r2, r2 * 2, r2 * 2),
                    self._angle * 0.63 + 140.0, T.SECONDARY, 2.4, 0.62)

        # core: a slow breath, so even a long stall still shows a pulse
        beat = (math.sin((time.perf_counter() - self._t0) * 2.2) + 1.0) / 2.0
        core = QColor(T.ACCENT)
        core.setAlpha(int(70 + 110 * beat))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(core)
        radius = r2 * (0.26 + 0.06 * beat)
        painter.drawEllipse(QPointF(cx, cy), radius, radius)


class ProgressBar(QWidget):
    """A thin determinate bar that degrades to a shuttle when there is no total.

    Most of a run has a real denominator - images, sweep cells - and shows it.
    Loading the bundle does not: torch.load offers no callback, so any
    percentage there would be invented. That stretch runs the indeterminate
    shuttle instead, which promises nothing.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(6)
        self._done = 0
        self._total = 0
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def set_progress(self, done: int, total: int):
        """total <= 0 switches to the indeterminate shuttle, and starts its timer."""
        self._done, self._total = int(done), int(total)
        if self._total <= 0:
            if not self._timer.isActive():
                self._timer.start(16)
        else:
            self._timer.stop()
        self.update()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._timer.stop()

    def _tick(self):
        self._phase = (self._phase + 0.012) % 1.0
        self.update()

    def paintEvent(self, event):
        """Track, then either the filled fraction or the sweeping segment."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        width, height = float(self.width()), float(self.height())
        radius = height / 2.0

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(T.TRACK))
        painter.drawRoundedRect(QRectF(0, 0, width, height), radius, radius)
        painter.setBrush(QColor(T.ACCENT))

        if self._total > 0:
            frac = max(0.0, min(1.0, self._done / self._total))
            if frac > 0:
                # max(height, ...): below one bar-height a rounded rect collapses
                # into a sliver, so tiny progress would look like none
                painter.drawRoundedRect(
                    QRectF(0, 0, max(height, width * frac), height),
                    radius, radius)
            return

        # indeterminate: one segment sweeping the track
        seg = width * 0.30
        travel = (width + seg) * self._phase - seg
        x0, x1 = max(0.0, travel), min(width, travel + seg)
        if x1 > x0:
            painter.drawRoundedRect(QRectF(x0, 0, x1 - x0, height),
                                    radius, radius)


class PhaseRow(QWidget):
    """One step in the list: a state glyph, its name, and how long it took."""

    GLYPH = {PENDING: "○", ACTIVE: "●", DONE: "✓"}

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.title = title
        self.state = PENDING
        self._started = None
        self.elapsed = 0.0

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        self.glyph = QLabel(self.GLYPH[PENDING])
        self.glyph.setFixedWidth(14)
        self.glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.name = QLabel(title)
        self.time = QLabel("")
        self.time.setAlignment(Qt.AlignmentFlag.AlignRight
                               | Qt.AlignmentFlag.AlignVCenter)

        lay.addWidget(self.glyph)
        lay.addWidget(self.name, 1)
        lay.addWidget(self.time)
        self._paint()

    def set_state(self, state: int):
        """Move this step between pending / active / done, timing the active run.

        Returns early when the state is unchanged, so set_phase() can be called
        on every progress report without restarting the clock.
        """
        if state == self.state:
            return
        if state == ACTIVE:
            self._started = time.perf_counter()
        elif state == DONE and self._started is not None:
            self.elapsed = time.perf_counter() - self._started
            self.time.setText(fmt_duration(self.elapsed))
        self.state = state
        self._paint()

    def _paint(self):
        glyph_color = {PENDING: T.TEXT_MUTED, ACTIVE: T.ACCENT_TEXT,
                       DONE: T.GOOD}[self.state]
        text_color = {PENDING: T.TEXT_MUTED, ACTIVE: T.TEXT,
                      DONE: T.TEXT_DIM}[self.state]
        weight = 600 if self.state == ACTIVE else 500
        self.glyph.setText(self.GLYPH[self.state])
        self.glyph.setStyleSheet(
            f"color: {glyph_color}; font-size: {C.FS_BODY}px;"
            " background: transparent;")
        self.name.setStyleSheet(
            f"color: {text_color}; font-size: {C.FS_BODY}px;"
            f" font-weight: {weight}; background: transparent;")
        self.time.setStyleSheet(
            f"color: {T.TEXT_MUTED}; font-size: {C.FS_SMALL}px;"
            f" font-family: {T.MONO_STACK}; background: transparent;")


class LoadingOverlay(QWidget):
    """Full-window scrim with the working card centred on it.

    A child of the window rather than a modal dialog: a dialog brings its own
    event loop and its own close semantics, and this must not be dismissable -
    the run underneath cannot be abandoned by clicking away from it.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: dict = {}      # phase key -> PhaseRow
        self._order: list = []     # phase keys, in the order they will run
        self._active = None
        self._t0 = 0.0             # when begin() was called, for the footer clock

        self.card = QWidget(self)
        self.card.setObjectName("loadcard")
        self.card.setStyleSheet(
            f"#loadcard {{ background-color: {T.SURFACE};"
            f" border: 1px solid {T.BORDER_STRONG};"
            f" border-radius: {T.R_CARD + 4}px; }}")
        shadow = QGraphicsDropShadowEffect(self.card)
        shadow.setBlurRadius(48)
        shadow.setOffset(0, 12)
        shadow.setColor(QColor(0, 0, 0, 180))
        self.card.setGraphicsEffect(shadow)

        outer = QVBoxLayout(self.card)
        outer.setContentsMargins(30, 28, 30, 26)
        outer.setSpacing(0)

        self.spinner = Spinner(76, self.card)
        spin_row = QHBoxLayout()
        spin_row.addStretch(1)
        spin_row.addWidget(self.spinner)
        spin_row.addStretch(1)
        outer.addLayout(spin_row)
        outer.addSpacing(18)

        self.title = QLabel("Working")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title.setStyleSheet(
            f"color: {T.TEXT}; font-size: {C.FS_TITLE + 3}px; font-weight: 700;"
            " letter-spacing: -0.3px; background: transparent;")
        outer.addWidget(self.title)
        outer.addSpacing(6)

        self.detail = QLabel("")
        self.detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail.setWordWrap(True)
        self.detail.setMinimumHeight(30)
        self.detail.setStyleSheet(
            f"color: {T.TEXT_DIM}; font-size: {C.FS_SMALL}px;"
            f" font-family: {T.MONO_STACK}; background: transparent;")
        outer.addWidget(self.detail)
        outer.addSpacing(16)

        self.bar = ProgressBar(self.card)
        outer.addWidget(self.bar)
        outer.addSpacing(7)

        self.counter = QLabel("")
        self.counter.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.counter.setStyleSheet(
            f"color: {T.TEXT_FAINT}; font-size: {C.FS_MICRO}px;"
            f" font-family: {T.MONO_STACK}; background: transparent;")
        outer.addWidget(self.counter)
        outer.addSpacing(18)

        rule = QWidget(self.card)
        rule.setFixedHeight(1)
        rule.setStyleSheet(f"background-color: {T.BORDER};")
        outer.addWidget(rule)
        outer.addSpacing(14)

        self.phase_box = QVBoxLayout()
        self.phase_box.setSpacing(9)
        outer.addLayout(self.phase_box)
        outer.addSpacing(16)

        self.footer = QLabel("")
        self.footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.footer.setStyleSheet(
            f"color: {T.TEXT_MUTED}; font-size: {C.FS_MICRO}px;"
            " background: transparent;")
        outer.addWidget(self.footer)

        self.card.setFixedWidth(430)
        self.hide()

        self._clock = QTimer(self)
        self._clock.timeout.connect(self._tick_clock)

    # -- geometry ----------------------------------------------------------
    def paintEvent(self, event):
        """The scrim. Alpha rather than opaque, so the window stays recognisable."""
        QPainter(self).fillRect(self.rect(), QColor(10, 11, 13, 205))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._centre()

    def _centre(self):
        """Re-centre the card. Called on resize and whenever its height changes -
        the detail line wraps, so the card grows and shrinks during a run."""
        width = self.card.width()
        height = self.card.sizeHint().height()
        self.card.setGeometry((self.width() - width) // 2,
                              (self.height() - height) // 2, width, height)

    # -- api ---------------------------------------------------------------
    def begin(self, phases: list, title: str = "Working"):
        """phases: [(key, label)] in the order they will run."""
        while self.phase_box.count():
            item = self.phase_box.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self._rows.clear()
        self._order = []

        for key, label in phases:
            row = PhaseRow(label, self.card)
            self.phase_box.addWidget(row)
            self._rows[key] = row
            self._order.append(key)

        self._active = None
        self._t0 = time.perf_counter()
        self.title.setText(title)
        self.detail.setText("")
        self.counter.setText("")
        self.bar.set_progress(0, 0)
        self.footer.setText(
            "detailed progress is also printing to the terminal")

        self.show()
        self.raise_()
        self._centre()
        self._clock.start(200)

    def set_phase(self, key: str, detail: str = ""):
        """Mark key active and everything before it done. Idempotent."""
        if key not in self._rows:
            return
        index = self._order.index(key)
        for i, other in enumerate(self._order):
            self._rows[other].set_state(
                DONE if i < index else ACTIVE if i == index else PENDING)
        if key != self._active:
            self._active = key
            self.title.setText(self._rows[key].title)
            self.bar.set_progress(0, 0)
            self.counter.setText("")
        if detail:
            self.detail.setText(detail)
        self._centre()

    def set_detail(self, detail: str):
        self.detail.setText(detail)
        self._centre()

    def set_progress(self, done: int, total: int, note: str = ""):
        """Update the bar and its counter. total <= 0 shows only the note."""
        self.bar.set_progress(done, total)
        if total > 0:
            text = f"{done:,} / {total:,}   ·   {100.0 * done / total:.0f}%"
            if note:
                text += f"   ·   {note}"
            self.counter.setText(text)
        else:
            self.counter.setText(note)

    def finish(self):
        """Mark everything done, stop the clock, hide. Safe if never shown."""
        for key in self._order:
            self._rows[key].set_state(DONE)
        self._clock.stop()
        self.hide()

    def _tick_clock(self):
        self.footer.setText(
            f"{fmt_duration(time.perf_counter() - self._t0)} elapsed"
            "   ·   detailed progress is also printing to the terminal")
