"""EUDR farm checker - the main window.

Draw or load farms on the EUDR map stored in Neon, and check them with both storage approaches.
Everything is a Qt widget except the map, which is a Leaflet view (pages.py). Anything that
needs the database runs on one background thread (worker.py -> neon_service.py). Each feature lives in its own module and is created at
the end of MainWindow.__init__.

    frontend\\.venv\\Scripts\\python frontend\\app.py [--screenshot out.png --after 20]
"""

import argparse
import json
import sys
from collections import defaultdict

from PyQt5.QtCore import QByteArray, QSize, Qt, QTimer
from PyQt5.QtGui import QColor, QFont, QGuiApplication, QIcon, QKeySequence, QPainter, QPixmap
from PyQt5.QtSvg import QSvgRenderer
from PyQt5.QtWebEngineWidgets import QWebEngineProfile
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

import legend
import tiles
from checks_ui import CheckController
from compare_tab import CompareTab
from eudr_layer import EudrLayer
from map_area import MapArea
from maps_ui import MapsController
from results_panel import ResultsPanel
from neon_service import NeonService
from pages import WebPage, load_qwebchannel_js
from worker import Worker

# Shown until the extent of the map stored in Neon arrives: top-left and bottom-right (lat, lon)
TOP_LEFT = (-1.8, 103.0)
BOTTOM_RIGHT = (-2.3, 104.0)

# Farm colours before a check, cycled by farm id (shared by the map and the toolbar)
COLORS = ["#2563eb", "#16a34a", "#d97706", "#9333ea", "#dc2626", "#0891b2", "#db2777", "#65a30d"]

# Toolbar icons (24x24 stroke paths)
ICONS = {
    "draw": "M12 3l8.5 6.2-3.2 10.3H6.7L3.5 9.2z",
    "point": "M12 8a4 4 0 100 8 4 4 0 000-8z",
    "edit": "M4 20h4L19 9l-4-4L4 16zM13.5 6.5l4 4",
    "undo": "M9 14L4 9l5-5M4 9h11a5 5 0 010 10h-3",
    "check": "M5 12l5 5L20 7",
    "close": "M6 6l12 12M18 6L6 18",
    "export": "M12 4v11M7 10l5 5 5-5M5 20h14",
    "more": "M5 12h.01M12 12h.01M19 12h.01",
    "open": "M3 7h6l2 2h10v10H3z",
    "verify": "M12 3l7 3v5c0 5-3.5 8.5-7 10-3.5-1.5-7-5-7-10V6zM9 12l2 2 4-4",
    "inspect": "M10.5 4a6.5 6.5 0 100 13 6.5 6.5 0 000-13zM20 20l-4.8-4.8",
    "layers": "M12 3l9 5-9 5-9-5zM3 13l9 5 9-5",
}

STYLESHEET = """
QToolBar#main {
    background: #ffffff; border: none; border-bottom: 1px solid #e5e7eb;
    padding: 4px 8px; spacing: 4px;
}
QToolBar#main QToolButton {
    padding: 5px 9px; border: 1px solid transparent; border-radius: 6px; color: #1f2937;
}
QToolBar#main QToolButton:hover { background: #f3f4f6; border-color: #e5e7eb; }
QToolBar#main QToolButton:pressed, QToolBar#main QToolButton:checked { background: #e0e7ff; }
QToolBar#main QToolButton:disabled { color: #9ca3af; }
QToolBar#main QToolButton::menu-indicator { image: none; }
QToolBar#main QLabel { color: #6b7280; padding: 0 2px 0 8px; }
QComboBox {
    padding: 4px 8px; border: 1px solid #d1d5db; border-radius: 6px; background: #fff; min-height: 22px;
}
QComboBox:hover { border-color: #9ca3af; }
QComboBox:disabled { color: #9ca3af; background: #f9fafb; }
QComboBox QAbstractItemView { border: 1px solid #d1d5db; selection-background-color: #e0e7ff; selection-color: #111827; }
QMenu { background: #fff; border: 1px solid #d1d5db; padding: 4px; }
QMenu::item { padding: 6px 24px 6px 12px; border-radius: 4px; }
QMenu::item:selected { background: #f3f4f6; }
QMenu::item:disabled { color: #9ca3af; }
QMenu::separator { height: 1px; background: #e5e7eb; margin: 4px 6px; }
QStatusBar { background: #f9fafb; border-top: 1px solid #e5e7eb; color: #374151; }
QStatusBar QLabel { padding: 2px 8px; }
QTabWidget::pane { border: none; }
QTabBar::tab { padding: 6px 18px; border: none; border-bottom: 2px solid transparent; color: #52514e; background: #ffffff; }
QTabBar::tab:selected { color: #0b0b0b; border-bottom-color: #2563eb; }
QSlider::groove:horizontal { height: 4px; background: #e5e7eb; border-radius: 2px; }
QSlider::handle:horizontal { width: 14px; margin: -6px 0; border-radius: 7px; background: #2563eb; }
QSplitter::handle { background: #e5e7eb; }
QFrame#overlay { background: #fcfcfb; border: 1px solid #d9d8d2; border-radius: 8px; }
QFrame#overlay[tone="error"] { border-left: 4px solid #d03b3b; }
QFrame#overlay QLabel, QFrame#overlay QCheckBox { background: transparent; border: none; color: #0b0b0b; }
QLabel[role="title"] { font-weight: 600; }
QLabel[role="secondary"] { color: #52514e; }
QLabel[role="muted"] { color: #898781; }
QLabel[role="heading"] { font-size: 15px; font-weight: 600; color: #0b0b0b; }
QLabel[role="error"] { color: #b42318; }
QPushButton[role="danger"]:enabled { color: #b42318; font-weight: 600; }
QWidget#panel { background: #f7f7f5; }
QFrame#card { background: #fcfcfb; border: 1px solid #e5e4df; border-radius: 8px; }
QFrame#card QLabel { background: transparent; border: none; }
QFrame#card QPushButton { color: #2563eb; border: none; padding: 2px 4px; }
QFrame#card QPushButton:hover { text-decoration: underline; }
QTableWidget { background: #ffffff; border: 1px solid #e5e4df; border-radius: 6px; gridline-color: #f0efeb; selection-background-color: #e0e7ff; selection-color: #0b0b0b; }
QHeaderView::section { background: #f7f7f5; border: none; border-bottom: 1px solid #e5e4df; padding: 4px 6px; color: #52514e; }
"""


def svg_icon(name, color="#374151"):
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
        f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="{ICONS[name]}"/></svg>'
    )
    pix = QPixmap(64, 64)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    QSvgRenderer(QByteArray(svg.encode())).render(painter)
    painter.end()
    return QIcon(pix)


def swatch(shape_id):
    pix = QPixmap(32, 32)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor(COLORS[(shape_id - 1) % len(COLORS)]))
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(4, 4, 24, 24, 6, 6)
    painter.end()
    return QIcon(pix)


def fmt_area(km2):
    return f"{km2:.2f} km²" if km2 >= 1 or km2 == 0 else f"{km2 * 100:.1f} ha"


def fmt_size(props):
    return "point" if props.get("kind") == "point" else fmt_area(props["area_km2"])


class GeoJsonDialog(QDialog):
    def __init__(self, text, parent):
        super().__init__(parent)
        self.setWindowTitle("GeoJSON")
        self.resize(560, 640)
        view = QPlainTextEdit(text)
        view.setReadOnly(True)
        view.setFont(QFont("Consolas", 10))
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(view)
        layout.addWidget(buttons)


class MainWindow(QMainWindow):
    def __init__(self, tile_handler, map_name=None):
        super().__init__()
        self.setWindowTitle("EUDR farm checker")
        self.resize(1400, 850)
        self.geojson = {"type": "FeatureCollection", "features": []}
        self.state = {"mode": "idle", "points": 0, "area_km2": 0, "selected": None}
        self.tile_handler = tile_handler
        self.map_info = None
        self._jobs = {}
        self._listeners = defaultdict(list)

        # Every page gets the same config: legend colours, verdict words, farm colours
        self.qwebchannel_js = load_qwebchannel_js()
        self.page_config = dict(legend.page_config(), shapeColours=COLORS,
                                initialBounds=[list(TOP_LEFT), list(BOTTOM_RIGHT)])
        self.map = WebPage("map", "map.html", self.page_config, self.qwebchannel_js, self)
        self.view, self.bridge = self.map.view, self.map.bridge
        self.bridge.geojson_changed.connect(self.on_geojson_changed)
        self.bridge.state_changed.connect(self.on_state_changed)

        # Map tab: the map (with Qt widgets floating over it) and, from Task 10, the right-hand panel
        self.map_area = MapArea(self.view)
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self.map_area)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self.splitter, "Map")
        self.setCentralWidget(self.tabs)

        # One background thread for everything that touches Neon
        self.worker = Worker(lambda status: NeonService(on_status=status))
        self.worker.done.connect(self._job_done)
        self.worker.failed.connect(self._job_failed)
        self.worker.progress.connect(self._job_progress)
        self.worker.status.connect(lambda text: self.statusBar().showMessage(text, 6000))
        self.worker.start()

        self.build_actions()
        self.build_toolbar()

        self.hint = QLabel()
        self.summary = QLabel()
        self.busy = QLabel()
        self.statusBar().addWidget(self.hint, 1)
        self.statusBar().addPermanentWidget(self.busy)
        self.statusBar().addPermanentWidget(self.summary)
        self.statusBar().setSizeGripEnabled(False)

        # ----- features: each module adds its own actions, pages and listeners -----
        self.eudr = EudrLayer(self)
        self.checks = CheckController(self)
        self.results = ResultsPanel(self)
        self.splitter.addWidget(self.results)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)
        self.compare = CompareTab(self)
        self.tabs.addTab(self.compare, "Compare")
        self.maps = MapsController(self, preferred=map_name)  # lists the maps in Neon, then loads one

        self.refresh()

    # ----- events between features -----
    def listen(self, event, callback):
        self._listeners[event].append(callback)

    def emit(self, event, *args):
        for callback in list(self._listeners[event]):
            callback(*args)

    # ----- background jobs -----
    def run_job(self, kind, method, *args, done=None, failed=None, progress=None, coalesce=False, **kwargs):
        """Run NeonService.<method>(*args, **kwargs) on the worker thread. done(result),
        failed(message) and progress(done, total, text) run on this thread; failed returning True
        means the feature showed the error itself (otherwise a dialog shows it)."""
        job_id = self.worker.submit(kind, method, args, kwargs, progress=progress is not None,
                                    coalesce=coalesce)
        self._jobs[job_id] = (done, failed, progress)
        self._update_busy()
        return job_id

    def _finish(self, job_id):
        callbacks = self._jobs.pop(job_id, (None, None, None))
        self._update_busy()
        return callbacks

    def _job_done(self, job_id, kind, result):
        done, _, _ = self._finish(job_id)
        if done:
            done(result)

    def _job_failed(self, job_id, kind, message):
        _, failed, _ = self._finish(job_id)
        if not message:  # replaced by a newer request of the same kind
            return
        if failed and failed(message):
            return
        self.statusBar().showMessage(message.splitlines()[0], 8000)
        QMessageBox.warning(self, "Couldn't finish that", message)

    def _job_progress(self, job_id, kind, done, total, text):
        entry = self._jobs.get(job_id)
        if entry and entry[2]:
            entry[2](done, total, text)

    def _update_busy(self):
        self.busy.setText("Working…" if self._jobs else "")

    # ----- UI construction -----
    def icon(self, name):
        return svg_icon(name)

    def action(self, text, icon=None, shortcuts=(), slot=None):
        act = QAction(text, self)
        if icon:
            act.setIcon(svg_icon(icon))
        if shortcuts:
            act.setShortcuts([QKeySequence(s) for s in shortcuts])
            act.setToolTip(f"{text}  ({shortcuts[0]})")
        if slot:
            act.triggered.connect(slot)
        self.addAction(act)
        return act

    def build_actions(self):
        self.draw_act = self.action("Draw", "draw", ["P"], lambda: self.js("startDraw()"))
        self.edit_act = self.action("Edit", "edit", ["E"], lambda: self.js("startEdit()"))
        self.draw_act.setCheckable(True)
        self.edit_act.setCheckable(True)

        self.undo_act = self.action("Undo point", "undo", ["Ctrl+Z", "Backspace"], lambda: self.js("undoPoint()"))
        self.finish_act = self.action("Finish", "check", ["Return", "Enter"], lambda: self.js("confirmAction()"))
        self.cancel_act = self.action("Cancel", "close", slot=lambda: self.js("cancelAction()"))
        self.cancel_act.setToolTip("Cancel  (Esc)")
        # Esc lives on its own action: hidden actions (like Cancel when idle) lose their shortcuts
        self.action("Escape", shortcuts=["Esc"], slot=lambda: self.js("cancelAction()"))

        self.zoom_act = self.action("Zoom to farm", slot=lambda: self.js(f"zoomToShape({self.selected_id()})"))
        self.rename_act = self.action("Rename…", shortcuts=["F2"], slot=self.rename_selected)
        self.delete_act = self.action("Delete", shortcuts=["Del"], slot=self.delete_selected)

        self.save_act = self.action("Save GeoJSON…", shortcuts=["Ctrl+S"], slot=self.save)
        self.copy_act = self.action("Copy GeoJSON", shortcuts=["Ctrl+Shift+C"], slot=self.copy)
        self.view_act = self.action("View GeoJSON…", slot=self.show_geojson)
        self.clear_act = self.action("Clear all…", slot=self.clear_all)

    def build_toolbar(self):
        tb = self.toolbar = QToolBar("Main")
        tb.setObjectName("main")
        tb.setMovable(False)
        tb.setIconSize(QSize(18, 18))
        tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        tb.setContextMenuPolicy(Qt.PreventContextMenu)
        self.addToolBar(Qt.TopToolBarArea, tb)

        tb.addAction(self.draw_act)
        tb.addAction(self.edit_act)
        tb.addSeparator()
        # Drawing/editing controls; only shown while active
        tb.addAction(self.undo_act)
        tb.addAction(self.finish_act)
        tb.addAction(self.cancel_act)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.spacer_anchor = tb.addWidget(spacer)  # features put their buttons before this

        tb.addWidget(QLabel("Farm"))
        self.shape_combo = QComboBox()
        self.shape_combo.setMinimumWidth(240)
        self.shape_combo.currentIndexChanged.connect(self.on_combo_selection)
        tb.addWidget(self.shape_combo)

        shape_menu = QMenu(self)
        shape_menu.addAction(self.zoom_act)
        shape_menu.addAction(self.rename_act)
        shape_menu.addSeparator()
        shape_menu.addAction(self.delete_act)
        self.shape_btn = QToolButton()
        self.shape_btn.setIcon(svg_icon("more"))
        self.shape_btn.setToolTip("Farm actions")
        self.shape_btn.setMenu(shape_menu)
        self.shape_btn.setPopupMode(QToolButton.InstantPopup)
        tb.addWidget(self.shape_btn)

        tb.addSeparator()
        self.basemap_anchor = tb.addWidget(QLabel("Basemap"))  # layer controls go before this
        basemap = QComboBox()
        basemap.addItem("Satellite", "satellite")
        basemap.addItem("Streets", "streets")
        basemap.currentIndexChanged.connect(lambda i: self.js(f"setBasemap('{basemap.itemData(i)}')"))
        tb.addWidget(basemap)

        tb.addSeparator()
        export_menu = QMenu(self)
        export_menu.addAction(self.save_act)
        export_menu.addAction(self.copy_act)
        export_menu.addAction(self.view_act)
        export_menu.addSeparator()
        export_menu.addAction(self.clear_act)
        export_btn = QToolButton()
        export_btn.setText("Export")
        export_btn.setIcon(svg_icon("export"))
        export_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        export_btn.setMenu(export_menu)
        export_btn.setPopupMode(QToolButton.InstantPopup)
        tb.addWidget(export_btn)

    def insert_tools(self, anchor, items):
        """Put actions, widgets or separators (None) on the toolbar, before the anchor."""
        for item in items:
            if item is None:
                self.toolbar.insertSeparator(anchor)
            elif isinstance(item, QAction):
                self.toolbar.insertAction(anchor, item)
            else:
                self.toolbar.insertWidget(anchor, item)

    # ----- helpers -----
    def js(self, code):
        self.map.page.runJavaScript(code)

    def selected_id(self):
        return self.state["selected"]

    # ----- map -> UI -----
    def on_geojson_changed(self, geojson):
        self.geojson = geojson
        self.refresh()
        self.emit("geojson", geojson)

    def on_state_changed(self, state):
        self.state = state
        self.refresh()

    def refresh(self):
        mode = self.state["mode"]
        features = self.geojson["features"]
        pts = self.state["points"]
        idle = mode == "idle"
        has_selection = self.selected_id() is not None

        self.draw_act.setEnabled(idle)
        self.draw_act.setChecked(mode == "drawing")
        self.edit_act.setEnabled(idle and bool(features))
        self.edit_act.setChecked(mode == "editing")

        self.undo_act.setVisible(mode == "drawing")
        self.undo_act.setEnabled(pts >= 2)
        self.finish_act.setVisible(mode in ("drawing", "editing"))
        self.finish_act.setText("Save edits" if mode == "editing" else "Finish")
        self.finish_act.setEnabled(mode == "editing" or pts >= 3)
        self.cancel_act.setVisible(not idle)
        self.cancel_act.setText("Discard" if mode == "editing" else "Cancel")

        for act in (self.zoom_act, self.rename_act, self.delete_act):
            act.setEnabled(idle and has_selection)
        self.shape_btn.setEnabled(idle and has_selection)
        self.shape_combo.setEnabled(idle and bool(features))
        for act in (self.save_act, self.copy_act, self.view_act, self.clear_act):
            act.setEnabled(bool(features))

        # Farm dropdown
        self.shape_combo.blockSignals(True)
        self.shape_combo.clear()
        self.shape_combo.addItem("No farm selected" if features else "No farms yet", None)
        for f in features:
            p = f["properties"]
            self.shape_combo.addItem(swatch(p["id"]), f"{p['name']}  ·  {fmt_size(p)}", p["id"])
            if p["id"] == self.selected_id():
                self.shape_combo.setCurrentIndex(self.shape_combo.count() - 1)
        self.shape_combo.blockSignals(False)

        # Status bar
        if mode == "drawing":
            if pts == 0:
                hint = "Click on the map to place the first point."
            elif pts < 3:
                hint = f"Click to add points ({3 - pts} more needed)."
            else:
                hint = (f"{pts} points · {fmt_area(self.state['area_km2'])} — "
                        "click the green point or press Enter to finish.")
        elif mode == "placing":
            hint = "Click on the map to place the point. Esc to cancel."
        elif mode == "editing":
            hint = "Drag the handles to reshape; drag a faded midpoint to add a corner. Enter to save, Esc to discard."
        elif not features:
            hint = "Press Polygon (P) or Point (O) and click on the map, or Open a GeoJSON file."
        elif has_selection:
            hint = "Farm selected. Del to delete, F2 to rename, Esc to deselect."
        else:
            hint = "Click a farm to select it, or press Check (Ctrl+Enter) to check them all."
        self.hint.setText(hint)

        total = sum(f["properties"]["area_km2"] for f in features)
        noun = "farm" if len(features) == 1 else "farms"
        self.summary.setText(f"{len(features)} {noun} · {fmt_area(total)}")
        self.emit("state", self.state)

    # ----- UI -> map -----
    def on_combo_selection(self, index):
        shape_id = self.shape_combo.itemData(index)
        self.js(f"selectShape({'null' if shape_id is None else shape_id}, true)")

    def rename_selected(self):
        shape_id = self.selected_id()
        feature = next((f for f in self.geojson["features"] if f["properties"]["id"] == shape_id), None)
        if feature is None:
            return
        name, ok = QInputDialog.getText(self, "Rename farm", "Name:", text=feature["properties"]["name"])
        if ok and name.strip():
            self.js(f"renameShape({shape_id}, {json.dumps(name.strip())})")

    def delete_selected(self):
        if self.selected_id() is not None:
            self.js(f"deleteShape({self.selected_id()})")

    def geojson_text(self):
        return json.dumps(self.geojson, indent=2)

    def save(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save GeoJSON", "farms.geojson", "GeoJSON (*.geojson *.json)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.geojson_text())
            self.statusBar().showMessage(f"Saved {path}", 5000)

    def copy(self):
        QApplication.clipboard().setText(self.geojson_text())
        self.statusBar().showMessage("GeoJSON copied to clipboard", 3000)

    def show_geojson(self):
        GeoJsonDialog(self.geojson_text(), self).exec_()

    def clear_all(self):
        answer = QMessageBox.question(self, "Clear all farms", f"Delete all {len(self.geojson['features'])} farms?")
        if answer == QMessageBox.Yes:
            self.js("clearAll()")

    def closeEvent(self, event):
        self.worker.stop()
        self.worker.wait(3000)
        super().closeEvent(event)


def schedule_screenshot(window, path, seconds):
    """Save a picture of the window after a delay, then quit: a way to check the UI from a script."""
    def shoot():
        window.grab().save(path)  # renders every widget, the map view included
        print(f"Saved {path}", flush=True)
        QApplication.quit()
    QTimer.singleShot(int(seconds * 1000), shoot)


def main():
    parser = argparse.ArgumentParser(description="EUDR farm checker")
    parser.add_argument("--screenshot", help="save a picture of the window to this file, then quit")
    parser.add_argument("--after", type=float, default=20, help="seconds to wait before the screenshot")
    parser.add_argument("--open", metavar="FILE", help="add the farms in this GeoJSON file once the map has loaded")
    parser.add_argument("--check", action="store_true", help="with --open: check them all straight away")
    parser.add_argument("--benchmark", action="store_true", help="open the Compare tab and run the benchmark")
    parser.add_argument("--map", metavar="NAME", help="show this stored map at start-up (default: the last one used)")
    args, qt_args = parser.parse_known_args()

    # Scale the UI on high-DPI screens (Qt5 doesn't by default)
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    if hasattr(QGuiApplication, "setHighDpiScaleFactorRoundingPolicy"):
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    tiles.register_scheme()  # must happen before the QApplication exists
    app = QApplication([sys.argv[0]] + qt_args)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
    tile_handler = tiles.TileHandler(app)
    QWebEngineProfile.defaultProfile().installUrlSchemeHandler(tiles.SCHEME, tile_handler)
    window = MainWindow(tile_handler, map_name=args.map)
    window.showMaximized()
    if args.benchmark:
        started = []

        def start_benchmark(info):
            if not started:
                started.append(True)
                window.tabs.setCurrentWidget(window.compare)
                window.compare.run()
        window.listen("map_loaded", start_benchmark)
    if args.open:
        opened = []

        def open_once(info):
            if not opened:
                opened.append(True)
                window.checks.open_path(args.open, check=args.check)
        window.listen("map_loaded", open_once)
    if args.screenshot:
        schedule_screenshot(window, args.screenshot, args.after)
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
