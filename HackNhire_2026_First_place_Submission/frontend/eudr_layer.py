"""eudr_layer.py - the colour-coded EUDR map: loads a stored map from Neon, then controls the layer.

NeonService.open_map(name) reads the map's grid_info and every stencil page (one query, about
1.3 MB for a 6,000 x 6,000 map) and unpacks the whole map in memory; tiles.py draws the tiles from
that, with no database work per tile. Which map to load is decided by maps_ui.MapsController.
The legend and the loading/error banner are Qt widgets floating over the map. The toolbar
gets a Layers menu and an opacity slider; the status bar says whether Neon is connected.
A map stored with protected areas (backend protected_areas.py: wdpa*.geojson files given at ingest)
gets them drawn over it as a dashed outline, with a row in the legend and in the Layers menu.
"""

from functools import partial

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import (QAction, QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QMenu,
                             QPushButton, QSlider, QToolButton)

from legend import PROTECTED_COLOUR, PROTECTED_LABEL


def share_text(pct):
    return f"{pct:.0f}%" if pct >= 10 else (f"{pct:.1f}%" if pct >= 0.1 else "<0.1%")


def colour_icon(colour, size=12):
    pix = QPixmap(size, size)
    pix.fill(QColor(colour))
    return QIcon(pix)


def outline_icon(colour, size=12):
    """A see-through square with a coloured border: the protected areas' key."""
    pix = QPixmap(size, size)
    fill = QColor(colour)
    fill.setAlpha(60)
    pix.fill(fill)
    painter = QPainter(pix)
    pen = QPen(QColor(colour))
    pen.setWidth(2)
    painter.setPen(pen)
    painter.drawRect(1, 1, size - 2, size - 2)
    painter.end()
    return QIcon(pix)


def label(text, role=None):
    widget = QLabel(text)
    widget.setTextFormat(Qt.PlainText)
    if role:
        widget.setProperty("role", role)
    return widget


class Legend(QFrame):
    """The map's key: each class's colour, name and share of the map. Unticking a class hides it
    on the map; hidden_changed gives the sorted list of hidden class codes. A map with protected
    areas gets one more row; unticking it hides them (protected_toggled)."""

    hidden_changed = pyqtSignal(object)
    protected_toggled = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("overlay")
        self.hidden = set()
        self.checkboxes = []
        self.protected_box = None
        self.protected_shown = True
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(10, 8, 10, 8)
        self.grid.setHorizontalSpacing(12)
        self.grid.setVerticalSpacing(2)
        self.hide()

    def set_info(self, info):
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.checkboxes = []
        self.protected_box = None
        self.grid.addWidget(label(f"EUDR map · {info['map_version']}", "title"), 0, 0, 1, 2)
        for row, c in enumerate(info["classes"], start=1):
            box = QCheckBox(c["label"])
            box.setIcon(colour_icon(c["colour"]))
            box.setChecked(c["code"] not in self.hidden)
            box.toggled.connect(partial(self.toggle, c["code"]))
            share = label(share_text(c["share"]), "secondary")
            share.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.grid.addWidget(box, row, 0)
            self.grid.addWidget(share, row, 1)
            self.checkboxes.append(box)
        row = len(info["classes"]) + 1
        count = info.get("protected_areas") or 0
        if count:
            box = QCheckBox(PROTECTED_LABEL)
            box.setIcon(outline_icon(PROTECTED_COLOUR))
            box.setChecked(self.protected_shown)
            box.setToolTip("Protected areas (WDPA) stored with this map in Neon")
            box.toggled.connect(self.toggle_protected)
            n = label(f"{count:,}", "secondary")
            n.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.grid.addWidget(box, row, 0)
            self.grid.addWidget(n, row, 1)
            self.protected_box = box
            row += 1
        note = label(f"From the stencils in Neon · {info['width']:,} × {info['height']:,} px", "muted")
        self.grid.addWidget(note, row, 0, 1, 2)
        self.show()

    def toggle_protected(self, visible):
        self.protected_shown = visible
        if self.protected_box is not None and self.protected_box.isChecked() != visible:
            self.protected_box.setChecked(visible)
        self.protected_toggled.emit(visible)

    def toggle(self, code, visible):
        if visible:
            self.hidden.discard(code)
        else:
            self.hidden.add(code)
        self.hidden_changed.emit(sorted(self.hidden))


class Banner(QFrame):
    """A message floating over the top of the map, with an optional button (e.g. Retry)."""

    retry = pyqtSignal()  # the button was clicked

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("overlay")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 8, 10, 8)
        self.text = label("")
        self.text.setWordWrap(True)
        self.text.setMaximumWidth(560)
        self.button = QPushButton("Retry")
        self.button.clicked.connect(lambda: self.retry.emit())
        layout.addWidget(self.text)
        layout.addWidget(self.button)
        self.hide()

    def show_message(self, text, error=False, button=None):
        """button: the text of a button to show next to the message (None: no button)."""
        self.setProperty("tone", "error" if error else "info")
        self.style().unpolish(self)
        self.style().polish(self)
        self.text.setText(text)
        self.button.setText(button or "")
        self.button.setVisible(button is not None)
        self.show()


class EudrLayer:
    def __init__(self, window):
        self.w = window
        self.info = None
        self.name = None                        # the map shown, or being loaded
        self.banner_action = lambda: None       # what the banner's button does now

        self.neon_label = QLabel("Neon: connecting…")
        window.statusBar().addPermanentWidget(self.neon_label)

        self.legend = Legend()
        self.legend.hidden_changed.connect(lambda codes: window.map.call("setHiddenClasses", codes))
        self.legend.protected_toggled.connect(self.set_protected_visible)
        window.map_area.add_overlay(self.legend, "bottom-left", (12, 34))  # above Leaflet's scale bar
        self.banner = Banner()
        self.banner.retry.connect(lambda: self.banner_action())
        window.map_area.add_overlay(self.banner, "top-center", (0, 12))

        self.layer_act = QAction("EUDR map (from Neon)", window, checkable=True, checked=True)
        self.layer_act.toggled.connect(self.set_visible)
        self.layers_menu = QMenu(window)
        self.layers_menu.addAction(self.layer_act)
        self.protected_act = QAction(PROTECTED_LABEL + " (WDPA)", window, checkable=True, checked=True)
        self.protected_act.setEnabled(False)  # until a map with protected areas is shown
        self.protected_act.toggled.connect(self.set_protected_visible)
        self.layers_menu.addAction(self.protected_act)
        layers_btn = QToolButton()
        layers_btn.setText("Layers")
        layers_btn.setIcon(window.icon("layers"))
        layers_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        layers_btn.setMenu(self.layers_menu)
        layers_btn.setPopupMode(QToolButton.InstantPopup)

        self.opacity = QSlider(Qt.Horizontal)
        self.opacity.setRange(0, 100)
        self.opacity.setValue(70)
        self.opacity.setFixedWidth(110)
        self.opacity.setToolTip("Opacity of the EUDR map")
        self.opacity.valueChanged.connect(lambda v: window.map.call("setOverlayOpacity", v / 100))

        window.insert_tools(window.basemap_anchor, [layers_btn, QLabel("Opacity"), self.opacity, None])
        # Which map to load is decided by maps_ui.MapsController (it lists the maps first).

    def set_visible(self, on):
        self.w.map.call("setOverlayVisible", on)
        self.legend.setVisible(on and self.info is not None)

    def set_protected_visible(self, on):
        """Show or hide the protected areas (from the Layers menu or the legend; both follow)."""
        if self.protected_act.isChecked() != on:
            self.protected_act.setChecked(on)
        if self.legend.protected_shown != on:
            self.legend.toggle_protected(on)
        self.w.map.call("setProtectedVisible", on)

    def show_protected(self, fc):
        """Draw the map's protected areas (None: it has none)."""
        self.protected_act.setEnabled(bool(fc and fc.get("features")))
        self.w.map.call("setProtectedAreas", fc)

    def show_banner(self, text, error=False, button=None, action=None):
        """A message over the map. With an action, a button runs it ('Retry' unless named)."""
        self.banner_action = action or (lambda: None)
        if action is not None and button is None:
            button = "Retry"
        self.banner.show_message(text, error=error, button=button if action is not None else None)
        self.w.map_area.relayout()

    def load(self, name):
        """Load a stored map from Neon (on the worker thread), then show it."""
        self.name = name
        self.neon_label.setText("Neon: connecting…")
        self.show_banner(f"Loading the map '{name}' from Neon…")
        self.w.run_job("open_map", "open_map", name, done=self.loaded,
                       failed=lambda message: self.failed(name, message), coalesce=True)

    def loaded(self, info):
        if info["map_version"] != self.name:  # a slower load of a map chosen earlier: ignore it
            return
        service = self.w.worker.service
        self.w.tile_handler.set_map(service.grid, service.classes, service.to_map)
        self.info = info
        self.w.map.call("setMapInfo", info)
        self.show_protected(service.protected)
        self.legend.set_info(info)
        self.banner.hide()
        self.w.map_area.relayout()
        self.neon_label.setText(f"Neon: connected · {info['map_version']} · "
                                f"{info['width']:,} × {info['height']:,} px")
        self.w.map_info = info
        self.w.emit("map_loaded", info)

    def renamed(self, info):
        """The map shown got a new name (and schema): same pixels, so it is shown again without reading."""
        self.name = info["map_version"]
        self.loaded(info)

    def failed(self, name, message):
        if name != self.name:  # an earlier choice failing late: the map chosen since is loading
            return True
        self.neon_label.setText("Neon: map not loaded")
        self.show_banner(f"Could not load the map '{name}' from Neon. {message}", error=True,
                         action=lambda: self.load(name))
        return True  # shown over the map, with a Retry button

    def clear(self, text, button=None, action=None):
        """Nothing to show: no maps stored yet, or the map shown was deleted."""
        self.info = self.name = None
        self.w.map_info = None
        self.w.tile_handler.set_map(None, None)
        self.w.map.call("clearMapInfo")
        self.protected_act.setEnabled(False)
        self.legend.hide()
        self.neon_label.setText("Neon: connected · no map shown")
        self.show_banner(text, button=button, action=action)
