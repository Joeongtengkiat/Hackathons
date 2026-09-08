"""Small reusable UI pieces.

Everything here is presentation only - no widget in this module knows about a
dataset, a score or a threshold. That is what lets the pages compose them freely
and what keeps the type scale and the card treatment consistent across screens
that were written at different times.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget
)

from .. import theme as T


#: One type scale, used everywhere - no arbitrary sizes. Five steps is enough
#: to build a hierarchy and few enough that two labels of the same rank can
#: never end up a pixel apart.
FS_MICRO, FS_SMALL, FS_BODY, FS_TITLE, FS_VALUE = 10, 11, 13, 15, 28


class Card(QFrame):
    """The container surface, one step above the page ground.

    Both cues stay on by default - the lighter fill and the hairline. A Qt
    stylesheet has no shadows, and on a dark ground a fill difference this
    small is not enough on its own to say where a panel ends.
    """

    def __init__(self, parent=None, padding: int = 14, bordered: bool = True):
        super().__init__(parent)
        self.setObjectName("card")
        border = f"1px solid {T.BORDER}" if bordered else "none"
        self.setStyleSheet(
            f"#card {{ background-color: {T.CARD}; border: {border};"
            f" border-radius: {T.R_CARD}px; }}"
        )
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(padding, padding, padding, padding)
        self._layout.setSpacing(8)

    def layout(self) -> QVBoxLayout:  # type: ignore[override]
        """The card's own layout - add content to this, not to the card."""
        return self._layout


class StatCard(Card):
    """Big-number metric tile: label, value, one short sub-line.

    The label is sentence case at reading size. Small caps with tracking is the
    reflex for a tile like this, and it costs legibility at 10px for nothing.
    """

    def __init__(self, title: str, parent=None):
        super().__init__(parent, padding=16, bordered=True)
        self.setMinimumWidth(130)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.title_label = QLabel(title)
        self.title_label.setStyleSheet(
            f"color: {T.TEXT_FAINT}; font-size: {FS_SMALL}px; font-weight: 500;"
            " background: transparent;"
        )

        self.value_label = QLabel("—")
        self._paint_value(T.TEXT)

        self.sub_label = QLabel("")
        self.sub_label.setStyleSheet(
            f"color: {T.TEXT_FAINT}; font-size: {FS_SMALL}px; background: transparent;"
        )

        self._layout.setSpacing(3)
        self._layout.addWidget(self.title_label)
        self._layout.addWidget(self.value_label)
        self._layout.addWidget(self.sub_label)

    def _paint_value(self, color: str):
        self.value_label.setStyleSheet(
            f"color: {color}; font-size: {FS_VALUE}px; font-weight: 600;"
            " letter-spacing: -0.4px; background: transparent;"
        )

    def set_value(self, text: str, sub: str = None, color: str = None):
        """Update the tile. `sub=None` leaves the sub-line as it was.

        `color` is how a tile flags a bad number (an FPR over 10%); the default
        is the normal text colour, so a tile never stays red by accident.
        """
        self.value_label.setText(text)
        self._paint_value(color or T.TEXT)
        if sub is not None:
            self.sub_label.setText(sub)

    def set_title(self, text: str):
        self.title_label.setText(text)


class Badge(QLabel):
    """A pill with a border - a short status word, not a control."""

    def __init__(self, text: str = "", color: str = T.TEXT_DIM, parent=None):
        super().__init__(text, parent)
        self.set_color(color)

    def set_color(self, color: str):
        self.setStyleSheet(
            f"color: {color}; background-color: {T.RAISED};"
            f" border: 1px solid {T.BORDER_STRONG}; border-radius: 10px;"
            " padding: 3px 10px; font-size: 11px; font-weight: 600;"
        )


class Dot(QLabel):
    """6px status dot - says what a sentence of label text used to say."""

    def __init__(self, color: str = T.TEXT_FAINT, parent=None):
        super().__init__("", parent)
        self.setFixedSize(8, 8)
        self.set_color(color)

    def set_color(self, color: str):
        self.setStyleSheet(f"background-color: {color}; border-radius: 4px;")


class Chip(QPushButton):
    """A checkable filter pill. The pages own the mutual exclusion themselves,
    since the same chip row also has to be settable from code."""

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setCheckable(True)
        self.setProperty("chip", True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)


class Tab(QPushButton):
    """Header tab - an underline and a weight change, no chrome of its own."""

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setCheckable(True)
        self.setProperty("tab", True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)


class SectionTitle(QLabel):
    """Rule-in for a group of controls - quieter than a heading.

    Sentence case, reading size. The controls underneath carry the weight; the
    label only has to say what they are.
    """

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setStyleSheet(
            f"color: {T.TEXT_DIM}; font-size: {FS_SMALL}px; font-weight: 600;"
            " background: transparent;"
        )


class Hint(QLabel):
    """Quiet explanatory line under a control. Wraps rather than elides."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setWordWrap(True)
        self.setStyleSheet(
            f"color: {T.TEXT_DIM}; font-size: {FS_SMALL}px; background: transparent;"
        )


class EmptyState(QWidget):
    """Centred message for panels with nothing to show yet."""

    def __init__(self, title: str, hint: str = "", parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setSpacing(6)
        self.title_label = QLabel(title)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_label.setStyleSheet(
            f"color: {T.TEXT_DIM}; font-size: 15px; font-weight: 600; background: transparent;"
        )
        self.hint_label = QLabel(hint)
        self.hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint_label.setWordWrap(True)
        self.hint_label.setStyleSheet(
            f"color: {T.TEXT_FAINT}; font-size: 12px; background: transparent;"
        )
        lay.addWidget(self.title_label)
        lay.addWidget(self.hint_label)

    def set_text(self, title: str, hint: str = ""):
        self.title_label.setText(title)
        self.hint_label.setText(hint)


def score_color(score: float) -> str:
    """Teal (authentic) -> amber -> red (AI).

    Fixed bands, deliberately not the live threshold: this colours the *score*,
    and a hue that moved as the slider moved would make two images with the same
    number look different. The threshold is shown by the verdict text next to it.
    """
    if score is None or score != score:
        return T.TEXT_FAINT
    if score < 0.35:
        return T.REAL_COLOR
    if score < 0.65:
        return T.WARN
    return T.AI_COLOR
