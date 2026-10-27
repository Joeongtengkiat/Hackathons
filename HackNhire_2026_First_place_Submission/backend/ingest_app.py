"""
ingest_app.py - a window for ingesting a map into Neon.

Choose the satellite layers (the ~20 GeoTIFFs, in one folder) or a results folder that was already
built (e.g. by Joe), name the map, choose the area, and press Ingest. The right-hand panel lists the
files the folder must hold and the word each file name must contain, and ticks them off once a folder
is chosen. The window runs pipeline.py - exactly the command you could type yourself, shown on screen -
and follows it:

    1. Compress  the layers into evidence.tif and outcome.tif (eudr_layers.py)   [skipped for a results folder]
    2. Upload    them to Neon as the map (one transaction: a failure or Cancel saves nothing)
    3. Check     the test plots against Neon, compared with eudr_layers.py's own answers

and ends on a summary: what is stored, how big it is, whether every plot matched, and the next command.

Run it from the HackNHire folder (once: backend\\venv\\Scripts\\python -m pip install -r requirements.txt):
    backend\\venv\\Scripts\\python backend\\ingest_app.py
"""

from __future__ import annotations

import html
import os
import re
import sys
import time
from pathlib import Path

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
for _folder in ("compaction", "database"):
    if os.path.join(BACKEND_DIR, _folder) not in sys.path:
        sys.path.insert(0, os.path.join(BACKEND_DIR, _folder))

import rasterio  # noqa: E402
from PyQt5.QtCore import QEvent, QProcess, QProcessEnvironment, Qt, QThread, QTimer, QUrl, pyqtSignal  # noqa: E402
from PyQt5.QtGui import QDesktopServices, QFont, QGuiApplication  # noqa: E402
from PyQt5.QtWidgets import (QAbstractItemView, QAbstractSpinBox, QApplication, QCheckBox,  # noqa: E402
                             QDoubleSpinBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
                             QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
                             QPushButton, QScrollArea, QSizePolicy, QSpinBox, QStackedWidget, QTableWidget,
                             QTableWidgetItem, QVBoxLayout, QWidget)
from rasterio.warp import transform_bounds  # noqa: E402

import common  # noqa: E402
import layers  # noqa: E402
import maps  # noqa: E402
import protected_areas  # noqa: E402
from layers import eudr_layers  # noqa: E402

PIPELINE = os.path.join(BACKEND_DIR, "pipeline.py")

# What a layers folder must hold. eudr_layers.py finds each GeoTIFF by a word in its file name
# (eudr_layers.DISCOVERY: any case, sub-folders searched too), so the window lists those words.
# Groups of (key, name, what it is for, the word(s) the file name must contain, an example name).
LAYER_GROUPS = (
    ("Required", "", (
        ("gfc2020", "JRC forest cover 2020 (GFC2020)", "Forest on 31 Dec 2020, the EUDR cut-off",
         "gfc2020", "JRC_GFC2020_V3_N0_E100.tif"),
    )),
    ("Clearing data", "at least one; Hansen and TMF together make 'confirmed'", (
        ("lossyear", "Hansen tree-loss year", "The year Landsat saw the trees go",
         "lossyear", "Hansen_GFC-2025-v1.13_lossyear_00N_100E.tif"),
        ("tmf_annual", "JRC TMF yearly change", "TMF's class each year: one file per year, from 2020",
         "AnnualChange_<year>", "JRC_TMF_AnnualChange_v1_2020_ASI_N0_E100.tif"),
        ("tmf_deforestation_year", "JRC TMF deforestation year", "The year TMF saw the deforestation",
         "DeforestationYear", "JRC_TMF_DeforestationYear_v1_ASI_N0_E100.tif"),
    )),
    ("Recommended", "", (
        ("tmf_degradation_year", "JRC TMF degradation year", "Forest damaged but still standing",
         "DegradationYear", "JRC_TMF_DegradationYear_v1_ASI_N0_E100.tif"),
        ("tmf_transition", "JRC TMF transition map", "TMF's main transition classes",
         "TransitionMap + Main", "JRC_TMF_TransitionMap_MainClasses_v1_ASI_N0_E100.tif"),
        ("gft2020", "JRC forest type 2020 (GFT2020)", "Naturally regenerating, primary or planted forest",
         "gft2020", "JRC_GFT2020_V1_N0_E100.tif"),
        ("rubber", "Rubber map of Southeast Asia", "Rubber counts as farming, not forest",
         "rubber", "rubber_map_2020.tif"),
        ("datamask", "Hansen land/water mask", "Land, water and no data",
         "datamask", "Hansen_GFC-2025-v1.13_datamask_00N_100E.tif"),
        ("treecover2000", "Hansen tree cover 2000", "Canopy cover in 2000",
         "treecover2000", "Hansen_GFC-2025-v1.13_treecover2000_00N_100E.tif"),
    )),
    ("Optional", "extra evidence, used when present", (
        ("radd", "RADD radar alerts", "Radar clearing alerts (Sentinel-1)", "radd", "radd_alerts.tif"),
        ("primary_forest", "UMD primary forest 2001", "Where RADD watches", "primary_forest",
         "primary_forest_2001.tif"),
        ("alerts", "GFW integrated alerts", "Optical alerts after the yearly data (GLAD-L + RADD)",
         "integrated_alerts", "gfw_integrated_alerts.tif"),
        ("dist_alerts", "DIST-ALERT optical alerts", "Optical alerts after the yearly data (Landsat + Sentinel-2)",
         "dist_alert", "dist_alerts.tif"),
        ("dynamic_world", "Dynamic World land cover", "What the land became after a clearing (Sentinel-2)",
         "dynamic_world", "dynamic_world_2021.tif"),
        ("io_lulc", "Impact Observatory land cover", "What the land became after a clearing (Sentinel-2)",
         "io_lulc", "io_lulc_2021.tif"),
        ("protected", "Protected areas (WDPA, GeoJSON)", "Shown in the app as protected areas; also the "
         "evidence's 'protected' fact", "wdpa…geojson", "WDPA_Oct2026_Public_IDN-polygons.geojson"),
    )),
)
LAYER_LABELS = {row[0]: row[1] for _, _, rows in LAYER_GROUPS for row in rows}
CLEARING = ("lossyear", "tmf_annual", "tmf_deforestation_year")
RULE_NOTES = {  # the fine print of eudr_layers.DISCOVERY, shown as tooltips
    "gfc2020": "The name contains 'gfc2020' but not 'subtype' or 'gft'.",
    "gft2020": "The name contains 'gft2020' (or 'gfc2020' and 'subtype').",
    "tmf_annual": "The name contains 'AnnualChange' and the year, e.g. AnnualChange_2020 or "
                  "AnnualChange_v1_2020. One file per year; 2020 is the one that matters most.",
    "tmf_transition": "The name contains both 'TransitionMap' and 'Main'.",
    "rubber": "The name contains 'rubber' but not '1km'. Any GeoTIFF inside a folder whose name contains "
              "'rubber' counts too (the 10 m tiles are named only by their corner).",
    "dynamic_world": "The name contains 'dynamic_world' or 'dynamicworld'.",
    "primary_forest": "The name contains 'primary_forest' or 'primary-forest'.",
    "protected": "A GeoJSON file (.geojson) whose name starts with 'wdpa', e.g. WDPA_Oct2026_Public_IDN-polygons."
                 "geojson. Several are fine (they are merged). Not a GeoTIFF: the polygons are stored as they are.",
}
KEYWORDS = {  # the part of a file name each DISCOVERY rule looks at, to highlight it (any case)
    "gfc2020": r"gfc2020", "lossyear": r"lossyear", "tmf_annual": eudr_layers.ANNUAL_RE.pattern,
    "tmf_deforestation_year": r"deforestationyear", "tmf_degradation_year": r"degradationyear",
    "tmf_transition": r"transitionmap|main", "gft2020": r"gft2020|subtype", "rubber": r"rubber",
    "datamask": r"datamask", "treecover2000": r"treecover2000", "radd": r"radd",
    "primary_forest": r"primary[_-]forest", "alerts": r"integrated_alerts", "dist_alerts": r"dist_alert",
    "dynamic_world": r"dynamic_?world", "io_lulc": r"io_lulc", "protected": r"^wdpa|\.geojson$",
}
KEY_STYLE = "color:#1d4ed8; font-weight:700;"
# A results folder (already compressed): (key, file, what it is, required)
RESULTS_FILES = (
    ("evidence", "evidence.tif", "Every EUDR decision fact per pixel", True),
    ("outcome", "outcome.tif", "The outcome map", True),
    ("test_plots", "test_plots.geojson", "Test plots (built with --test-plots)", False),
    ("verified", "summary.json with fidelity", "Built with --verify: the evaluation compares with the layers", False),
    ("protected", "wdpa*.geojson", "Protected areas, stored with the map (the app shows them)", False),
)
TABLE_NOTES = {
    "grid_info": "the map's grid",
    "evidence_info": "evidence.tif's bit layout",
    "evidence_pages": "evidence.tif, in pages",
    "protected_areas": "the protected areas (PostGIS)",
    "stencil_pages": "the outcome's colours, in pages",
    "class_polys": "the outlines (PostGIS)",
    "square_labels": "squares of one colour",
    "plots": "the plots",
    "plot_results": "the plots' answers (colours)",
    "evidence_results": "the plots' answers (evidence)",
}
STAGES = (("Compress the layers", "eudr_layers.py turns the layers into evidence.tif and outcome.tif"),
          ("Upload to Neon", "One transaction: a failure or Cancel saves nothing"),
          ("Check the plots against Neon", "Every plot's answer from Neon, compared with eudr_layers.py's"))
MARKS = {"ok": "✔", "error": "✖", "warn": "!", "none": "○", "idle": "•"}

STEP_RE = re.compile(r"^Step (\d) of 3")
PERCENT_RE = re.compile(r"^\s*(.+?):\s+(\d+(?:\.\d+)?)% \(")
SAME_RE = re.compile(r"Every output the same as eudr_layers\.py from evidence\.tif: (\d+) of (\d+) plots")

# The frontend app's look (frontend/app.py): white cards on a warm grey, blue for actions
STYLE = """
QWidget { font-family: "Segoe UI"; font-size: 10pt; color: #0b0b0b; }
QWidget#page { background: #f7f7f5; }
QWidget#clear { background: transparent; }
QFrame#card { background: #ffffff; border: 1px solid #e5e4df; border-radius: 10px; }
QFrame#footer { background: #ffffff; border: none; border-top: 1px solid #e5e4df; }
QFrame#rule { background: #efeee9; border: none; max-height: 1px; min-height: 1px; }
QFrame#banner { border-radius: 10px; border: 1px solid #e5e4df; background: #ffffff; }
QFrame#banner[tone="ok"] { background: #f0faf0; border-color: #bfe3bf; }
QFrame#banner[tone="warn"] { background: #fff7ed; border-color: #f6d5b0; }
QFrame#banner[tone="error"] { background: #fef3f2; border-color: #f4c7c3; }

QLabel[role="title"] { font-size: 17pt; font-weight: 600; }
QLabel[role="heading"] { font-size: 11pt; font-weight: 600; }
QLabel[role="rowtitle"] { font-weight: 600; }
QLabel[role="secondary"] { color: #52514e; }
QLabel[role="muted"] { color: #898781; }
QLabel[role="caps"] { color: #898781; font-size: 8pt; font-weight: 600; }
QLabel[role="error"] { color: #b42318; }
QLabel[role="warning"] { color: #b45309; }
QLabel[role="ok"] { color: #1b7837; }
QLabel[dim="true"] { color: #898781; }
QLabel[role="stat"] { font-size: 16pt; font-weight: 600; }
QLabel[role="chip"] { font-family: Consolas; font-size: 9pt; font-weight: 700; color: #1d4ed8; background: #eef2ff;
                      border: 1px solid #c7d2fe; border-radius: 4px; padding: 2px 7px; }
QLabel[role="fname"] { font-family: Consolas; font-size: 9pt; color: #898781; }
QLabel[role="fname"][tone="ok"] { color: #1f2937; }
QLabel[role="fname"][tone="warn"] { color: #b45309; }
QLabel[role="fname"][tone="error"] { color: #b42318; }
QFrame#naming { background: #f5f8ff; border: 1px solid #dbe4ff; border-radius: 8px; }
QFrame#notice { background: #fff7ed; border: 1px solid #f6d5b0; border-radius: 8px; }
QLabel[role="code"] { font-family: Consolas; font-size: 9pt; color: #1f2937; background: #f7f7f5;
                      border: 1px solid #e5e4df; border-radius: 6px; padding: 8px 10px; }
QLabel[role="rowsub"], QLabel[role="note"] { color: #898781; font-size: 9pt; }
QLabel[role="rowsub"][tone="ok"] { color: #1b7837; font-family: Consolas; }
QLabel[role="rowsub"][tone="warn"] { color: #b45309; }
QLabel[role="rowsub"][tone="error"] { color: #b42318; }
QLabel[role="mark"] { font-weight: 700; color: #c3c2b7; }
QLabel[role="mark"][tone="ok"] { color: #0ca30c; }
QLabel[role="mark"][tone="warn"] { color: #b45309; }
QLabel[role="mark"][tone="error"] { color: #d03b3b; }
QLabel[role="mark"][tone="none"] { color: #b8b7b0; }
QLabel[role="badge"] { font-weight: 600; font-size: 9pt; border-radius: 12px; color: #2563eb; background: #eef2ff; }
QLabel[role="badge"][tone="run"] { color: #ffffff; background: #2563eb; }
QLabel[role="badge"][tone="ok"] { color: #ffffff; background: #0ca30c; }
QLabel[role="badge"][tone="skip"] { color: #898781; background: #efeee9; }
QLabel[role="badge"][tone="error"] { color: #ffffff; background: #d03b3b; }
QLabel[role="bigmark"] { font-size: 20pt; font-weight: 700; border-radius: 24px; color: #ffffff; }
QLabel[role="bigmark"][tone="ok"] { background: #0ca30c; }
QLabel[role="bigmark"][tone="warn"] { background: #ec835a; }
QLabel[role="bigmark"][tone="error"] { background: #d03b3b; }
QLabel[role="pill"] { border-radius: 11px; padding: 3px 10px; font-size: 9pt; color: #52514e; background: #efeee9; }
QLabel[role="pill"][tone="ok"] { color: #067647; background: #e7f6ec; }
QLabel[role="pill"][tone="error"] { color: #b42318; background: #fde8e6; }

QLineEdit, QSpinBox, QDoubleSpinBox { padding: 5px 8px; border: 1px solid #d1d5db; border-radius: 6px;
                                      background: #ffffff; selection-background-color: #bfdbfe; selection-color: #0b0b0b; }
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus { border-color: #2563eb; }
QLineEdit[state="error"] { border-color: #d03b3b; }
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled { background: #f7f7f5; color: #a3a29c; }
QPushButton { padding: 6px 14px; border: 1px solid #d1d5db; border-radius: 6px; background: #ffffff; color: #1f2937; }
QPushButton:hover { background: #f3f4f6; }
QPushButton:disabled { color: #a3a29c; background: #f7f7f5; }
QPushButton[role="primary"] { background: #2563eb; border-color: #2563eb; color: #ffffff; font-weight: 600;
                              padding: 8px 26px; }
QPushButton[role="primary"]:hover { background: #1d4ed8; }
QPushButton[role="primary"]:disabled { background: #b9cbf6; border-color: #b9cbf6; color: #ffffff; }
QPushButton[role="danger"] { color: #b42318; border-color: #f1c4c0; }
QPushButton[role="danger"]:hover { background: #fef3f2; }
QPushButton[role="link"] { border: none; background: transparent; color: #2563eb; padding: 2px 2px; }
QPushButton[role="link"]:hover { text-decoration: underline; }
QPushButton[role="link"]:disabled { color: #a3a29c; }
QCheckBox { spacing: 8px; font-weight: 600; }
QCheckBox:disabled { color: #a3a29c; }
QProgressBar { border: none; background: #e5e7eb; border-radius: 3px; }
QProgressBar::chunk { background: #2563eb; border-radius: 3px; }
QPlainTextEdit#log { background: #16181d; color: #d7dae0; border: none; border-radius: 8px; padding: 6px;
                     font-family: Consolas; font-size: 9pt; selection-background-color: #3b4252; }
QTableWidget { background: #ffffff; border: none; gridline-color: #f0efeb; selection-background-color: #eef2ff;
               selection-color: #0b0b0b; }
QHeaderView::section { background: #ffffff; border: none; border-bottom: 1px solid #e5e4df; padding: 4px 6px;
                       color: #898781; font-weight: 600; }
QToolTip { color: #0b0b0b; background: #ffffff; border: 1px solid #d1d5db; padding: 4px; }
"""


# --------------------------------------------------------------------------------------
# plain helpers (no window)
# --------------------------------------------------------------------------------------
def results_folder_or_none(path: str) -> str | None:
    """The path if it is a results folder of the compaction step (evidence.tif + outcome.tif)."""
    if not (os.path.isdir(path) and os.path.isfile(os.path.join(path, "outcome.tif"))):
        return None
    try:
        return path if os.path.isfile(layers.evidence_file(path)) else None
    except (SystemExit, Exception):  # noqa: BLE001 - anything odd: treat it as not a results folder
        return None


def inspect_folder(path: str) -> dict:
    """What a folder holds, for the setup screen. kind: 'results', 'layers' or 'none'."""
    path = os.path.normpath(path)
    if not os.path.isdir(path):
        return {"kind": "none", "problem": f"Can't find the folder {path}. Check the path, or press Browse…"}
    if results_folder_or_none(path):
        info = {"kind": "results", "problem": "", "warning": ""}
        with rasterio.open(os.path.join(path, "outcome.tif")) as src:
            info["size"] = (src.width, src.height)
        info["problem"] = maps.size_problem(*info["size"])
        summary = layers.summary(path) or {}
        info["verified"] = bool((summary.get("fidelity") or {}).get("plots_compared"))
        info["test_plots"] = os.path.isfile(os.path.join(path, "test_plots.geojson"))
        info["evidence_name"] = os.path.basename(layers.evidence_file(path))
        info["protected"] = protected_areas.find_files(path)
        return info
    found, annual = eudr_layers.discover(path)
    tifs = [p for p in Path(path).rglob("*") if p.suffix.lower() in (".tif", ".tiff") and p.is_file()]
    known = {p for v in found.values() for p in v} | {p for v in annual.values() for p in v}
    unknown = [p for p in tifs if p not in known]
    if not found and not annual:
        return {"kind": "none", "unknown": unknown,
                "problem": ("None of the GeoTIFFs here has a keyword in its name: rename each to contain its "
                            "layer's keyword (on the right)." if tifs else
                            "No GeoTIFFs here. Choose the folder holding the satellite layers, or a results "
                            "folder (evidence.tif + outcome.tif).")}
    protected = protected_areas.find_files(path)
    if protected:
        found["protected"] = [Path(p) for p in protected]
    info = {"kind": "layers", "found": found, "annual": annual, "unknown": unknown, "n_files": len(tifs),
            "problem": "", "warning": ""}
    has_tmf = bool(annual) or "tmf_deforestation_year" in found
    if "gfc2020" not in found:
        info["problem"] = ("Missing the required JRC forest cover 2020 layer: add a GeoTIFF with 'gfc2020' in "
                           "its name.")
    elif "lossyear" not in found and not has_tmf:
        info["problem"] = ("Missing clearing data: add Hansen lossyear or the JRC TMF files "
                           "(AnnualChange_<year> or DeforestationYear).")
    elif "lossyear" not in found:
        info["warning"] = ("No Hansen lossyear: nothing can be 'confirmed' (that needs two sources), so TMF "
                           "clearing shows as 'probable'.")
    elif not has_tmf:
        info["warning"] = "No JRC TMF files: nothing can be 'confirmed' (that needs two sources)."
    bounds, res = None, None
    for p in found.get("gfc2020", []):
        with rasterio.open(p) as src:
            b = transform_bounds(src.crs, "EPSG:4326", *src.bounds) if src.crs else tuple(src.bounds)
            r = src.res[0] if (src.crs is None or src.crs.is_geographic) else src.res[0] / 111320.0
        bounds = b if bounds is None else (min(bounds[0], b[0]), min(bounds[1], b[1]),
                                           max(bounds[2], b[2]), max(bounds[3], b[3]))
        res = r if res is None else min(res, r)
    info["bounds"], info["res_deg"] = bounds, res
    return info


def layers_found(info: dict) -> int:
    """How many of the listed layers a layers folder has."""
    return sum(1 for key in LAYER_LABELS
               if (info.get("annual") if key == "tmf_annual" else info.get("found", {}).get(key)))


def name_problem(name: str, existing: list[tuple[str, str]]) -> str:
    """Why a new map can't have this name ("" if it can). existing: [(name, schema)] stored."""
    name = (name or "").strip()
    try:
        schema = common.map_schema(name)
    except SystemExit as exc:
        return str(exc.code)
    for other, other_schema in existing:
        if other == name:
            return f"There's already a map called '{other}'. Choose another name, or delete that map first."
        if other_schema == schema:
            return f"'{name}' is too close to '{other}': both would be stored as {schema}. Choose another name."
    return ""


def map_summary(name: str) -> dict:
    """What the new map takes in Neon (read-only)."""
    with common.connect() as conn:
        grid = common.load_grid(conn, name)
        sizes = common.table_sizes(conn, [grid.table(t) for t in maps.MAP_TABLES])
        conn.rollback()
    return {"schema": grid.schema, "width": grid.width, "height": grid.height,
            "tables": {t.rsplit(".", 1)[-1]: v for t, v in sizes.items()},
            "total": sum(size for size, _ in sizes.values())}


def duration(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 60} min {seconds % 60:02d} s" if seconds >= 60 else f"{seconds} s"


def megabytes(n_bytes: float) -> str:
    return f"{n_bytes / 1e6:,.1f} MB" if n_bytes >= 1e5 else f"{n_bytes / 1e3:,.0f} KB"


class Task(QThread):
    """Runs fn() off the window's thread (Neon is a network away); done(result) or failed(message)."""
    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn

    def run(self):
        try:
            self.done.emit(self.fn())
        except SystemExit as exc:  # the backend's own "how to fix it" messages
            self.failed.emit(str(exc.code))
        except Exception as exc:  # noqa: BLE001 - shown in the window instead of crashing it
            self.failed.emit(f"{type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------------------
# small widgets
# --------------------------------------------------------------------------------------
def make_label(text="", role=None, wrap=True) -> QLabel:
    widget = QLabel(text)
    widget.setTextFormat(Qt.PlainText)
    widget.setWordWrap(wrap)
    if role:
        widget.setProperty("role", role)
    return widget


def set_prop(widget, name, value) -> None:
    """Change a property the stylesheet reads, and restyle the widget."""
    widget.setProperty(name, value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def badge(text, size=24) -> QLabel:
    widget = make_label(text, "badge", wrap=False)
    widget.setAlignment(Qt.AlignCenter)
    widget.setFixedSize(size, size)
    widget.setStyleSheet(f"border-radius: {size // 2}px;")
    return widget


def mark(tone="idle") -> QLabel:
    widget = make_label(MARKS[tone], "mark", wrap=False)
    widget.setProperty("tone", tone)
    widget.setFixedWidth(16)
    widget.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
    return widget


def set_mark(widget, tone) -> None:
    widget.setText(MARKS[tone])
    set_prop(widget, "tone", tone)


def link_button(text, slot) -> QPushButton:
    button = QPushButton(text)
    button.setProperty("role", "link")
    button.setCursor(Qt.PointingHandCursor)
    button.clicked.connect(slot)
    return button


def rule() -> QFrame:
    line = QFrame()
    line.setObjectName("rule")
    return line


def card(margins=(16, 14, 16, 14), spacing=8) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(*margins)
    layout.setSpacing(spacing)
    return frame, layout


def scroll_area(widget) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    area.setStyleSheet("QScrollArea { background: transparent; }")
    area.viewport().setAutoFillBackground(False)
    widget.setObjectName("clear")
    area.setWidget(widget)
    return area


class Elided(QLabel):
    """One line that shortens itself with … instead of widening the window (the full text is its tooltip)."""

    def __init__(self, text="", role=None, mode=Qt.ElideMiddle):
        super().__init__()
        self.full, self.mode = "", mode
        self.setTextFormat(Qt.PlainText)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        if role:
            self.setProperty("role", role)
        self.setText(text)

    def setText(self, text):  # noqa: N802 - Qt's name
        self.full = text or ""
        self.setToolTip(self.full)
        self.fit()

    def fit(self):
        width = max(self.contentsRect().width(), 40)
        super().setText(self.fontMetrics().elidedText(self.full, self.mode, width))

    def resizeEvent(self, event):  # noqa: N802 - Qt's name
        super().resizeEvent(event)
        self.fit()

    def changeEvent(self, event):  # noqa: N802 - Qt's name
        super().changeEvent(event)
        if event.type() in (QEvent.FontChange, QEvent.StyleChange):
            self.fit()


def highlight(text: str, pattern: str | None) -> str:
    """text as HTML, with the parts matching pattern (any case) in the keyword style."""
    out, last = [], 0
    for match in re.finditer(pattern, text, re.I) if pattern else ():
        if match.end() > match.start():
            out += [html.escape(text[last:match.start()]),
                    f'<span style="{KEY_STYLE}">{html.escape(match.group(0))}</span>']
            last = match.end()
    out.append(html.escape(text[last:]))
    return f'<span style="white-space:pre">{"".join(out)}</span>'


class KeywordLabel(Elided):
    """A file name on one line with its layer's keyword in blue: why the file is (or would be) recognised."""
    pattern = None

    def __init__(self, pattern):
        super().__init__("", "fname", Qt.ElideRight)
        self.setTextFormat(Qt.RichText)
        self.pattern = pattern
        self.fit()

    def fit(self):
        width = max(self.contentsRect().width(), 40) - 10  # room for the bold keyword
        QLabel.setText(self, highlight(self.fontMetrics().elidedText(self.full, self.mode, width), self.pattern))


class LayerGuide(QWidget):
    """What a layers folder must hold: one row per layer, led by the keyword its file name must contain,
    with an example name (keyword highlighted), ticked off with the file found once a folder is chosen."""

    def __init__(self):
        super().__init__()
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 6, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(0)
        grid.setColumnStretch(2, 1)
        self.rows = {}
        r = 0
        for group, note, rows in LAYER_GROUPS:
            head = make_label(group.upper() + (f"   {note}" if note else ""), "caps")
            head.setContentsMargins(0, 14 if r else 2, 0, 6)
            grid.addWidget(head, r, 0, 1, 3)
            r += 1
            for key, name, purpose, word, example in rows:
                icon = mark()
                chip = make_label(word, "chip", wrap=False)
                chip.setToolTip(RULE_NOTES.get(key, f"The file name contains '{word}' (any case)."))
                title = make_label(name, "rowtitle", wrap=False)
                title.setToolTip(purpose)
                sub = KeywordLabel(KEYWORDS.get(key))
                sub.setContentsMargins(0, 3, 0, 10)
                grid.addWidget(icon, r, 0, 2, 1)
                grid.addWidget(chip, r, 1, Qt.AlignLeft | Qt.AlignVCenter)
                grid.addWidget(title, r, 2)
                grid.addWidget(sub, r + 1, 1, 1, 2)
                self.rows[key] = (icon, sub, purpose, example)
                r += 2
        grid.setRowStretch(r, 1)
        self.show_folder({"kind": "none"})

    def show_folder(self, info: dict) -> None:
        scanned = info["kind"] == "layers"
        found, annual = info.get("found", {}), info.get("annual", {})
        has_clearing = bool(annual) or any(found.get(k) for k in CLEARING if k != "tmf_annual")
        for key, (icon, sub, purpose, example) in self.rows.items():
            files = [p for y in sorted(annual) for p in annual[y]] if key == "tmf_annual" else found.get(key, [])
            if not scanned:
                tone, text = "idle", f"e.g. {example}"
            elif files and key == "tmf_annual":
                years = sorted(annual)
                tone = "ok" if eudr_layers.CUTOFF_YEAR in annual else "warn"
                span = f"{years[0]}–{years[-1]}" if len(years) > 1 else str(years[0])
                text = (f"{len(files)} file{'s' if len(files) > 1 else ''}, {span}"
                        + ("" if tone == "ok" else f", none for {eudr_layers.CUTOFF_YEAR}")
                        + f": {Path(files[0]).name}")
            elif files:
                tone = "ok"
                text = Path(files[0]).name + (f"  + {len(files) - 1} more" if len(files) > 1 else "")
            elif key in eudr_layers.REQUIRED:
                tone, text = "error", f"missing (required) · e.g. {example}"
            elif key in CLEARING and not has_clearing:
                tone, text = "error", f"missing (need one clearing source) · e.g. {example}"
            else:
                tone, text = "none", f"not found · e.g. {example}"
            set_mark(icon, tone)
            set_prop(sub, "tone", tone)
            sub.setText(text)
            if files:
                sub.setToolTip("\n".join(str(p) for p in files[:40]) + ("\n…" if len(files) > 40 else ""))
            else:
                sub.setToolTip(f"{purpose}. {RULE_NOTES.get(key, '')}".strip())


class ResultsGuide(QWidget):
    """What a results folder holds (evidence.tif + outcome.tif, and the extras the evaluation uses)."""

    def __init__(self):
        super().__init__()
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 6, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(0)
        grid.setColumnStretch(1, 1)
        head = make_label("A RESULTS FOLDER   already compressed by eudr_layers.py", "caps")
        head.setContentsMargins(0, 2, 0, 6)
        grid.addWidget(head, 0, 0, 1, 2)
        self.rows = {}
        for i, (key, name, purpose, required) in enumerate(RESULTS_FILES):
            icon = mark()
            title = make_label(name, "rowtitle", wrap=False)
            sub = Elided("", "rowsub", Qt.ElideRight)
            sub.setContentsMargins(0, 1, 0, 9)
            grid.addWidget(icon, 1 + 2 * i, 0, 2, 1)
            grid.addWidget(title, 1 + 2 * i, 1)
            grid.addWidget(sub, 2 + 2 * i, 1)
            self.rows[key] = (icon, title, sub, purpose, required)
        grid.setRowStretch(1 + 2 * len(RESULTS_FILES), 1)

    def show_folder(self, info: dict) -> None:
        have = {"evidence": True, "outcome": True, "test_plots": info.get("test_plots"),
                "verified": info.get("verified"), "protected": info.get("protected")}
        for key, (icon, title, sub, purpose, required) in self.rows.items():
            if key == "evidence":
                title.setText(info.get("evidence_name") or "evidence.tif")
            if key == "outcome" and info.get("size"):
                w, h = info["size"]
                purpose = f"The outcome map, {w:,} × {h:,} pixels"
            if key == "protected" and have[key]:
                files = info["protected"]
                title.setText(os.path.basename(files[0]) + (f"  + {len(files) - 1} more" if len(files) > 1 else ""))
            if have[key]:
                tone, text = "ok", purpose
            else:
                tone = "warn" if key == "verified" else "none"
                text = {"test_plots": "None: only your own plots (if any) are checked",
                        "verified": "Built without --verify: the evaluation won't have fidelity against the "
                                    "original layers",
                        "protected": "None: the map is stored without protected areas"}[key]
            set_mark(icon, tone)
            set_prop(sub, "tone", "warn" if tone == "warn" else "")
            sub.setText(text)


# --------------------------------------------------------------------------------------
# the window
# --------------------------------------------------------------------------------------
class IngestWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Ingest a map into Neon")
        self.resize(1120, 800)
        self.setMinimumSize(980, 660)
        self.existing = None          # [(name, schema)] of the maps in Neon, once listed
        self.neon_error = ""
        self.info = {"kind": "none", "problem": ""}
        self.auto_name = ""           # the name filled in from the folder's name
        self.tasks = []
        self.proc = None
        self.run_state = {}

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        self.stack.addWidget(self.build_setup())
        self.stack.addWidget(self.build_working())
        self.stack.addWidget(self.build_done())
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.list_maps()

    def run_task(self, fn, done, failed):
        task = Task(fn, self)
        task.done.connect(done)
        task.failed.connect(failed)
        task.finished.connect(lambda: self.tasks.remove(task))
        self.tasks.append(task)
        task.start()

    @staticmethod
    def page():
        page = QWidget()
        page.setObjectName("page")
        return page

    @staticmethod
    def footer(*widgets_left) -> tuple[QFrame, QHBoxLayout]:
        bar = QFrame()
        bar.setObjectName("footer")
        row = QHBoxLayout(bar)
        row.setContentsMargins(24, 12, 24, 12)
        row.setSpacing(10)
        for widget in widgets_left:
            row.addWidget(widget)
        return bar, row

    @staticmethod
    def titles(title_label, sub_label) -> QVBoxLayout:
        column = QVBoxLayout()
        column.setSpacing(2)
        column.addWidget(title_label)
        column.addWidget(sub_label)
        return column

    # ----- screen 1: set up -----
    def build_setup(self):
        page = self.page()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        body = QVBoxLayout()
        body.setContentsMargins(24, 20, 24, 16)
        body.setSpacing(16)
        outer.addLayout(body, 1)

        head = QHBoxLayout()
        head.addLayout(self.titles(make_label("Ingest a map", "title"),
                                   make_label("Compress the satellite layers, upload them to Neon and check them: "
                                              "pipeline.py, in a window.", "secondary")), 1)
        self.neon_pill = make_label("", "pill", wrap=False)
        self.retry_btn = link_button("Retry", self.list_maps)
        self.retry_btn.hide()
        head.addWidget(self.neon_pill, 0, Qt.AlignTop)
        head.addWidget(self.retry_btn, 0, Qt.AlignTop)
        body.addLayout(head)

        columns = QHBoxLayout()
        columns.setSpacing(16)
        left = QWidget()
        form = QVBoxLayout(left)
        form.setContentsMargins(0, 0, 6, 0)
        form.setSpacing(10)
        self.build_form(form)
        form.addStretch(1)
        left_area = scroll_area(left)
        left_area.setFixedWidth(476)
        columns.addWidget(left_area)
        columns.addWidget(self.build_guide(), 1)
        body.addLayout(columns, 1)

        self.status_mark = mark()
        self.status_mark.setAlignment(Qt.AlignCenter)
        self.status = make_label("", "secondary")
        bar, row = self.footer()
        row.addWidget(self.status_mark, 0, Qt.AlignVCenter)
        row.addWidget(self.status, 1)
        self.start_btn = QPushButton("Ingest")
        self.start_btn.setProperty("role", "primary")
        self.start_btn.setMinimumWidth(150)
        self.start_btn.clicked.connect(self.start)
        row.addWidget(self.start_btn)
        outer.addWidget(bar)

        self.folder.textChanged.connect(self.folder_changed)
        self.name.textChanged.connect(self.validate)
        self.plots.textChanged.connect(self.validate)
        self.folder_changed("")
        return page

    def form_card(self, form, number, title):
        frame, layout = card((16, 12, 16, 12), 6)
        head = QHBoxLayout()
        head.setSpacing(10)
        head.addWidget(badge(str(number)))
        head.addWidget(make_label(title, "heading", wrap=False))
        head.addStretch(1)
        layout.addLayout(head)
        form.addWidget(frame)
        return frame, layout

    def build_form(self, form):
        # 1. the data, and the map's name (filled in from the folder's)
        _, lay = self.form_card(form, 1, "Data and name")
        row = QHBoxLayout()
        self.folder = QLineEdit()
        self.folder.setPlaceholderText("The satellite layers, or a results folder")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self.browse_folder)
        row.addWidget(self.folder, 1)
        row.addWidget(browse)
        lay.addLayout(row)
        self.folder_note = make_label("", "note")
        lay.addWidget(self.folder_note)

        lay.addSpacing(4)
        lay.addWidget(make_label("Map name", "rowtitle", wrap=False))
        self.name = QLineEdit()
        self.name.setMaxLength(common.MAP_NAME_MAX)
        self.name.setPlaceholderText("e.g. jambi")
        lay.addWidget(self.name)
        self.name_note = make_label("", "note")
        lay.addWidget(self.name_note)

        # 2. the area
        self.area_box, lay = self.form_card(form, 2, "Area")
        self.area_intro = make_label("", "note")
        lay.addWidget(self.area_intro)
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        self.west, self.south, self.east, self.north = (QDoubleSpinBox() for _ in range(4))
        for spin, lo, hi in ((self.west, -180, 180), (self.east, -180, 180), (self.south, -90, 90),
                             (self.north, -90, 90)):
            spin.setRange(lo, hi)
            spin.setDecimals(4)
            spin.setSingleStep(0.05)
            spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
            spin.valueChanged.connect(self.validate)
        for (r, c), (text, spin) in zip(((0, 0), (0, 2), (1, 0), (1, 2)),
                                        (("West", self.west), ("East", self.east), ("South", self.south),
                                         ("North", self.north))):
            grid.addWidget(make_label(text, "secondary", wrap=False), r, c)
            grid.addWidget(spin, r, c + 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        lay.addLayout(grid)
        row = QHBoxLayout()
        self.area_note = make_label("", "note")
        row.addWidget(self.area_note, 1)
        self.whole_btn = link_button("Use the whole area", lambda: self.use_whole_area())
        row.addWidget(self.whole_btn, 0, Qt.AlignTop)
        lay.addLayout(row)

        # 3. options
        _, lay = self.form_card(form, 3, "Options")
        row = QHBoxLayout()
        row.addWidget(make_label("Test plots", "rowtitle", wrap=False))
        self.test_plots = QSpinBox()
        self.test_plots.setRange(0, 20000)
        self.test_plots.setSingleStep(100)
        self.test_plots.setValue(1000)
        self.test_plots.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.test_plots.setFixedWidth(80)
        row.addWidget(self.test_plots)
        self.test_plots_note = make_label("made-up plots, checked after the upload", "note")
        row.addWidget(self.test_plots_note, 1)
        lay.addLayout(row)
        self.verify = QCheckBox("Compare with the original layers (--verify)")
        self.verify.setChecked(True)
        lay.addWidget(self.verify)
        self.verify_note = self.indented(lay, "The evaluation's fidelity numbers need it. Step 1 takes longer.")
        self.outlines = QCheckBox("Store the outlines too")
        self.outlines.setChecked(True)
        lay.addWidget(self.outlines)
        self.indented(lay, "The app's outline comparison needs them. About 24× the stencils' size.")
        lay.addWidget(make_label("Your farm plots (optional)", "rowtitle", wrap=False))
        row = QHBoxLayout()
        self.plots = QLineEdit()
        self.plots.setPlaceholderText("GeoJSON, EU Information System format")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self.browse_plots)
        row.addWidget(self.plots, 1)
        row.addWidget(browse)
        lay.addLayout(row)
        self.plots_note = make_label("", "error")
        self.plots_note.hide()
        lay.addWidget(self.plots_note)

    @staticmethod
    def indented(layout, text) -> QLabel:
        note = make_label(text, "note")
        note.setContentsMargins(24, 0, 0, 2)
        layout.addWidget(note)
        return note

    def build_guide(self):
        frame, lay = card((18, 16, 18, 14), 8)
        self.guide_title = make_label("", "heading")
        lay.addWidget(self.guide_title)

        # the naming rule, before the list (the list repeats it per layer)
        self.naming = QFrame()
        self.naming.setObjectName("naming")
        nlay = QVBoxLayout(self.naming)
        nlay.setContentsMargins(12, 10, 12, 10)
        nlay.setSpacing(6)
        rule_text = QLabel("eudr_layers.py recognises each GeoTIFF by a <b>keyword in its file name</b> "
                           f'(<span style="{KEY_STYLE}">blue</span>, any case). The rest of the name can be '
                           "anything, so the official download names usually work as they are.")
        rule_text.setWordWrap(True)
        nlay.addWidget(rule_text)
        example = QLabel(f'<span style="font-family:Consolas">{highlight("JRC_GFC2020_V3_N0_E100.tif", "gfc2020")}'
                         '</span>&nbsp;&nbsp;→&nbsp;&nbsp;JRC forest cover 2020')
        nlay.addWidget(example)
        nlay.addWidget(make_label(".tif or .tiff · sub-folders are searched · several tiles per layer are fine · "
                                  "a GeoTIFF whose name has no keyword is ignored", "note"))
        lay.addWidget(self.naming)
        self.guide_intro = make_label("", "secondary")
        lay.addWidget(self.guide_intro)
        lay.addSpacing(2)
        self.guide_stack = QStackedWidget()
        self.layer_guide = LayerGuide()
        self.results_guide = ResultsGuide()
        self.guide_stack.addWidget(scroll_area(self.layer_guide))
        self.guide_stack.addWidget(scroll_area(self.results_guide))
        lay.addWidget(self.guide_stack, 1)

        self.unknown_box = QFrame()
        self.unknown_box.setObjectName("notice")
        ulay = QVBoxLayout(self.unknown_box)
        ulay.setContentsMargins(12, 8, 12, 8)
        self.unknown_note = make_label("", "warning")
        ulay.addWidget(self.unknown_note)
        lay.addWidget(self.unknown_box)
        lay.addWidget(rule())
        self.guide_foot = make_label("", "muted")
        lay.addWidget(self.guide_foot)
        return frame

    def list_maps(self):
        self.retry_btn.hide()
        self.existing, self.neon_error = None, ""
        self.neon_pill.setText("Neon · connecting…")
        set_prop(self.neon_pill, "tone", "")
        self.validate()

        def work():
            with common.connect() as conn:
                found = maps.list_maps(conn)
                conn.rollback()
            return [(m["name"], m["schema"]) for m in found]
        self.run_task(work, self.listed, self.list_failed)

    def listed(self, existing):
        self.existing = existing
        n = len(existing)
        self.neon_pill.setText(f"●  Neon connected · {n} map{'' if n == 1 else 's'} stored")
        self.neon_pill.setToolTip("Maps in Neon:\n" + "\n".join(name for name, _ in existing) if existing
                                  else "No maps in Neon yet.")
        set_prop(self.neon_pill, "tone", "ok")
        self.validate()

    def list_failed(self, message):
        self.existing, self.neon_error = None, message
        self.neon_pill.setText("●  Can't reach Neon")
        self.neon_pill.setToolTip(message)
        set_prop(self.neon_pill, "tone", "error")
        self.retry_btn.show()
        self.validate()

    def browse_folder(self):
        start = self.folder.text().strip() or BACKEND_DIR
        path = QFileDialog.getExistingDirectory(self, "Choose the folder of satellite layers (or a results folder)",
                                                start if os.path.isdir(start) else BACKEND_DIR)
        if path:
            self.folder.setText(os.path.normpath(path))

    def browse_plots(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose farm plots", BACKEND_DIR,
                                              "GeoJSON (*.geojson *.json);;All files (*)")
        if path:
            self.plots.setText(os.path.normpath(path))

    def folder_changed(self, text):
        path = text.strip()
        try:
            self.info = inspect_folder(path) if path else {"kind": "none", "problem": ""}
        except Exception as exc:  # noqa: BLE001 - an unreadable file: say which, don't crash
            self.info = {"kind": "none", "problem": f"Could not read the folder: {exc}"}
        kind = self.info["kind"]
        results = kind == "results"

        # the folder's own line
        if not path:
            self.folder_note.setText("The GeoTIFFs listed on the right, or a results folder already built.")
            set_prop(self.folder_note, "role", "note")
        elif self.info.get("problem"):
            self.folder_note.setText(self.info["problem"])
            set_prop(self.folder_note, "role", "error")
        elif results:
            w, h = self.info["size"]
            self.folder_note.setText(f"✔  A results folder ({w:,} × {h:,} pixels): uploaded as it is.")
            set_prop(self.folder_note, "role", "ok")
        else:
            n = self.info["n_files"]
            self.folder_note.setText(f"✔  {layers_found(self.info)} of {len(LAYER_LABELS)} layers found, in "
                                     f"{n} GeoTIFF{'' if n == 1 else 's'}.")
            set_prop(self.folder_note, "role", "ok")

        # the guide
        self.guide_stack.setCurrentIndex(1 if results else 0)
        (self.results_guide if results else self.layer_guide).show_folder(self.info)
        self.guide_title.setText("What a results folder holds" if results else "File names: what the folder must hold")
        self.naming.setVisible(not results)
        self.guide_intro.setText("This folder was compressed already, so it is uploaded as it is.")
        self.guide_intro.setVisible(results)
        unknown = self.info.get("unknown") or []
        listed = "\n".join(f"   •  {p.name}" for p in unknown[:5]) + (f"\n   …and {len(unknown) - 5} more"
                                                                       if len(unknown) > 5 else "")
        self.unknown_note.setText(f"{len(unknown)} GeoTIFF{' has' if len(unknown) == 1 else 's have'} no keyword in "
                                  f"the name, so {'it is' if len(unknown) == 1 else 'they are'} not used:\n{listed}\n"
                                  "To use one, rename it to contain its layer's keyword." if unknown else "")
        self.unknown_box.setVisible(bool(unknown))
        self.guide_foot.setText(
            "To rebuild it from the satellite layers, choose the layers folder instead." if results else
            "Already compressed? Choose a results folder instead: one holding evidence.tif and outcome.tif "
            "(e.g. backend\\layers\\<map>).")

        # what the results folder decides already
        for widget in (self.west, self.south, self.east, self.north, self.whole_btn, self.test_plots,
                       self.verify):
            widget.setEnabled(not results)
        self.whole_btn.setEnabled(kind == "layers" and bool(self.info.get("bounds")))
        if results:
            self.verify.setChecked(bool(self.info.get("verified")))
            self.verify_note.setText("Decided when the folder was built: "
                                     + ("it was built with --verify." if self.info.get("verified") else
                                        "it was built without --verify."))
            self.test_plots_note.setText("the folder's own test plots are used" if self.info.get("test_plots")
                                         else "the folder has none")
        else:
            self.verify_note.setText("The evaluation's fidelity numbers need it. Step 1 takes longer.")
            self.test_plots_note.setText("made-up plots, checked after the upload")

        name = self.name.text().strip()
        if path and (not name or name == self.auto_name):  # follow the folder until a name is typed
            self.auto_name = os.path.basename(os.path.normpath(path))[:common.MAP_NAME_MAX]
            self.name.setText(self.auto_name)
        if kind == "layers" and self.info.get("bounds") and self.west.value() == self.east.value():
            self.use_whole_area(only_if_fits=True)
        self.validate()

    def use_whole_area(self, only_if_fits=False):
        bounds = self.info.get("bounds")
        if not bounds:
            return
        if only_if_fits and self.area_check(bounds)[0]:
            return
        for spin, value in zip((self.west, self.south, self.east, self.north), bounds):
            spin.setValue(round(value, 4))

    def bbox(self):
        return (self.west.value(), self.south.value(), self.east.value(), self.north.value())

    def area_check(self, box=None) -> tuple[str, str]:
        """(why the area can't be used or "", its size in words)."""
        w, s, e, n = box or self.bbox()
        if e <= w or n <= s:
            return "Choose an area: east must be more than west, and north more than south.", ""
        res = self.info.get("res_deg") or (10 / 111320.0)
        width, height = round((e - w) / res), round((n - s) / res)
        pixels = width * height
        note = (f"About {width:,} × {height:,} pixels"
                + (f" ({pixels / 1e6:,.1f} million)" if pixels >= 1e5 else "") + ", at the layers' pixel size.")
        bounds = self.info.get("bounds")
        if bounds and (e <= bounds[0] or w >= bounds[2] or n <= bounds[1] or s >= bounds[3]):
            return "This area is outside the GFC2020 files: choose an area they cover, or use the whole area.", note
        return maps.size_problem(width, height), note

    def validate(self, *_):
        """Check every field, mark the ones that are wrong, and say what (if anything) stops the ingest."""
        if not hasattr(self, "start_btn"):
            return
        errors, warnings = [], []
        kind = self.info["kind"]
        folder = self.folder.text().strip()
        if not folder:
            errors.append("Choose the data folder.")
        elif self.info.get("problem"):
            errors.append(self.info["problem"])
        if self.info.get("warning"):
            warnings.append(self.info["warning"])
        set_prop(self.folder, "state", "error" if folder and self.info.get("problem") else "")

        name = self.name.text().strip()
        problem = name_problem(name, self.existing or []) if name else ""
        if not name:
            errors.append("Give the map a name.")
            self.name_note.setText("The name shown in the app's Map list.")
            set_prop(self.name_note, "role", "note")
        elif problem:
            errors.append(problem)
            self.name_note.setText(problem)
            set_prop(self.name_note, "role", "error")
        else:
            self.name_note.setText(f"Stored in Neon as the schema {common.map_schema(name)}.")
            set_prop(self.name_note, "role", "note")
        set_prop(self.name, "state", "error" if problem else "")

        if kind == "layers":
            self.area_intro.setText("Longitude / latitude (WGS 84). Only this box is read from the layers.")
            problem, note = self.area_check()
            self.area_note.setText(problem or note)
            set_prop(self.area_note, "role", "error" if problem else "note")
            if problem:
                errors.append(problem)
        elif kind == "results":
            w, h = self.info["size"]
            self.area_intro.setText(f"The results folder's own area: {w:,} × {h:,} pixels.")
            self.area_note.setText("")
        else:
            self.area_intro.setText("Longitude / latitude (WGS 84), filled in once a folder is chosen.")
            self.area_note.setText("")

        plots = self.plots.text().strip()
        plots_problem = f"Can't find the plots file {plots}." if plots and not os.path.isfile(plots) else ""
        self.plots_note.setText(plots_problem)
        self.plots_note.setVisible(bool(plots_problem))
        set_prop(self.plots, "state", "error" if plots_problem else "")
        if plots_problem:
            errors.append(plots_problem)

        if self.existing is None:
            errors.append(f"Can't reach Neon: {self.neon_error} Check the connection and press Retry."
                          if self.neon_error else "Connecting to Neon (to check the name isn't taken)…")

        if errors:
            tone, text = "error", errors[0]
        elif warnings:
            tone, text = "warn", "  ".join(warnings)
        else:
            tone = "ok"
            text = ("Ready: upload, then check the plots." if kind == "results" else
                    "Ready: compress, upload, then check the plots.")
        set_mark(self.status_mark, tone)
        self.status.setText(text)
        set_prop(self.status, "role", {"error": "error", "warn": "warning", "ok": "secondary"}[tone])
        self.start_btn.setEnabled(not errors)

    # ----- screen 2: working -----
    def build_working(self):
        page = self.page()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(14)
        head = QHBoxLayout()
        self.work_title = make_label("", "title")
        self.work_sub = make_label("", "secondary")
        head.addLayout(self.titles(self.work_title, self.work_sub), 1)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setProperty("role", "danger")
        self.cancel_btn.clicked.connect(self.cancel)
        head.addWidget(self.cancel_btn, 0, Qt.AlignTop)
        lay.addLayout(head)

        steps, slay = card((18, 6, 18, 6), 0)
        self.stage_rows = []
        for i, (title, desc) in enumerate(STAGES):
            if i:
                slay.addWidget(rule())
            holder = QWidget()
            holder.setObjectName("clear")
            row = QHBoxLayout(holder)
            row.setContentsMargins(0, 12, 0, 12)
            row.setSpacing(14)
            number = badge(str(i + 1), 28)
            text = QVBoxLayout()
            text.setSpacing(3)
            title_label = make_label(title, "heading", wrap=False)
            sub = Elided(desc, "rowsub", Qt.ElideRight)
            bar = QProgressBar()
            bar.setTextVisible(False)
            bar.setFixedHeight(6)
            bar.hide()
            text.addWidget(title_label)
            text.addWidget(sub)
            text.addSpacing(2)
            text.addWidget(bar)
            clock = make_label("", "muted", wrap=False)
            row.addWidget(number, 0, Qt.AlignTop)
            row.addLayout(text, 1)
            row.addWidget(clock, 0, Qt.AlignTop)
            slay.addWidget(holder)
            self.stage_rows.append({"badge": number, "title": title_label, "sub": sub, "bar": bar, "clock": clock})
        lay.addWidget(steps)

        log_card, llay = card((18, 14, 18, 18), 8)
        row = QHBoxLayout()
        row.addWidget(make_label("Output of pipeline.py", "heading", wrap=False))
        row.addStretch(1)
        self.copy_cmd_btn = link_button("Copy the command", self.copy_run_command)
        row.addWidget(self.copy_cmd_btn)
        llay.addLayout(row)
        self.command = Elided("", "muted", Qt.ElideRight)
        self.command.setFont(QFont("Consolas", 9))
        llay.addWidget(self.command)
        self.log = QPlainTextEdit()
        self.log.setObjectName("log")
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(20000)
        llay.addWidget(self.log, 1)
        lay.addWidget(log_card, 1)
        return page

    def set_stage(self, index, state, text=None):
        """state: waiting, running, done, skipped, failed."""
        row, run = self.stage_rows[index], self.run_state
        row["badge"].setText({"done": "✔", "skipped": "–", "failed": "✖"}.get(state, str(index + 1)))
        set_prop(row["badge"], "tone", {"waiting": "", "running": "run", "done": "ok", "skipped": "skip",
                                        "failed": "error"}[state])
        set_prop(row["title"], "dim", state in ("waiting", "skipped"))
        row["bar"].setVisible(state == "running")
        if state == "running":
            row["bar"].setRange(0, 0)
            run["stage_start"][index] = time.time()
        if state == "done" and run["stage_start"][index]:
            row["clock"].setText(duration(time.time() - run["stage_start"][index]))
        if state in ("waiting", "skipped"):
            row["clock"].setText("")
        default = {"waiting": STAGES[index][1], "running": "Starting…", "done": "Done", "skipped": "Skipped",
                   "failed": "Stopped here"}[state]
        set_prop(row["sub"], "tone", {"done": "", "failed": "error"}.get(state, ""))
        row["sub"].setText(text or default)
        run["stages"][index] = state
        if state == "running":
            run["current"] = index

    def pipeline_args(self):
        name = self.name.text().strip()
        args = ["--name", name]
        if self.info["kind"] == "results":
            args += ["--out-dir", os.path.normpath(self.folder.text().strip()), "--skip-build"]
        else:
            args += ["--data-dir", os.path.normpath(self.folder.text().strip()),
                     "--bbox", *(f"{v:.6f}" for v in self.bbox())]
            if self.test_plots.value():
                args += ["--test-plots", str(self.test_plots.value())]
            if self.verify.isChecked():
                args.append("--verify")
        if not self.outlines.isChecked():
            args.append("--no-outlines")
        if self.plots.text().strip():
            args += ["--plots", os.path.normpath(self.plots.text().strip())]
        return args

    def run_command(self):
        shown = " ".join(f'"{a}"' if " " in a else a for a in self.run_state["args"])
        return f"venv\\Scripts\\python pipeline.py {shown}"

    def start(self):
        self.validate()
        if not self.start_btn.isEnabled():
            return
        name = self.name.text().strip()
        args = self.pipeline_args()
        results = self.info["kind"] == "results"
        out = (os.path.normpath(self.folder.text().strip()) if results
               else os.path.join(common.LAYERS_DIR, common.map_schema(name)[len("map_"):]))
        self.run_state = {"name": name, "args": args, "out": out, "start": time.time(), "results": results,
                          "stages": ["waiting"] * 3, "stage_start": [None] * 3, "current": None,
                          "same": None, "cancelled": False, "lines": [], "buffer": "",
                          "plots": bool(self.plots.text().strip() or self.test_plots.value() or results)}
        self.work_title.setText(f"Ingesting '{name}'")
        self.command.setText(self.run_command())
        for i in range(3):
            self.set_stage(i, "waiting")
        if results:
            self.set_stage(0, "skipped", "Skipped: the results folder is compressed already")
        self.log.clear()
        self.cancel_btn.setEnabled(True)
        self.stack.setCurrentIndex(1)
        self.timer.start(1000)
        self.tick()

        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.MergedChannels)
        self.proc.setWorkingDirectory(BACKEND_DIR)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")
        env.insert("PYTHONIOENCODING", "utf-8")
        self.proc.setProcessEnvironment(env)
        self.proc.readyReadStandardOutput.connect(self.read_output)
        self.proc.finished.connect(self.process_finished)
        self.proc.errorOccurred.connect(self.process_error)
        self.proc.start(sys.executable, ["-u", PIPELINE, *args])

    def tick(self):
        run = self.run_state
        if not run:
            return
        self.work_sub.setText(f"{duration(time.time() - run['start'])} so far. You can leave this window open; "
                              "it says when it's done.")
        current = run.get("current")
        if current is not None and run["stages"][current] == "running" and run["stage_start"][current]:
            self.stage_rows[current]["clock"].setText(duration(time.time() - run["stage_start"][current]))

    def read_output(self):
        text = bytes(self.proc.readAllStandardOutput()).decode("utf-8", "replace")
        buffer = self.run_state["buffer"] + text.replace("\r\n", "\n").replace("\r", "\n")
        *lines, self.run_state["buffer"] = buffer.split("\n")
        for line in lines:
            self.handle_line(line)

    def handle_line(self, line):
        self.log.appendPlainText(line)
        run = self.run_state
        if line.strip():
            run["lines"] = (run["lines"] + [line])[-40:]
        step = STEP_RE.match(line)
        if step:
            current = int(step.group(1)) - 1
            for i in range(current):
                if run["stages"][i] == "running":
                    self.set_stage(i, "done")
                elif run["stages"][i] == "waiting":
                    self.set_stage(i, "skipped")
            self.set_stage(current, "running")
            return
        index = run.get("current")
        percent = PERCENT_RE.match(line)
        if percent and index is not None:
            bar = self.stage_rows[index]["bar"]
            bar.setRange(0, 1000)
            bar.setValue(int(10 * float(percent.group(2))))
            self.stage_rows[index]["sub"].setText(
                f"{percent.group(1).strip().capitalize()} · {float(percent.group(2)):.0f}%")
            return
        same = SAME_RE.search(line)
        if same:
            run["same"] = (int(same.group(1)), int(same.group(2)))
        if line.strip() and index is not None and run["stages"][index] == "running":
            self.stage_rows[index]["sub"].setText(line.strip())

    def copy_run_command(self):
        QGuiApplication.clipboard().setText(self.run_command())
        self.copy_cmd_btn.setText("Copied")
        QTimer.singleShot(2000, lambda: self.copy_cmd_btn.setText("Copy the command"))

    def cancel(self):
        if self.proc is None or self.proc.state() == QProcess.NotRunning:
            return
        answer = QMessageBox.question(
            self, "Cancel the ingest?",
            "Stop now? An upload to Neon is one transaction, so nothing half-done is saved there. "
            "A compression step that is stopped leaves an incomplete folder in backend/layers/, "
            "which is rebuilt next time.")
        if answer == QMessageBox.Yes and self.proc.state() != QProcess.NotRunning:
            self.run_state["cancelled"] = True
            self.cancel_btn.setEnabled(False)
            self.work_sub.setText("Cancelling…")
            self.proc.kill()

    def process_error(self, error):
        if error == QProcess.FailedToStart:
            self.timer.stop()
            self.show_done("error", "The ingest could not start",
                           f"Python could not run pipeline.py ({self.proc.errorString()}).")

    def process_finished(self, code, status):
        run = self.run_state
        if run["buffer"]:
            self.handle_line(run["buffer"])
            run["buffer"] = ""
        self.timer.stop()
        took = duration(time.time() - run["start"])
        run["took"] = took
        stages = run["stages"]
        if run["cancelled"]:
            self.stop_stages()
            self.show_done("warn", "Cancelled", f"Stopped after {took}. Nothing half-done was saved to Neon.")
            return
        same = run["same"]
        uploaded = stages[2] != "waiting" or code == 0
        if code == 0 and status == QProcess.NormalExit:
            for i, state in enumerate(stages):
                if state == "running":
                    self.set_stage(i, "done")
                elif state == "waiting":
                    self.set_stage(i, "skipped", "Skipped: no plots to check" if i == 2 else None)
            self.show_done("ok", f"'{run['name']}' is in Neon", f"Finished in {took}.")
        elif same is not None and uploaded and same[0] != same[1]:
            self.stop_stages()
            self.show_done("warn", f"'{run['name']}' is in Neon, but some plots differ",
                           f"{same[1] - same[0]:,} of {same[1]:,} plots got a different answer from Neon than "
                           "from evidence.tif. The log has the details, and the CSV is in database/results/.")
        else:
            self.stop_stages()
            self.show_done("error", "The ingest stopped",
                           f"After {took}. Nothing half-done was saved to Neon. The end of the log below says why.")

    def stop_stages(self):
        for i, state in enumerate(self.run_state["stages"]):
            if state == "running":
                self.set_stage(i, "failed")

    # ----- screen 3: done -----
    def build_done(self):
        page = self.page()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        body = QVBoxLayout()
        body.setContentsMargins(24, 20, 24, 16)
        body.setSpacing(14)
        outer.addLayout(body, 1)

        self.banner = QFrame()
        self.banner.setObjectName("banner")
        row = QHBoxLayout(self.banner)
        row.setContentsMargins(18, 16, 18, 16)
        row.setSpacing(16)
        self.done_icon = make_label("", "bigmark", wrap=False)
        self.done_icon.setAlignment(Qt.AlignCenter)
        self.done_icon.setFixedSize(48, 48)
        row.addWidget(self.done_icon, 0, Qt.AlignTop)
        self.done_title = make_label("", "title")
        self.done_text = make_label("", "secondary")
        self.done_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        row.addLayout(self.titles(self.done_title, self.done_text), 1)
        body.addWidget(self.banner)

        self.done_stack = QStackedWidget()
        self.done_stack.addWidget(self.build_summary())
        log_card, llay = card((18, 14, 18, 18), 8)
        llay.addWidget(make_label("Output of pipeline.py", "heading", wrap=False))
        self.done_log = QPlainTextEdit()
        self.done_log.setObjectName("log")
        self.done_log.setReadOnly(True)
        llay.addWidget(self.done_log, 1)
        self.done_stack.addWidget(log_card)
        body.addWidget(self.done_stack, 1)

        self.log_toggle = link_button("Show the log", self.toggle_log)
        self.copy_log_btn = QPushButton("Copy the log")
        self.copy_log_btn.clicked.connect(self.copy_log)
        bar, row = self.footer(self.log_toggle, self.copy_log_btn)
        row.addStretch(1)
        self.back_btn = QPushButton("Back to set-up")
        self.back_btn.clicked.connect(self.back)
        self.again_btn = QPushButton("Ingest another map")
        self.again_btn.clicked.connect(self.again)
        close = QPushButton("Close")
        close.setProperty("role", "primary")
        close.clicked.connect(self.close)
        for widget in (self.back_btn, self.again_btn, close):
            row.addWidget(widget)
        outer.addWidget(bar)
        return page

    def build_summary(self):
        page = QWidget()
        page.setObjectName("clear")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)

        stats = QHBoxLayout()
        stats.setSpacing(12)
        self.stat_values = {}
        for key, caption in (("size", "stored in Neon"), ("pixels", "pixels"), ("plots", "plots with exactly "
                                                                                 "eudr_layers.py's answers"),
                             ("time", "to ingest")):
            frame, flay = card((16, 12, 16, 12), 2)
            value = make_label("…", "stat", wrap=False)
            flay.addWidget(value)
            flay.addWidget(make_label(caption, "muted"))
            stats.addWidget(frame, 1)
            self.stat_values[key] = value
        lay.addLayout(stats)

        columns = QHBoxLayout()
        columns.setSpacing(14)
        tables_card, tlay = card((18, 14, 18, 14), 8)
        self.tables_title = make_label("What is stored", "heading")
        tlay.addWidget(self.tables_title)
        self.tables = QTableWidget(0, 4)
        self.tables.setHorizontalHeaderLabels(["Table", "Holds", "Rows", "Size"])
        self.tables.verticalHeader().hide()
        self.tables.setShowGrid(False)
        self.tables.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tables.setSelectionMode(QAbstractItemView.NoSelection)
        self.tables.setFocusPolicy(Qt.NoFocus)
        header = self.tables.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        tlay.addWidget(self.tables, 1)
        self.tables_note = make_label("", "muted")
        tlay.addWidget(self.tables_note)
        columns.addWidget(tables_card, 3)

        next_card, nlay = card((18, 14, 18, 14), 8)
        nlay.addWidget(make_label("Next: the evaluation", "heading"))
        nlay.addWidget(make_label("Storage, query speed, and decision and pixel fidelity against the original "
                                  "layers. Run it from the backend folder:", "secondary"))
        self.eval_command = make_label("", "code")
        self.eval_command.setTextInteractionFlags(Qt.TextSelectableByMouse)
        nlay.addWidget(self.eval_command)
        row = QHBoxLayout()
        self.copy_btn = QPushButton("Copy the command")
        self.copy_btn.clicked.connect(self.copy_command)
        self.open_btn = QPushButton("Open the results folder")
        self.open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.run_state["out"])))
        row.addWidget(self.copy_btn)
        row.addWidget(self.open_btn)
        row.addStretch(1)
        nlay.addLayout(row)
        nlay.addSpacing(6)
        nlay.addWidget(rule())
        nlay.addSpacing(2)
        self.app_note = make_label("", "secondary")
        nlay.addWidget(self.app_note)
        nlay.addStretch(1)
        columns.addWidget(next_card, 2)
        lay.addLayout(columns, 1)
        return page

    def evaluation_command(self):
        out = self.run_state["out"]
        try:
            folder = os.path.relpath(out, BACKEND_DIR)
        except ValueError:  # another drive
            folder = out
        return f'venv\\Scripts\\python compare\\compare.py "{folder}" --map "{self.run_state["name"]}" --repeat 5'

    def show_done(self, tone, title, text):
        set_prop(self.banner, "tone", tone)
        self.done_icon.setText({"ok": "✔", "warn": "!", "error": "✖"}[tone])
        set_prop(self.done_icon, "tone", tone)
        self.done_title.setText(title)
        self.done_text.setText(text)
        self.done_log.setPlainText(self.log.toPlainText())
        self.done_log.verticalScrollBar().setValue(self.done_log.verticalScrollBar().maximum())
        stored = tone == "ok" or "is in Neon" in title
        self.log_toggle.setVisible(stored)
        self.copy_log_btn.setVisible(not stored)
        self.back_btn.setVisible(not stored)
        self.again_btn.setVisible(stored)
        self.show_log(not stored)
        if stored:
            self.fill_summary()
        self.stack.setCurrentIndex(2)

    def show_log(self, on):
        self.done_stack.setCurrentIndex(1 if on else 0)
        self.log_toggle.setText("Show the summary" if on else "Show the log")

    def toggle_log(self):
        self.show_log(self.done_stack.currentIndex() == 0)

    def fill_summary(self):
        run = self.run_state
        self.stat_values["size"].setText("…")
        self.stat_values["pixels"].setText("…")
        self.stat_values["time"].setText(run.get("took", "–"))
        same = run["same"]
        self.stat_values["plots"].setText(f"{same[0]:,} / {same[1]:,}" if same else "–")
        self.stat_values["plots"].setStyleSheet("" if not same else
                                                f"color: {'#1b7837' if same[0] == same[1] else '#b42318'};")
        self.tables.setRowCount(0)
        self.tables_title.setText("What is stored")
        self.tables_note.setText("Reading what is stored in Neon…")
        self.eval_command.setText(self.evaluation_command())
        self.open_btn.setVisible(os.path.isdir(run.get("out", "")))
        self.app_note.setText(f"Then open the app and choose '{run['name']}' in the Map list:\n"
                              "frontend\\.venv\\Scripts\\python frontend\\app.py")
        if not same and not run["plots"]:
            self.stat_values["plots"].setToolTip("No plots were checked (no test plots and no plots file).")
        name = run["name"]
        self.run_task(lambda: map_summary(name), self.show_summary,
                      lambda message: self.tables_note.setText(f"Could not read the map back from Neon: {message}"))

    def show_summary(self, info):
        tables = info["tables"]
        self.stat_values["size"].setText(megabytes(info["total"]))
        self.stat_values["pixels"].setText(f"{info['width']:,} × {info['height']:,}")
        self.tables_title.setText(f"What is stored, in the schema {info['schema']}")
        ordered = sorted(tables.items(), key=lambda kv: -kv[1][0])
        self.tables.setRowCount(len(ordered) + 1)
        bold = QFont()
        bold.setBold(True)
        for r, (table, (size, rows)) in enumerate(ordered + [("total", (info["total"], None))]):
            cells = (table, TABLE_NOTES.get(table, ""), "" if rows is None else f"{rows:,}", megabytes(size))
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setTextAlignment((Qt.AlignRight if c >= 2 else Qt.AlignLeft) | Qt.AlignVCenter)
                if c == 0:
                    item.setFont(QFont("Consolas", 9))
                if table == "total":
                    item.setFont(bold)
                self.tables.setItem(r, c, item)
        self.tables.resizeRowsToContents()
        built = [a for a, t in (("evidence pages", "evidence_pages"), ("stencils", "stencil_pages"),
                                ("outlines", "class_polys")) if t in tables]
        self.tables_note.setText(f"Approaches stored: {', '.join(built) or 'none'}. Sizes include the indexes.")

    def copy_command(self):
        QGuiApplication.clipboard().setText(self.evaluation_command())
        self.copy_btn.setText("Copied")
        QTimer.singleShot(2000, lambda: self.copy_btn.setText("Copy the command"))

    def copy_log(self):
        QGuiApplication.clipboard().setText(self.log.toPlainText())
        self.copy_log_btn.setText("Copied")
        QTimer.singleShot(2000, lambda: self.copy_log_btn.setText("Copy the log"))

    def back(self):
        """To the set-up screen with everything as it was (after a failure or a cancel)."""
        self.stack.setCurrentIndex(0)
        self.list_maps()

    def again(self):
        """To an empty set-up screen for the next map (the options stay)."""
        self.auto_name = ""
        for field in (self.folder, self.name, self.plots):
            field.clear()
        for spin in (self.west, self.south, self.east, self.north):
            spin.setValue(0)
        self.stack.setCurrentIndex(0)
        self.list_maps()

    def closeEvent(self, event):  # noqa: N802 - Qt's name
        if self.proc is not None and self.proc.state() != QProcess.NotRunning:
            answer = QMessageBox.question(self, "Stop the ingest?",
                                          "An ingest is running. Stop it and close? Nothing half-done is "
                                          "saved to Neon.")
            if answer != QMessageBox.Yes:
                event.ignore()
                return
            self.run_state["cancelled"] = True
            self.proc.kill()
            self.proc.waitForFinished(5000)
        event.accept()


def main() -> int:
    if hasattr(Qt, "AA_EnableHighDpiScaling"):
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    window = IngestWindow()
    screen = app.primaryScreen().availableGeometry()
    window.resize(min(1120, screen.width() - 40), min(900, screen.height() - 60))
    window.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
