"""map_area.py - the map view with native Qt widgets floating over it (legend, banner, verdict card).

The Leaflet view fills the area. Overlays are child widgets kept in a corner when the window is
resized. A Qt widget drawn over the web view renders correctly (checked on 2026-10-15).
"""

from PyQt5.QtWidgets import QVBoxLayout, QWidget


def overlay_position(corner, margin, area_size, widget_size):
    """Top-left (x, y) for a widget of widget_size in a corner of the area ('top-left',
    'top-center', 'top-right', 'bottom-left', 'bottom-right'); margin = (x, y) from the edges."""
    (area_w, area_h), (w, h), (mx, my) = area_size, widget_size, margin
    vertical, horizontal = corner.split("-")
    x = {"left": mx, "center": (area_w - w) // 2, "right": area_w - w - mx}[horizontal]
    y = my if vertical == "top" else area_h - h - my
    return max(x, 0), max(y, 0)


class MapArea(QWidget):
    def __init__(self, view, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(view)
        self._overlays = []

    def add_overlay(self, widget, corner, margin=(12, 12)):
        """Float a Qt widget over the map, in a corner."""
        widget.setParent(self)
        self._overlays.append((widget, corner, margin))
        self.relayout()

    def relayout(self):
        """Fit every overlay to its contents and put it back in its corner (call after changes)."""
        for widget, corner, margin in self._overlays:
            widget.adjustSize()
            x, y = overlay_position(corner, margin, (self.width(), self.height()),
                                    (widget.width(), widget.height()))
            widget.move(x, y)
            widget.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.relayout()
