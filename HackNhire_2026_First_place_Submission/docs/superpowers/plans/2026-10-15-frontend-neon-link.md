# Frontend ↔ Neon link: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect the PyQt map app to the EUDR map in Neon: colour-coded map, checking points and outlines with both storage approaches (zoom-in verdict card with highlighted problem areas), statistics with charts, and live diagrams.

**Architecture:** The app imports the backend's own modules (`backend/database`) through `frontend/backend.py` and talks to Neon on one background thread (`worker.py` → `neon_service.py`). Map tiles are drawn in Python from the stencil pages (downloaded once) and served to Leaflet at `eudr://tiles/...`.

**Everything except the map itself is native Qt:**
- the results panel, Inspect diagram, legend, banner and verdict card (the last three float over the map);
- the Statistics tab, with charts drawn with QPainter (`charts.py`) and diagrams shown with QSvgWidget.

The map stays the Leaflet view inside the window, as in today's app. It talks to Python through QWebChannel (`pages.py`). Each feature lives in a small controller module that `app.py` creates.

**Tech Stack:** Python 3.13 venv (code kept 3.10-compatible), PyQt5 5.15 + QtWebEngine (Chromium 83), Leaflet 1.9.4 + leaflet.draw 1.0.4 (cdnjs), numpy, shapely, pyproj, rasterio, psycopg 3, Neon PostgreSQL 18 + PostGIS 3.6. Tests: standard-library `unittest`.

**Spec:** `docs/superpowers/specs/2026-10-15-frontend-neon-link-design.md`

## Global Constraints

- **No file under `backend/` changes.** The app calls the backend's functions; its rules in `backend/CLAUDE.md` stay intact.
- **Packages:** PyQt5, PyQtWebEngine and `backend/requirements.txt` only (numpy, rasterio, shapely, pyproj, psycopg[binary]). `pyflakes` is a lint tool only. Tests use `unittest`. Charts are QPainter widgets in `charts.py`, and diagrams are SVG files shown with `QSvgWidget` (QtSvg ships with PyQt5).
- **PyQt only (your decision):**
  - Every new piece of UI is a Qt widget: tables, cards, legend, banner, charts, diagrams.
  - The only web content is the Leaflet map, which was already there. Map JavaScript draws only map layers (farms, tiles, highlights, outlines), never panels or controls.
- **Map JavaScript must run in Qt WebEngine 5.15 (Chromium 83):** ES2019 only. No `??=`, `||=`, `&&=`, `replaceAll`, `.at()`, `Object.hasOwn`, or private class fields.
- **Neon:** connect only through `common.connect()` (it sets `prepare_threshold=None`). End every transaction (`NeonService._run`). Use one batched query per job, never one query per farm.
- **Secrets:** never print or log `DATABASE_URL` or anything from `backend/.env`.
- **Text:** plain English with British spelling (colour, metres). Error messages say how to fix the problem.
- **Colours:**
  - **The map** uses the TIFF's legend: land `#E1E1E1`, water `#6BAED6`, forest `#1B7837`, deforestation `#E31A1C`, needs review `#6A3D9A`, no data `#000000`.
  - **Charts** use the checked set: deforestation `#e34948`, needs review `#6250d6`, forest `#008300`, water `#2a78d6`, no data `#52514e`, land `#c3c2b7`. Stacks go in that order.
  - **Methods:** stencils `#eb6834`, outlines `#1baf7a`, TIFF pixel rule `#898781`, TIFF exact area `#52514e`.
  - **Verdicts** use status colours and always come with an icon and a label: FAIL `#d03b3b` ✖, MANUAL_REVIEW `#ec835a` ⚠, CANNOT_CHECK `#898781` ?, CHECK_LOCATION `#fab219` ⚠, PASS `#0ca30c` ✔.
- **Data text** (farm names, plot ids, messages): in the map JavaScript, use `esc()` or `textContent`, never raw `innerHTML`. In Qt, show it as plain text (`Qt.PlainText` on labels that contain data).
- **Timings** are labelled "over Neon" and benchmarks report the median of 3 (`backend/CLAUDE.md`).
- **Commands** are run from the project root `HackNHire\` in PowerShell, with `frontend\.venv\Scripts\python`.
- **No git yet:** this folder is not a git repository, so each task ends with a checkpoint. If `git init` was agreed, commit at each checkpoint with the message shown.

## Review Focus

Most likely failure modes that the spec implies but no feature test would naturally hit. Each has a pinned test in the task named.

1. **A farm partly or wholly outside the map, in the focus view.** The verdict is `CANNOT_CHECK`, the outside part is hatched, and nothing crashes. *Test: `test_parts.py::test_farm_outside_the_map` (Task 11).*
2. **The map's most common colour is a problem colour** (`default_class` is not land). The default colour's part is still highlighted. *Test: `test_parts.py::test_default_colour_part_is_farm_minus_stored_parts` (Task 11).*
3. **A farm edited after its check.** Its old verdict must show as out of date, never as current. *Test: `test_checks_ui.py::test_edited_and_deleted_farms` (Task 9).*
4. **A point exactly on the map's east or south edge.** It is outside in both the tiles and the checks (floor rule). *Test: `test_tiles.py::test_map_edges_follow_the_floor_rule` (Task 2).*
5. **Cancelling the pixel-fidelity check part-way.** `common.Progress` must be restored, or later verifies misbehave. *Test: `test_stats_service.py::test_progress_hook_restores_on_cancel` (Task 14).*

## File structure

| File | Responsibility | Task |
|---|---|---|
| `frontend/requirements.txt` | adds `-r ../backend/requirements.txt` | 1 |
| `frontend/backend.py` | puts `backend/database` on `sys.path`; re-exports `common`, `polygon_approach`, `stencil_approach` | 1 |
| `frontend/legend.py` | class labels, map/chart/method/verdict colours, `page_config()` | 1 |
| `frontend/tiles.py` | tile maths, palette PNG encoder, `eudr://` scheme handler | 2 |
| `frontend/neon_service.py` | `NeonService`: every Neon/backend call. Later tasks append methods at the end of the class (it is the last thing in the file) | 3, 8, 11, 14, 16 |
| `frontend/worker.py` | background `QThread` job queue | 4 |
| `frontend/pages.py` | `WebPage` loads the map page (`web/map.html` + includes), queues calls until ready, prints JS errors; `Bridge` | 5 |
| `frontend/web/map.html`, `web/map.js` | the Leaflet map and core farm drawing (polygons and points, verdict styling) | 5 |
| `frontend/map_area.py` | `MapArea`: the map view with native Qt widgets floating over it (`overlay_position`) | 5 |
| `frontend/app.py` | main window: toolbar, tabs, worker and job routing, events between controllers | 5 (rewrite), small edits later |
| `frontend/eudr_layer.py`, `web/map_eudr.js` | the colour-coded tile layer (JS); native legend and loading/error banner; opacity | 6 |
| `frontend/report.py` | `PlotResult` → JSON rows, summaries, verdict card text | 7 |
| `frontend/farms.py` | map/file features → backend `Farm`s | 8 |
| `frontend/checks_ui.py` | Point tool, Open ▾, Check ▾, results state, stale detection | 9 |
| `frontend/charts.py` | native QPainter charts: chart card with legend and "Show table", stacked bars (Task 10); bars, grouped bars, lines, stat tiles (Task 15) | 10, 15 |
| `frontend/results_panel.py` | right panel (Qt): Results table, selected farm's hectares chart, Inspect tab host | 10 |
| `frontend/parts.py` | focus geometry: parts per colour, outside part, pixel overlay | 11 |
| `frontend/focus.py`, `web/map_focus.js` | focus view: native verdict card over the map; zoom, dim and highlights on the map; toggle, Next problem | 12 |
| `frontend/farmgen.py` | seeded benchmark farms | 13 |
| `frontend/stats_service.py` | progress hook for verify, decision-fidelity counting | 14 |
| `frontend/stats_tab.py` | Statistics tab (Qt): test results, storage, benchmark, fidelity, How it works | 15, 18 |
| `frontend/spot.py` | pure helpers for Inspect: page geometry, stencil bits, labels | 16 |
| `frontend/inspect_view.py`, `frontend/inspect_tool.py`, `web/map_inspect.js` | Inspect diagram (QPainter), Inspect tool, stored-outlines layer | 17 |
| `frontend/diagrams/data_flow.svg`, `diagrams/encodings.svg` | How it works pictures (static SVG, Tiny 1.2 features only) | 18 |
| `frontend/smoke_neon.py` | read-only end-to-end check against Neon | 19 |
| `frontend/README.md` | how to install, run and test | 19 |
| `frontend/tests/` | `unittest` tests (`__init__.py` makes it a package) | all |

**Run all tests:** `frontend\.venv\Scripts\python -m unittest discover -s frontend/tests -t frontend -v`
**Run one file:** add `-p test_<name>.py`.

---

## Phase 1: colour-coded map

### Task 1: Environment, backend import and legend

**Files:**
- Modify: `frontend/requirements.txt`
- Create: `frontend/backend.py`, `frontend/legend.py`, `frontend/tests/__init__.py`, `frontend/tests/test_legend.py`

**Interfaces:**
- Produces: `backend.common`, `backend.polygon_approach`, `backend.stencil_approach` (the backend modules). `legend.CLASSES`, `CLASS_LABELS`, `MAP_COLOURS`, `CHART_COLOURS`, `CHART_ORDER`, `PROBLEM_CLASSES`, `METHODS`, `VERDICT_STYLE`, `VERDICT_ORDER`, `LAND`…`NO_DATA`, `hex_to_rgb(str) -> (r, g, b)`, `page_config() -> dict`.

- [ ] **Step 1: Point the frontend at the backend's packages**

`frontend/requirements.txt` (whole file):

```
PyQt5>=5.15
PyQtWebEngine>=5.15
# The app imports backend/database/*.py to read the map from Neon, so it needs the backend's packages
-r ../backend/requirements.txt
```

- [ ] **Step 2: Install**

Run: `frontend\.venv\Scripts\python -m pip install -r frontend/requirements.txt pyflakes`
Then: `frontend\.venv\Scripts\python -c "import rasterio, shapely, pyproj, psycopg; print('backend packages OK')"`
Expected: `backend packages OK`

- [ ] **Step 3: Write `frontend/backend.py`**

```python
"""backend.py - makes the backend's own modules importable, the same way compare/compare.py does.

    from backend import common, polygon_approach, stencil_approach

Everything the app knows about the map and the EUDR rules comes from these modules, so the app
always gives the same answers as the command-line tools. Nothing in backend/ is changed.
"""

import os
import sys

DATABASE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "backend", "database")
if DATABASE_DIR not in sys.path:
    sys.path.insert(0, DATABASE_DIR)

import common  # noqa: E402
import polygon_approach  # noqa: E402
import stencil_approach  # noqa: E402

__all__ = ["common", "polygon_approach", "stencil_approach"]
```

- [ ] **Step 4: Write the failing test** `frontend/tests/__init__.py` (empty file) and `frontend/tests/test_legend.py`:

```python
import json
import unittest

import legend
from backend import common


class LegendTest(unittest.TestCase):
    def test_class_codes_match_the_backend(self):
        self.assertEqual(legend.CLASSES, common.COLOURS)
        for code in legend.CLASSES:
            for table in (legend.CLASS_LABELS, legend.MAP_COLOURS, legend.CHART_COLOURS):
                self.assertIn(code, table)

    def test_chart_order_covers_every_class_once(self):
        self.assertEqual(sorted(legend.CHART_ORDER), list(legend.CLASSES))

    def test_verdicts_match_the_backend(self):
        self.assertEqual(set(legend.VERDICT_STYLE), set(common.VERDICTS))
        self.assertEqual(legend.VERDICT_ORDER, common.VERDICTS)

    def test_page_config_is_json_ready(self):
        config = json.loads(json.dumps(legend.page_config()))
        self.assertEqual(config["verdicts"]["FAIL"]["headline"], "Not compliant")
        self.assertEqual([c["code"] for c in config["classes"]], list(legend.CLASSES))
        self.assertEqual(config["methods"]["polygons"]["label"], "Outlines")

    def test_hex_to_rgb(self):
        self.assertEqual(legend.hex_to_rgb("#E31A1C"), (227, 26, 28))
```

- [ ] **Step 5: Run it and see it fail**

Run: `frontend\.venv\Scripts\python -m unittest discover -s frontend/tests -t frontend -p test_legend.py -v`
Expected: ERROR, `ModuleNotFoundError: No module named 'legend'`

- [ ] **Step 6: Write `frontend/legend.py`**

```python
"""legend.py - the colours and words the app uses for map classes, methods and verdicts.

Two palettes, on purpose:
  MAP_COLOURS    the legend embedded in my_map.tif - the data's own colours, used on the map
  CHART_COLOURS  colour-blind-checked colours of the same families, used in charts. Checked with
                 the dataviz validator; stacked bars must keep CHART_ORDER (it passes every check)
Verdicts use fixed status colours and always come with an icon and a label.
"""

LAND, WATER, FOREST, DEFORESTATION, REVIEW, NO_DATA = 0, 1, 2, 3, 4, 5  # as in common.py
CLASSES = (LAND, WATER, FOREST, DEFORESTATION, REVIEW, NO_DATA)

CLASS_LABELS = {LAND: "Land", WATER: "Water", FOREST: "Forest", DEFORESTATION: "Deforestation",
                REVIEW: "Needs review", NO_DATA: "No data"}

MAP_COLOURS = {LAND: "#E1E1E1", WATER: "#6BAED6", FOREST: "#1B7837", DEFORESTATION: "#E31A1C",
               REVIEW: "#6A3D9A", NO_DATA: "#000000"}

CHART_COLOURS = {DEFORESTATION: "#e34948", REVIEW: "#6250d6", FOREST: "#008300", WATER: "#2a78d6",
                 NO_DATA: "#52514e", LAND: "#c3c2b7"}
CHART_ORDER = (DEFORESTATION, REVIEW, FOREST, WATER, NO_DATA, LAND)

PROBLEM_CLASSES = (DEFORESTATION, REVIEW, WATER, NO_DATA)  # highlighted in the focus view

METHODS = {  # key -> (label, chart colour)
    "stencils": ("Stencils", "#eb6834"),
    "polygons": ("Outlines", "#1baf7a"),
    "tiff_pixel": ("TIFF, pixel rule", "#898781"),
    "tiff_exact": ("TIFF, exact area", "#52514e"),
}

VERDICT_ORDER = ("FAIL", "MANUAL_REVIEW", "CANNOT_CHECK", "CHECK_LOCATION", "PASS")  # worst first
VERDICT_STYLE = {  # verdict -> (status colour, icon, headline)
    "FAIL": ("#d03b3b", "✖", "Not compliant"),
    "MANUAL_REVIEW": ("#ec835a", "⚠", "Needs review"),
    "CANNOT_CHECK": ("#898781", "?", "Cannot check"),
    "CHECK_LOCATION": ("#fab219", "⚠", "Check the location"),
    "PASS": ("#0ca30c", "✔", "Compliant"),
}


def hex_to_rgb(colour):
    colour = colour.lstrip("#")
    return tuple(int(colour[i:i + 2], 16) for i in (0, 2, 4))


def page_config():
    """What the web pages need to draw legends, charts and verdicts (JSON-ready)."""
    return {
        "classes": [{"code": c, "label": CLASS_LABELS[c], "map": MAP_COLOURS[c], "chart": CHART_COLOURS[c]}
                    for c in CLASSES],
        "chartOrder": list(CHART_ORDER),
        "problemClasses": list(PROBLEM_CLASSES),
        "methods": {key: {"label": label, "colour": colour} for key, (label, colour) in METHODS.items()},
        "verdicts": {v: {"colour": c, "icon": i, "headline": h} for v, (c, i, h) in VERDICT_STYLE.items()},
        "verdictOrder": list(VERDICT_ORDER),
    }
```

- [ ] **Step 7: Run the test and see it pass**

Run: `frontend\.venv\Scripts\python -m unittest discover -s frontend/tests -t frontend -p test_legend.py -v`
Expected: `Ran 5 tests ... OK`

- [ ] **Step 8: Checkpoint.** `git add frontend/requirements.txt frontend/backend.py frontend/legend.py frontend/tests` · `git commit -m "frontend: backend import and legend"`

---

### Task 2: Map tiles drawn from the colour array

**Files:**
- Create: `frontend/tiles.py`, `frontend/tests/test_tiles.py`

**Interfaces:**
- Consumes: `legend.CLASSES`, `legend.MAP_COLOURS`, `legend.hex_to_rgb`; `common.Grid` (fields `x0, y0, pixel_w, pixel_h, width, height`, `point_to_pixel`, `inside`).
- Produces:
  - `tiles.TILE = 256`, `tiles.TRANSPARENT = 6`, `tiles.SCHEME = b"eudr"`
  - `tile_lonlat(z, x, y) -> (lon[256], lat[256])`
  - `render_tile(classes, grid, z, x, y, to_map=None) -> uint8[256, 256]`
  - `palette(hidden=(), alpha=255) -> list[(r, g, b, a)]` (7 entries)
  - `encode_png(indices, rgba) -> bytes`
  - `parse_tile_path(path, query) -> (z, x, y, set) | None`
  - `register_scheme()`, `class TileHandler(QWebEngineUrlSchemeHandler)` with `set_map(grid, classes, to_map)`

- [ ] **Step 1: Write the failing test** `frontend/tests/test_tiles.py`:

```python
import struct
import unittest
import zlib

import numpy as np

import tiles
from backend import common


def make_grid(width=600, height=600, default=0):
    """The stored map's address system, smaller: top-left 103.0, -1.8, pixels of 1/12000 degree."""
    return common.Grid("test", 103.0, -1.8, 1 / 12000, 1 / 12000, width, height, 4326, True,
                       default_class=default, fingerprint="test")


def lonlat_to_tile(lon, lat, z):
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - np.arcsinh(np.tan(np.radians(lat))) / np.pi) / 2.0 * n
    return int(x), int(y)


def decode_png(data):
    """Our palette PNG -> (indices, rgba list), checking signature, CRCs, filter bytes."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, chunks = 8, {}
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        kind, body = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + length]
        (crc,) = struct.unpack(">I", data[pos + 8 + length:pos + 12 + length])
        assert crc == zlib.crc32(kind + body) & 0xFFFFFFFF
        chunks[kind] = body
        pos += 12 + length
    w, h, depth, colour_type = struct.unpack(">IIBB", chunks[b"IHDR"][:10])
    assert (depth, colour_type) == (8, 3)
    raw = np.frombuffer(zlib.decompress(chunks[b"IDAT"]), np.uint8).reshape(h, w + 1)
    assert not raw[:, 0].any()
    plte, trns = chunks[b"PLTE"], chunks[b"tRNS"]
    rgba = [(plte[3 * i], plte[3 * i + 1], plte[3 * i + 2], trns[i]) for i in range(len(plte) // 3)]
    return raw[:, 1:], rgba


class TileMathTest(unittest.TestCase):
    def setUp(self):
        self.grid = make_grid()
        self.classes = np.random.default_rng(1).integers(0, 5, (600, 600)).astype(np.uint8)

    def test_tile_pixels_take_the_colour_point_to_pixel_gives(self):
        z = 14
        x, y = lonlat_to_tile(103.02, -1.81, z)
        tile = tiles.render_tile(self.classes, self.grid, z, x, y)
        lon, lat = tiles.tile_lonlat(z, x, y)
        for i in (0, 37, 128, 255):
            for j in (0, 91, 200, 255):
                row, col = self.grid.point_to_pixel(float(lon[j]), float(lat[i]))
                expected = self.classes[row, col] if self.grid.inside(row, col) else tiles.TRANSPARENT
                self.assertEqual(tile[i, j], expected)

    def test_off_the_map_is_transparent(self):
        x, y = lonlat_to_tile(102.5, -1.5, 12)
        tile = tiles.render_tile(self.classes, self.grid, 12, x, y)
        self.assertTrue((tile == tiles.TRANSPARENT).all())

    def test_map_edges_follow_the_floor_rule(self):
        """Review focus 4: the east edge itself is outside the map, just west of it is inside."""
        east = 103.0 + self.grid.width / 12000
        self.assertFalse(self.grid.inside(*self.grid.point_to_pixel(east, -1.81)))
        x, y = lonlat_to_tile(east, -1.81, 18)
        tile = tiles.render_tile(self.classes, self.grid, 18, x, y)
        lon, _ = tiles.tile_lonlat(18, x, y)
        self.assertTrue((tile[:, lon >= east] == tiles.TRANSPARENT).all())
        self.assertTrue((tile[:, lon < east] != tiles.TRANSPARENT).all())


class PngTest(unittest.TestCase):
    def test_round_trip(self):
        indices = np.array([[0, 1, 2], [3, 4, tiles.TRANSPARENT]], np.uint8)
        got, rgba = decode_png(tiles.encode_png(indices, tiles.palette()))
        np.testing.assert_array_equal(got, indices)
        self.assertEqual(rgba[3], (0xE3, 0x1A, 0x1C, 255))
        self.assertEqual(rgba[tiles.TRANSPARENT][3], 0)

    def test_hidden_classes_are_transparent(self):
        rgba = tiles.palette(hidden={0, 4})
        self.assertEqual((rgba[0][3], rgba[4][3], rgba[2][3]), (0, 0, 255))

    def test_qt_reads_the_png(self):
        from PyQt5.QtGui import QImage
        image = QImage.fromData(tiles.encode_png(np.full((4, 5), 2, np.uint8), tiles.palette()), "PNG")
        self.assertFalse(image.isNull())
        self.assertEqual((image.width(), image.height()), (5, 4))
        self.assertEqual(image.pixelColor(1, 1).name(), "#1b7837")

    def test_parse_tile_path(self):
        self.assertEqual(tiles.parse_tile_path("/12/3245/2045.png", "hide=0,4"), (12, 3245, 2045, {0, 4}))
        self.assertEqual(tiles.parse_tile_path("/12/3245/2045.png", ""), (12, 3245, 2045, set()))
        self.assertIsNone(tiles.parse_tile_path("/12/x/2045.png", ""))
        self.assertIsNone(tiles.parse_tile_path("/12/2045", ""))
```

- [ ] **Step 2: Run it and see it fail**

Run: `frontend\.venv\Scripts\python -m unittest discover -s frontend/tests -t frontend -p test_tiles.py -v`
Expected: ERROR, `No module named 'tiles'`

- [ ] **Step 3: Write `frontend/tiles.py`**

```python
"""tiles.py - draws the EUDR map as Leaflet tiles, from the colour array rebuilt from Neon.

Leaflet asks for eudr://tiles/{z}/{x}/{y}.png?hide=0,4 (hide = classes switched off in the legend).
Each tile pixel is turned into longitude/latitude (Web Mercator), then into a map row/column with
the same floor rule as common.Grid.point_to_pixel, and takes that pixel's colour: nearest
neighbour, so classes are never blended. Pixels off the map are transparent. No database work.
"""

import struct
import zlib

import numpy as np
from PyQt5.QtCore import QBuffer, QIODevice
from PyQt5.QtWebEngineCore import QWebEngineUrlRequestJob, QWebEngineUrlScheme, QWebEngineUrlSchemeHandler

from legend import CLASSES, MAP_COLOURS, hex_to_rgb

SCHEME = b"eudr"
TILE = 256
TRANSPARENT = len(CLASSES)  # palette index for "off the map" (class codes are 0-5)


def tile_lonlat(z, x, y):
    """Longitude of each column centre and latitude of each row centre of a Web Mercator tile."""
    n = 2.0 ** z
    i = (np.arange(TILE) + 0.5) / TILE
    lon = (x + i) / n * 360.0 - 180.0
    lat = np.degrees(np.arctan(np.sinh(np.pi * (1.0 - 2.0 * (y + i) / n))))
    return lon, lat


def render_tile(classes, grid, z, x, y, to_map=None):
    """256 x 256 palette indices: the class under each tile pixel, TRANSPARENT off the map.
    to_map turns lon/lat into the map's own x/y for maps not in degrees (None for EPSG:4326)."""
    lon, lat = tile_lonlat(z, x, y)
    if to_map is None:  # degrees: columns depend only on longitude, rows only on latitude
        cols = np.floor((lon - grid.x0) / grid.pixel_w).astype(np.int64)
        rows = np.floor((grid.y0 - lat) / grid.pixel_h).astype(np.int64)
        rr, cc = np.meshgrid(rows, cols, indexing="ij")
    else:
        lons, lats = np.meshgrid(lon, lat)
        xs, ys = to_map(lons, lats)
        cc = np.floor((np.asarray(xs) - grid.x0) / grid.pixel_w).astype(np.int64)
        rr = np.floor((grid.y0 - np.asarray(ys)) / grid.pixel_h).astype(np.int64)
    inside = (rr >= 0) & (rr < grid.height) & (cc >= 0) & (cc < grid.width)
    out = np.full((TILE, TILE), TRANSPARENT, np.uint8)
    out[inside] = classes[rr[inside], cc[inside]]
    return out


def palette(hidden=(), alpha=255):
    """RGBA for palette indices 0..TRANSPARENT: the map's class colours (hidden classes and
    off-map pixels transparent)."""
    entries = [(*hex_to_rgb(MAP_COLOURS[c]), 0 if c in hidden else alpha) for c in CLASSES]
    return entries + [(0, 0, 0, 0)]


def encode_png(indices, rgba):
    """A palette PNG (8-bit indices + PLTE + tRNS) from a 2-D uint8 array, with zlib only."""
    h, w = indices.shape
    rows = np.zeros((h, w + 1), np.uint8)  # every row starts with filter type 0 (none)
    rows[:, 1:] = indices

    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 3, 0, 0, 0))
            + chunk(b"PLTE", bytes(v for r, g, b, _ in rgba for v in (r, g, b)))
            + chunk(b"tRNS", bytes(a for _, _, _, a in rgba))
            + chunk(b"IDAT", zlib.compress(rows.tobytes(), 6))
            + chunk(b"IEND", b""))


def parse_tile_path(path, query):
    """'/12/3245/2045.png', 'hide=0,4' -> (12, 3245, 2045, {0, 4}); None if it isn't a tile."""
    parts = path.strip("/").split("/")
    if len(parts) != 3 or not parts[2].endswith(".png"):
        return None
    try:
        z, x, y = int(parts[0]), int(parts[1]), int(parts[2][:-4])
    except ValueError:
        return None
    hidden = set()
    for item in query.split("&"):
        if item.startswith("hide="):
            hidden = {int(v) for v in item[5:].split(",") if v.strip().isdigit()}
    return z, x, y, hidden


def register_scheme():
    """Tell Qt WebEngine about eudr:// - must run before QApplication is created."""
    scheme = QWebEngineUrlScheme(SCHEME)
    scheme.setSyntax(QWebEngineUrlScheme.Syntax.Host)
    scheme.setFlags(QWebEngineUrlScheme.SecureScheme | QWebEngineUrlScheme.CorsEnabled)
    QWebEngineUrlScheme.registerScheme(scheme)


class TileHandler(QWebEngineUrlSchemeHandler):
    """Answers eudr://tiles/{z}/{x}/{y}.png from the colour array (runs on the window's thread)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.grid = self.classes = self.to_map = None

    def set_map(self, grid, classes, to_map=None):
        self.grid, self.classes, self.to_map = grid, classes, to_map

    def requestStarted(self, job):
        url = job.requestUrl()
        parsed = parse_tile_path(url.path(), url.query())
        if parsed is None or self.classes is None:
            job.fail(QWebEngineUrlRequestJob.UrlNotFound)
            return
        z, x, y, hidden = parsed
        png = encode_png(render_tile(self.classes, self.grid, z, x, y, self.to_map), palette(hidden))
        buf = QBuffer(job)  # owned by the job, so it lives exactly as long as the request
        buf.setData(png)
        buf.open(QIODevice.ReadOnly)
        job.reply(b"image/png", buf)
```

- [ ] **Step 4: Run the test and see it pass**

Run: `frontend\.venv\Scripts\python -m unittest discover -s frontend/tests -t frontend -p test_tiles.py -v`
Expected: `Ran 7 tests ... OK`

- [ ] **Step 5: Checkpoint.** `git add frontend/tiles.py frontend/tests/test_tiles.py` · `git commit -m "frontend: EUDR tiles from the colour array"`

---
### Task 3: Neon service core: open the map from Neon

**Files:**
- Create: `frontend/neon_service.py`, `frontend/tests/test_neon_service.py`

**Interfaces:**
- Consumes: `backend.common` (`connect`, `load_grid`, `Grid`, `TIFF_DIR`, `scan_map`, `PAGE`), `backend.stencil_approach` (`ensure_built`, `page_size`, `unzip_page`, `to_pixels`, `LAYERS`), `legend`.
- Produces:
  - `class Cancelled(Exception)`
  - `classes_from_pages(grid, rows) -> uint8[h, w]`
  - `lonlat_transformers(grid) -> (to_map, to_lonlat) | (None, None)`
  - `map_extent(grid) -> (west, south, east, north)`
  - `class NeonService(connect=None, on_status=None)` with:
    - attributes `grid`, `classes`, `counts`, `source_file`, `tif_path`, `tif_note`, `to_map`, `to_lonlat`
    - methods `_run(work, write=False)`, `close()`, `open_map() -> dict`, `map_info() -> dict`
  - **Rule for later tasks:** `class NeonService` is the last statement in the file. Later tasks append methods to the end of the file, indented 4 spaces.

- [ ] **Step 1: Write the failing test** `frontend/tests/test_neon_service.py`:

```python
import unittest
import zlib

import numpy as np
import psycopg

import neon_service
from backend import common, stencil_approach


def make_grid(width=520, height=300, default=0):
    return common.Grid("test", 103.0, -1.8, 1 / 12000, 1 / 12000, width, height, 4326, True,
                       default_class=default, fingerprint="test")


def pages_for(grid, data):
    """Pack a colour array the way stencil_approach.build does: one zipped row per page and no
    row for a page that is all the default colour."""
    stored = [c for c in common.COLOURS if c != grid.default_class]
    rows = []
    for row_off in range(0, grid.height, common.PAGE):
        for col_off in range(0, grid.width, common.PAGE):
            page = data[row_off:row_off + common.PAGE, col_off:col_off + common.PAGE]
            layers = stencil_approach.pack_page(page, stored)
            if layers.any():
                rows.append((col_off // common.PAGE, row_off // common.PAGE,
                             zlib.compress(layers.tobytes(), 9)))
    return rows


class ClassesFromPagesTest(unittest.TestCase):
    def test_round_trip_with_edge_pages_and_an_empty_page(self):
        grid = make_grid()
        data = np.random.default_rng(2).integers(0, 6, (grid.height, grid.width)).astype(np.uint8)
        data[:256, :256] = 0  # all default colour: this page gets no row
        rows = pages_for(grid, data)
        self.assertEqual(len(rows), 5)  # 3 x 2 pages minus the empty one
        np.testing.assert_array_equal(neon_service.classes_from_pages(grid, rows), data)

    def test_default_colour_need_not_be_land(self):
        grid = make_grid(default=2)
        data = np.full((grid.height, grid.width), 2, np.uint8)
        data[10:20, 300:340] = 3
        np.testing.assert_array_equal(neon_service.classes_from_pages(grid, pages_for(grid, data)), data)


class FakeConnection:
    def __init__(self):
        self.closed = False
        self.commits = self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


class RunTest(unittest.TestCase):
    def setUp(self):
        self.made, self.statuses = [], []

        def connect():
            self.made.append(FakeConnection())
            return self.made[-1]

        self.service = neon_service.NeonService(connect=connect, on_status=self.statuses.append)

    def test_read_jobs_end_with_a_rollback(self):
        self.assertEqual(self.service._run(lambda conn: 42), 42)
        self.assertEqual((self.made[0].rollbacks, self.made[0].commits), (1, 0))

    def test_write_jobs_commit(self):
        self.service._run(lambda conn: None, write=True)
        self.assertEqual(self.made[0].commits, 1)

    def test_a_dropped_connection_reconnects_once(self):
        calls = []

        def work(conn):
            calls.append(conn)
            if len(calls) == 1:
                raise psycopg.OperationalError("server closed the connection unexpectedly")
            return "ok"

        self.assertEqual(self.service._run(work), "ok")
        self.assertEqual(len(self.made), 2)
        self.assertTrue(self.made[0].closed)
        self.assertEqual(self.statuses, ["Reconnecting to Neon…"])

    def test_gives_up_after_the_second_failure(self):
        def work(conn):
            raise psycopg.OperationalError("still down")

        with self.assertRaises(psycopg.OperationalError):
            self.service._run(work)
        self.assertEqual(len(self.made), 2)

    def test_other_errors_roll_back_and_propagate(self):
        def work(conn):
            raise ValueError("bad")

        with self.assertRaises(ValueError):
            self.service._run(work)
        self.assertEqual(self.made[0].rollbacks, 1)

    def test_backend_messages_propagate(self):
        def work(conn):
            raise SystemExit("No map in the database yet")

        with self.assertRaises(SystemExit):
            self.service._run(work)


class InfoTest(unittest.TestCase):
    def test_extent_of_the_stored_map(self):
        west, south, east, north = neon_service.map_extent(make_grid(6000, 6000))
        for got, want in ((west, 103.0), (south, -2.3), (east, 103.5), (north, -1.8)):
            self.assertAlmostEqual(got, want)

    def test_map_info(self):
        service = neon_service.NeonService(connect=lambda: None)
        service.grid = make_grid(10, 10)
        service.counts = np.array([75, 0, 25, 0, 0, 0])
        info = service.map_info()
        self.assertEqual([c["code"] for c in info["classes"]], [0, 2])
        self.assertAlmostEqual(info["classes"][1]["share"], 25.0)
        (south, west), (north, east) = info["bounds"]
        self.assertAlmostEqual(south, -1.8 - 10 / 12000)
        self.assertAlmostEqual(east, 103.0 + 10 / 12000)
        self.assertFalse(info["tiff"]["available"])
```

- [ ] **Step 2: Run it and see it fail**

Run: `frontend\.venv\Scripts\python -m unittest discover -s frontend/tests -t frontend -p test_neon_service.py -v`
Expected: ERROR, `No module named 'neon_service'`

- [ ] **Step 3: Write `frontend/neon_service.py`**

```python
"""neon_service.py - the only part of the app that talks to the backend and to Neon.

Plain Python (no Qt), so it can be tested on its own and could later sit behind a web API.
It calls the backend's own functions (see backend.py), so every answer is the one the
command-line tools give.

Every public method goes through _run(): the transaction is always ended afterwards, so a
SET LOCAL jit = off never carries over and Neon's pooled connection is not held. If Neon has
gone to sleep and dropped the connection, it reconnects once and tries again.
"""

import os

import numpy as np
import psycopg
import rasterio
from pyproj import Transformer

from backend import common, stencil_approach
from legend import CLASS_LABELS, CLASSES, MAP_COLOURS

PAGE = common.PAGE


class Cancelled(Exception):
    """Raised inside a long job when the user presses Cancel."""


def classes_from_pages(grid, rows):
    """Stencil pages from Neon -> the whole map as a (height, width) uint8 array of colours.
    Unpacks like stencil_approach.verify; a page with no row is all the default colour."""
    out = np.full((grid.height, grid.width), grid.default_class, np.uint8)
    for page_col, page_row, blob in rows:
        h, w = stencil_approach.page_size(grid, page_col, page_row)
        layers = stencil_approach.unzip_page(bytes(blob), h, w)
        target = out[page_row * PAGE:page_row * PAGE + h, page_col * PAGE:page_col * PAGE + w]
        for colour in range(stencil_approach.LAYERS):
            if layers[colour].any():
                target[stencil_approach.to_pixels(layers[colour])[:h, :w]] = colour
    return out


def lonlat_transformers(grid):
    """(to_map, to_lonlat) between longitude/latitude and the map's own coordinates,
    or (None, None) when the map is already in degrees (EPSG:4326)."""
    if grid.srid == 4326:
        return None, None
    return (Transformer.from_crs(4326, grid.srid, always_xy=True).transform,
            Transformer.from_crs(grid.srid, 4326, always_xy=True).transform)


def map_extent(grid):
    """(west, south, east, north) of the map, in longitude/latitude."""
    xs, ys = grid.bounds_box().exterior.xy
    xs, ys = np.asarray(xs), np.asarray(ys)
    _, to_lonlat = lonlat_transformers(grid)
    if to_lonlat is not None:
        xs, ys = to_lonlat(xs, ys)
    return float(np.min(xs)), float(np.min(ys)), float(np.max(xs)), float(np.max(ys))


class NeonService:
    def __init__(self, connect=None, on_status=None):
        self._connect = connect or common.connect
        self.on_status = on_status or (lambda text: None)
        self.conn = None
        self.grid = self.classes = self.counts = None
        self.source_file = None
        self.tif_path = None
        self.tif_note = "The TIFF hasn't been looked for yet."
        self.to_map = self.to_lonlat = None

    # ----- connection -----
    def _close(self):
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:  # noqa: BLE001 - it is already broken
                pass
        self.conn = None

    def close(self):
        self._close()

    def _run(self, work, write=False):
        """Run work(conn) and end the transaction (commit if write, else rollback).
        A dropped connection (psycopg.OperationalError) gets one reconnect and one retry."""
        for attempt in (1, 2):
            if self.conn is None or self.conn.closed:
                self.conn = self._connect()
            try:
                result = work(self.conn)
                if write:
                    self.conn.commit()
                else:
                    self.conn.rollback()
                return result
            except psycopg.OperationalError:
                self._close()
                if attempt == 2:
                    raise
                self.on_status("Reconnecting to Neon…")
            except BaseException:
                try:
                    self.conn.rollback()
                except Exception:  # noqa: BLE001 - connection broken: start fresh next time
                    self._close()
                raise

    # ----- the map -----
    def open_map(self):
        """grid_info, then every stencil page in one query, unpacked into self.classes.
        Returns what the map page needs (JSON-ready)."""
        def load(conn):
            grid = common.load_grid(conn)
            stencil_approach.ensure_built(conn)
            with conn.cursor() as cur:
                cur.execute("SELECT source_file FROM grid_info LIMIT 1")
                source_file = cur.fetchone()[0]
                cur.execute("SELECT page_col, page_row, stencils FROM stencil_pages")
                rows = cur.fetchall()
            return grid, source_file, rows

        self.on_status("Reading the map from Neon…")
        grid, self.source_file, rows = self._run(load)
        self.grid = grid
        self.to_map, self.to_lonlat = lonlat_transformers(grid)
        self.classes = classes_from_pages(grid, rows)
        self.counts = np.bincount(self.classes.ravel(), minlength=len(CLASSES))[:len(CLASSES)]
        self.on_status("Looking for the original TIFF…")
        self._find_tiff()
        return self.map_info()

    def _find_tiff(self):
        """Use the local TIFF as the reference only if it is exactly the map stored in Neon."""
        self.tif_path = None
        name = self.source_file or "TIFF"
        path = os.path.join(common.TIFF_DIR, self.source_file or "")
        if not self.source_file or not os.path.isfile(path):
            self.tif_note = f"Put the uploaded {name} in backend/data/tiff/ to compare with the original TIFF."
            return
        try:
            with rasterio.open(path) as src:
                tif_grid = common.Grid.from_dataset(src, self.grid.map_version)
                same = tif_grid == self.grid and common.scan_map(src, tif_grid)[1] == self.grid.fingerprint
        except SystemExit as exc:
            self.tif_note = f"{name} could not be read: {exc}"
            return
        if not same:
            self.tif_note = f"{name} in backend/data/tiff/ is not the map stored in Neon, so it isn't used."
            return
        self.tif_path = path
        self.tif_note = f"Comparing with {name}: the same map as in Neon."

    def map_info(self):
        grid, counts = self.grid, self.counts
        total = max(int(counts.sum()), 1)
        west, south, east, north = map_extent(grid)
        return {
            "map_version": grid.map_version,
            "width": grid.width, "height": grid.height, "srid": grid.srid,
            "pixel_w": grid.pixel_w, "pixel_h": grid.pixel_h,
            "bounds": [[south, west], [north, east]],
            "default_class": grid.default_class,
            "classes": [{"code": c, "label": CLASS_LABELS[c], "colour": MAP_COLOURS[c],
                         "pixels": int(counts[c]), "share": 100.0 * int(counts[c]) / total}
                        for c in CLASSES if counts[c]],
            "tiff": {"available": self.tif_path is not None, "note": self.tif_note},
        }
```

- [ ] **Step 4: Run the test and see it pass**

Run: `frontend\.venv\Scripts\python -m unittest discover -s frontend/tests -t frontend -p test_neon_service.py -v`
Expected: `Ran 10 tests ... OK`

- [ ] **Step 5: Try it against Neon (read-only)**

From `frontend\` run: `.venv\Scripts\python -c "import neon_service as n; s=n.NeonService(on_status=print); i=s.open_map(); print(i['map_version'], i['width'], i['height'], i['bounds']); print([(c['label'], round(c['share'], 1)) for c in i['classes']]); print(i['tiff'])"`
Expected:
- `jambi-v1 6000 6000 [[-2.3, 103.0], [-1.8, 103.5]]`;
- shares close to land 64.9, water 0.1, forest 29.4, deforestation 2.3, needs review 3.0;
- `'available': True`.

- [ ] **Step 6: Checkpoint.** `git add frontend/neon_service.py frontend/tests/test_neon_service.py` · `git commit -m "frontend: Neon service opens the map from the stencils"`

---

### Task 4: Background worker

**Files:**
- Create: `frontend/worker.py`, `frontend/tests/test_worker.py`, `frontend/tests/qt_app.py` (one shared QApplication for every Qt test)

**Interfaces:**
- Consumes: `neon_service.Cancelled`; a `make_service(status_callback)` factory returning an object with job methods and `close()`.
- Produces: `class Worker(QThread)` with:
  - signals `done(int id, str kind, object result)`, `failed(int id, str kind, str message)` (`""` = replaced by a newer request; ignore it), `progress(int id, str kind, int done, int total, str text)`, `status(str)`, `started_job(int id, str kind)`
  - methods `submit(kind, method, args=(), kwargs=None, progress=False, coalesce=False) -> int`, `cancel()`, `stop()`
  - attribute `service`
  - A job with `progress=True` receives the keyword arguments `progress(done, total, text)` and `cancel` (a `threading.Event`).

- [ ] **Step 1: Write the shared Qt helper and the failing test.** `frontend/tests/qt_app.py`:

```python
"""One QApplication for every test that needs Qt, created when a test first asks for it.
QtWebEngineWidgets must be imported before any QApplication exists, so it is imported here."""

import sys

from PyQt5 import QtWebEngineWidgets  # noqa: F401
from PyQt5.QtWidgets import QApplication


def qt_app():
    return QApplication.instance() or QApplication(sys.argv[:1])
```

`frontend/tests/test_worker.py`:

```python
import unittest

from PyQt5.QtCore import QEventLoop, QTimer

import worker
from neon_service import Cancelled
from tests.qt_app import qt_app


class FakeService:
    def __init__(self, status):
        self.status = status

    def echo(self, value):
        return {"value": value}

    def boom(self):
        raise ValueError("bad input")

    def leave(self):
        raise SystemExit("Run the build first.")

    def slow(self, progress=None, cancel=None):
        for i in range(500):
            progress(i, 500, "working")
            if cancel.wait(0.01):
                raise Cancelled()
        return "finished"

    def close(self):
        pass


class WorkerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self.worker = worker.Worker(FakeService)
        self.events = []
        self.worker.done.connect(lambda i, kind, result: self.events.append(("done", kind, result)))
        self.worker.failed.connect(lambda i, kind, message: self.events.append(("failed", kind, message)))
        self.worker.start()

    def tearDown(self):
        self.worker.stop()
        self.worker.wait(3000)

    def wait_for(self, count, timeout_ms=5000):
        loop, timer = QEventLoop(), QTimer()
        timer.setInterval(10)
        timer.timeout.connect(lambda: loop.quit() if len(self.events) >= count else None)
        timer.start()
        QTimer.singleShot(timeout_ms, loop.quit)
        loop.exec_()
        timer.stop()

    def test_results_and_errors_come_back_in_order(self):
        self.worker.submit("a", "echo", (1,))
        self.worker.submit("b", "boom")
        self.worker.submit("c", "leave")
        self.wait_for(3)
        self.assertEqual(self.events, [("done", "a", {"value": 1}),
                                       ("failed", "b", "ValueError: bad input"),
                                       ("failed", "c", "Run the build first.")])

    def test_cancel_stops_a_long_job(self):
        self.worker.submit("s", "slow", progress=True)
        QTimer.singleShot(150, self.worker.cancel)
        self.wait_for(1)
        self.assertEqual(self.events, [("failed", "s", "Cancelled.")])

    def test_a_newer_request_replaces_a_waiting_one(self):
        self.worker.submit("s", "slow", progress=True)  # keeps the thread busy
        self.worker.submit("view", "echo", (1,), coalesce=True)
        self.worker.submit("view", "echo", (2,), coalesce=True)  # replaces the waiting one
        QTimer.singleShot(150, self.worker.cancel)
        self.wait_for(3)
        self.assertEqual(self.events, [("failed", "view", ""),
                                       ("failed", "s", "Cancelled."),
                                       ("done", "view", {"value": 2})])

    def test_progress_reaches_the_window(self):
        seen = []
        self.worker.progress.connect(lambda i, kind, done, total, text: seen.append((done, total, text)))
        self.worker.submit("s", "slow", progress=True)
        QTimer.singleShot(150, self.worker.cancel)
        self.wait_for(1)
        self.assertEqual(seen[0], (0, 500, "working"))
```

- [ ] **Step 2: Run it and see it fail**

Run: `frontend\.venv\Scripts\python -m unittest discover -s frontend/tests -t frontend -p test_worker.py -v`
Expected: ERROR, `No module named 'worker'`

- [ ] **Step 3: Write `frontend/worker.py`**

```python
"""worker.py - one background thread that owns the NeonService and runs jobs one at a time.

A psycopg connection must never be used by two threads at once, so every database job goes
through this queue, and the window never waits: results come back as Qt signals.
"""

import threading
import traceback
from collections import deque
from dataclasses import dataclass, field

from PyQt5.QtCore import QThread, pyqtSignal

from neon_service import Cancelled


@dataclass
class Job:
    id: int
    kind: str
    method: str
    args: tuple = ()
    kwargs: dict = field(default_factory=dict)
    progress: bool = False


class Worker(QThread):
    done = pyqtSignal(int, str, object)              # job id, kind, result
    failed = pyqtSignal(int, str, str)               # job id, kind, message ("" = replaced)
    progress = pyqtSignal(int, str, int, int, str)   # job id, kind, done, total, text
    status = pyqtSignal(str)                         # short status text from the service
    started_job = pyqtSignal(int, str)               # job id, kind

    def __init__(self, make_service, parent=None):
        super().__init__(parent)
        self._make_service = make_service
        self._jobs = deque()
        self._cond = threading.Condition()
        self._stopping = False
        self._cancel = threading.Event()
        self._next_id = 1
        self.service = None

    def submit(self, kind, method, args=(), kwargs=None, progress=False, coalesce=False):
        """Queue service.<method>(*args, **kwargs). coalesce=True: a waiting job of the same kind
        is dropped (it gets failed(id, kind, "")), e.g. outlines for a view already panned away."""
        with self._cond:
            job = Job(self._next_id, kind, method, tuple(args), dict(kwargs or {}), progress)
            self._next_id += 1
            dropped = [j for j in self._jobs if coalesce and j.kind == kind]
            for old in dropped:
                self._jobs.remove(old)
            self._jobs.append(job)
            self._cond.notify()
        for old in dropped:
            self.failed.emit(old.id, old.kind, "")
        return job.id

    def cancel(self):
        """Ask the running job to stop (long jobs check this between steps)."""
        self._cancel.set()

    def stop(self):
        with self._cond:
            self._stopping = True
            self._jobs.clear()
            self._cond.notify()
        self._cancel.set()

    def run(self):
        self.service = self._make_service(self.status.emit)
        try:
            while True:
                with self._cond:
                    while not self._jobs and not self._stopping:
                        self._cond.wait()
                    if self._stopping:
                        return
                    job = self._jobs.popleft()
                self._cancel.clear()
                self.started_job.emit(job.id, job.kind)
                self._execute(job)
        finally:
            self.service.close()

    def _execute(self, job):
        kwargs = dict(job.kwargs)
        if job.progress:
            kwargs["progress"] = lambda done, total, text: self.progress.emit(
                job.id, job.kind, int(done), int(total), str(text))
            kwargs["cancel"] = self._cancel
        try:
            result = getattr(self.service, job.method)(*job.args, **kwargs)
        except Cancelled:
            self.failed.emit(job.id, job.kind, "Cancelled.")
        except SystemExit as exc:  # the backend's own "how to fix it" messages
            self.failed.emit(job.id, job.kind, str(exc.code or "Stopped."))
        except Exception as exc:  # noqa: BLE001 - shown to the user instead of crashing the app
            traceback.print_exc()
            self.failed.emit(job.id, job.kind, f"{type(exc).__name__}: {exc}")
        else:
            self.done.emit(job.id, job.kind, result)
```

- [ ] **Step 4: Run the test and see it pass**

Run: `frontend\.venv\Scripts\python -m unittest discover -s frontend/tests -t frontend -p test_worker.py -v`
Expected: `Ran 4 tests ... OK`

- [ ] **Step 5: Checkpoint.** `git add frontend/worker.py frontend/tests/test_worker.py` · `git commit -m "frontend: background worker for Neon jobs"`

---

### Task 5: The map page moved out of `app.py`, a map area for Qt overlays, and the new window skeleton

The app still looks and works as before (draw, edit, rename, delete, export). Underneath:
- the map page lives in `web/`, loaded by `pages.py`;
- `map.js` already supports points and verdict styling, used from Task 9;
- the map view sits in a `MapArea`, which floats native Qt widgets over it (legend, banner, verdict card);
- the window has tabs, a splitter, a background worker, `run_job`, events and toolbar anchors for later features;
- `main()` registers the `eudr://` tile scheme.

**Files:**
- Create: `frontend/pages.py`, `frontend/map_area.py`, `frontend/web/map.html`, `frontend/web/map.js`, `frontend/tests/test_pages.py`, `frontend/tests/test_map_area.py`
- Rewrite: `frontend/app.py` (whole file below)

**Interfaces:**
- Consumes: `tiles.register_scheme`, `tiles.TileHandler`, `tiles.SCHEME`, `worker.Worker`, `neon_service.NeonService`, `legend.page_config`.
- Produces:
  - `pages.build_page(html_name, config, qwebchannel_js) -> str` and `pages.load_qwebchannel_js() -> str`
  - `class WebPage(name, html_name, config, qwebchannel_js, parent)` with `.view`, `.page`, `.bridge`, `call(fn, *args)`, `on(kind, handler)`
  - `Bridge` signals `geojson_changed(dict)`, `state_changed(dict)`, `message(str, object)`, `page_ready()`
  - `map_area.overlay_position(corner, margin, area_size, widget_size) -> (x, y)`; `MapArea(view)` with `add_overlay(widget, corner, margin=(12, 12))` and `relayout()`; corners are `top-left`, `top-center`, `top-right`, `bottom-left` and `bottom-right`
  - `MainWindow` with:
    - attributes `map` (WebPage), `map_area` (MapArea), `toolbar`, `tabs`, `splitter`, `worker`, `tile_handler`, `map_info`, `geojson`, `state`, `qwebchannel_js`, `page_config`, `spacer_anchor`, `basemap_anchor`
    - methods `run_job(kind, method, *args, done=None, failed=None, progress=None, coalesce=False, **kwargs) -> int`, `listen(event, cb)`, `emit(event, *args)`, `insert_tools(anchor, items)`, `icon(name) -> QIcon`, `js(code)`, `selected_id()`
    - events emitted: `"state"` (each refresh), `"geojson"` (each map change), `"map_loaded"` (Task 6)
  - Map JS (callable through `window.map.call(...)`):
    - drawing: `startDraw()`, `startPoint()`, `startEdit()`, `confirmAction()`, `cancelAction()`, `undoPoint()`
    - farms: `selectShape(id, fromPanel)`, `zoomToShape(id)`, `deleteShape(id)`, `renameShape(id, name)`, `clearAll()`, `addFeatures(featureCollection)`, `setVerdicts({id: {verdict, stale}})`
    - view and helpers: `setBasemap(name)`, `lockView(force)`, `addCss(text)`, `send(kind, data)`
  - Map JS globals: `map`, `bounds`, `aoi`, `drawnItems`, `mode`, `selectedId`, `verdicts`, `esc`, `findLayer`, `layerBounds`, `isPoint`
  - Messages from the map: `farmClicked {id}`, `mapClicked {lat, lng}`, `escape`

- [ ] **Step 1: Write the failing test** `frontend/tests/test_pages.py`:

```python
import os
import tempfile
import unittest
from unittest import mock

import pages


class BuildPageTest(unittest.TestCase):
    def test_includes_config_and_channel_are_filled_in(self):
        with tempfile.TemporaryDirectory() as folder:
            with open(os.path.join(folder, "p.html"), "w", encoding="utf-8") as fh:
                fh.write("<script>/*QWEBCHANNEL*/</script><script>/*CONFIG*/</script>"
                         "<script>/*INCLUDE:a.js*/</script>")
            with open(os.path.join(folder, "a.js"), "w", encoding="utf-8") as fh:
                fh.write("var a = 1; // \\1 stays as written")
            with mock.patch.object(pages, "WEB_DIR", folder):
                html = pages.build_page("p.html", {"x": [1, 2]}, "QWC();")
        self.assertIn("<script>QWC();</script>", html)
        self.assertIn('window.CONFIG = {"x": [1, 2]};', html)
        self.assertIn("var a = 1; // \\1 stays as written", html)

    def test_the_real_map_page_builds(self):
        html = pages.build_page("map.html", {"x": 1}, "QWC();")
        self.assertNotIn("/*INCLUDE:", html)
        self.assertIn("function setVerdicts", html)
```

And `frontend/tests/test_map_area.py`:

```python
import unittest

import map_area


class OverlayPositionTest(unittest.TestCase):
    def test_corners(self):
        area, size = (1000, 600), (200, 100)
        self.assertEqual(map_area.overlay_position("top-left", (12, 12), area, size), (12, 12))
        self.assertEqual(map_area.overlay_position("top-center", (0, 12), area, size), (400, 12))
        self.assertEqual(map_area.overlay_position("top-right", (12, 12), area, size), (788, 12))
        self.assertEqual(map_area.overlay_position("bottom-left", (12, 36), area, size), (12, 464))
        self.assertEqual(map_area.overlay_position("bottom-right", (12, 12), area, size), (788, 488))

    def test_never_placed_off_the_top_or_left(self):
        self.assertEqual(map_area.overlay_position("bottom-right", (12, 12), (100, 50), (200, 100)), (0, 0))
```

- [ ] **Step 2: Run them and see them fail**

Run: `frontend\.venv\Scripts\python -m unittest discover -s frontend/tests -t frontend -p "test_*.py" -v`
Expected: ERRORs, `No module named 'pages'` and `No module named 'map_area'`

- [ ] **Step 3: Write `frontend/pages.py`**

```python
"""pages.py - the map page (Leaflet, in web/) and how Python talks to it.

Each page is an HTML file in web/. Before it is loaded, markers are filled in:
  /*QWEBCHANNEL*/       Qt's qwebchannel.js, which lets the page call Python
  /*CONFIG*/            window.CONFIG = {...}: legend colours, verdict words, farm colours
  /*INCLUDE:name.js*/   the contents of web/name.js
A page talks to Python with bridge.send(kind, json) and says bridge.ready() once it has started;
Python calls page functions with WebPage.call(fn, args...). Calls made before the page is ready
are queued. Console messages (including JavaScript errors) are printed to the terminal.
"""

import json
import os
import re
from collections import defaultdict

from PyQt5.QtCore import QFile, QIODevice, QObject, QUrl, pyqtSignal, pyqtSlot
from PyQt5.QtWebChannel import QWebChannel
from PyQt5.QtWebEngineWidgets import QWebEnginePage, QWebEngineView

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
BASE_URL = QUrl("https://localhost/")
_INCLUDE = re.compile(r"/\*INCLUDE:([\w.-]+)\*/")


def load_qwebchannel_js():
    f = QFile(":/qtwebchannel/qwebchannel.js")
    if not f.open(QIODevice.ReadOnly):
        raise RuntimeError("Could not load qwebchannel.js from Qt resources")
    js = bytes(f.readAll()).decode("utf-8")
    f.close()
    return js


def read_web_file(name):
    with open(os.path.join(WEB_DIR, name), encoding="utf-8") as fh:
        return fh.read()


def build_page(html_name, config, qwebchannel_js):
    """The HTML of web/<html_name> with its includes, config and qwebchannel.js filled in."""
    html = _INCLUDE.sub(lambda m: read_web_file(m.group(1)), read_web_file(html_name))
    html = html.replace("/*CONFIG*/", "window.CONFIG = " + json.dumps(config) + ";")
    return html.replace("/*QWEBCHANNEL*/", qwebchannel_js)


class Bridge(QObject):
    """What a page can call: update(json) and state(json) (the map's drawing state),
    send(kind, json) (anything else) and ready() (the page has started)."""

    geojson_changed = pyqtSignal(dict)
    state_changed = pyqtSignal(dict)
    message = pyqtSignal(str, object)
    page_ready = pyqtSignal()

    @pyqtSlot(str)
    def update(self, payload):
        self.geojson_changed.emit(json.loads(payload))

    @pyqtSlot(str)
    def state(self, payload):
        self.state_changed.emit(json.loads(payload))

    @pyqtSlot(str, str)
    def send(self, kind, payload):
        self.message.emit(kind, json.loads(payload) if payload else None)

    @pyqtSlot()
    def ready(self):
        self.page_ready.emit()


class LoggingPage(QWebEnginePage):
    """Prints the page's console messages to the terminal, so JavaScript errors are visible."""

    def __init__(self, name, parent=None):
        super().__init__(parent)
        self.name = name

    def javaScriptConsoleMessage(self, level, message, line, source):
        print(f"[{self.name}] {message} (line {line})", flush=True)


class WebPage(QObject):
    def __init__(self, name, html_name, config, qwebchannel_js, parent=None):
        super().__init__(parent)
        self.view = QWebEngineView()
        self.page = LoggingPage(name, self.view)
        self.view.setPage(self.page)
        self.bridge = Bridge(self)
        self.channel = QWebChannel(self.page)
        self.channel.registerObject("bridge", self.bridge)
        self.page.setWebChannel(self.channel)
        self._ready = False
        self._queue = []
        self._handlers = defaultdict(list)
        self.bridge.page_ready.connect(self._on_ready)
        self.bridge.message.connect(self._dispatch)
        self.page.setHtml(build_page(html_name, config, qwebchannel_js), BASE_URL)

    def call(self, fn, *args):
        """Run fn(args...) in the page (arguments are sent as JSON)."""
        code = f"{fn}({', '.join(json.dumps(a) for a in args)});"
        if self._ready:
            self.page.runJavaScript(code)
        else:
            self._queue.append(code)

    def on(self, kind, handler):
        """handler(data) runs when the page calls send(kind, data)."""
        self._handlers[kind].append(handler)

    def _on_ready(self):
        self._ready = True
        for code in self._queue:
            self.page.runJavaScript(code)
        self._queue.clear()

    def _dispatch(self, kind, data):
        for handler in list(self._handlers.get(kind, [])):
            handler(data)
```

- [ ] **Step 4: Write `frontend/map_area.py`**

```python
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
```

- [ ] **Step 5: Write `frontend/web/map.html`**

```html
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet.draw/1.0.4/leaflet.draw.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet.draw/1.0.4/leaflet.draw.js"></script>
<script>/*QWEBCHANNEL*/</script>
<script>/*CONFIG*/</script>
<style>
  html, body, #map { height: 100%; margin: 0; padding: 0; }
  body { font: 13px "Segoe UI", system-ui, sans-serif; color: #0b0b0b; }
  .vertex-handle {
    background: #fff; border: 2px solid #1f2937; border-radius: 50%;
    box-shadow: 0 1px 3px rgba(0,0,0,0.4); cursor: pointer;
  }
  .vertex-handle.first-vertex { background: #22c55e; border-color: #fff; }
  .leaflet-draw-tooltip { display: none !important; }
  .shape-label {
    background: rgba(255,255,255,0.88); border: none; border-radius: 4px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.2); font: 600 12px "Segoe UI", sans-serif; padding: 2px 6px;
  }
  .shape-label::before { display: none; }
  .point-icon { background: none; border: none; }
  .point-icon span {
    display: block; width: 100%; height: 100%; box-sizing: border-box; border-radius: 50%;
    border: 2px solid #fff; box-shadow: 0 0 0 1px rgba(0,0,0,0.45), 0 1px 3px rgba(0,0,0,0.35);
  }
</style>
</head>
<body>
<div id="map"></div>
<script>/*INCLUDE:map.js*/</script>
</body>
</html>
```

- [ ] **Step 6: Write `frontend/web/map.js`** (the old inline script, plus points, verdict styling, `addFeatures` and `send`)

```javascript
// map.js - the map page: Leaflet + leaflet.draw. Farms are polygons and points.
// Python gets every change through bridge.update(GeoJSON) and the drawing state through
// bridge.state(...); anything else goes through send(kind, data).
var bounds = CONFIG.initialBounds;  // replaced by the extent of the map stored in Neon (map_eudr.js)
var COLORS = CONFIG.shapeColours;

function addCss(css) {
  var style = document.createElement('style');
  style.textContent = css;
  document.head.appendChild(style);
}

// Lock the view to the box: no panning outside it, no zooming out past it
var map = L.map('map', {
  zoomSnap: 0, zoomDelta: 0.5, zoomControl: false,
  maxBounds: bounds, maxBoundsViscosity: 1.0
});
L.control.zoom({ position: 'bottomright' }).addTo(map);
L.control.scale({ position: 'bottomleft' }).addTo(map);

// Smallest zoom at which the box still covers the whole map viewport
function lockView(force) {
  var minZoom = map.getBoundsZoom(bounds, true);
  map.setMinZoom(minZoom);
  if (force || !map._loaded || map.getZoom() < minZoom) {
    map.setView(L.latLngBounds(bounds).getCenter(), minZoom, { animate: false });
  }
}
lockView(false);
map.on('resize', function () { lockView(false); });

var basemaps = {
  streets: L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19, attribution: '&copy; OpenStreetMap contributors'
  }),
  satellite: L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
    maxZoom: 19, attribution: 'Tiles &copy; Esri'
  })
};
var basemap = basemaps.streets.addTo(map);
function setBasemap(name) {
  map.removeLayer(basemap);
  basemap = basemaps[name].addTo(map);
  basemap.bringToBack();
}

// Outline of the area of interest
var aoi = L.rectangle(bounds, { color: '#e63946', weight: 2, dashArray: '6 6', fill: false, interactive: false }).addTo(map);

map.createPane('halos').style.zIndex = 390;  // white outlines under checked farms
var drawnItems = new L.FeatureGroup().addTo(map);
var haloGroup = L.featureGroup().addTo(map);
var vertexIcon = new L.DivIcon({ iconSize: new L.Point(14, 14), className: 'vertex-handle' });
L.Edit.PolyVerticesEdit.prototype.options.icon = vertexIcon;
L.Edit.PolyVerticesEdit.prototype.options.touchIcon = vertexIcon;

function pointIcon(colour, selected) {
  var size = selected ? 18 : 14;
  return L.divIcon({ className: 'point-icon', iconSize: [size, size],
                     html: '<span style="background:' + colour + '"></span>' });
}

var drawHandler = new L.Draw.Polygon(map, {
  allowIntersection: false, showArea: false, guidelineDistance: 12,
  icon: vertexIcon, touchIcon: vertexIcon
});
var pointHandler = new L.Draw.Marker(map, { icon: pointIcon(COLORS[0], true) });
var editHandler = new L.EditToolbar.Edit(map, {
  featureGroup: drawnItems,
  selectedPathOptions: { dashArray: '8, 8', fill: true, fillOpacity: 0.25, maintainColor: true }
});

var bridge = null;
var mode = 'idle';      // idle | drawing | placing | editing
var selectedId = null;
var nextId = 1;
var activeDraw = null;  // the draw handler in use
var verdicts = {};      // id -> {verdict, stale}: the last check, set by Python

function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return '&#' + c.charCodeAt(0) + ';'; }); }
function send(kind, data) { if (bridge) bridge.send(kind, JSON.stringify(data === undefined ? null : data)); }
function colorFor(id) { return COLORS[(id - 1) % COLORS.length]; }
function isPoint(layer) { return layer instanceof L.Marker; }
function strokeFor(id) {
  var v = verdicts[id];
  if (!v) return colorFor(id);
  return v.stale ? '#9ca3af' : CONFIG.verdicts[v.verdict].colour;
}
function applyStyle(layer, selected) {
  var id = layer.feature.properties.id, v = verdicts[id], c = strokeFor(id);
  if (isPoint(layer)) { layer.setIcon(pointIcon(c, selected)); return; }
  layer.setStyle({
    color: c, fillColor: c, opacity: 1,
    weight: selected ? 4 : (v && !v.stale ? 3 : 2.5),
    dashArray: v && v.stale ? '6 5' : null,
    fillOpacity: selected ? 0.3 : (v ? 0.08 : 0.18)
  });
}
function findLayer(id) {
  return drawnItems.getLayers().filter(function (l) { return l.feature.properties.id === id; })[0];
}
function sortedLayers() {
  return drawnItems.getLayers().sort(function (a, b) { return a.feature.properties.id - b.feature.properties.id; });
}
function layerBounds(layer) {
  return isPoint(layer) ? L.latLngBounds([layer.getLatLng(), layer.getLatLng()]) : layer.getBounds();
}
function labelAnchor(layer) { return isPoint(layer) ? layer.getLatLng() : layer.getCenter(); }

// Geodesic area of a polygon layer in km2: every part, outer rings minus holes
function ringKm2(latlngs) { return L.GeometryUtil.geodesicArea(latlngs) / 1e6; }
function polygonsOf(layer) {
  var g = layer.toGeoJSON().geometry;
  return g.type === 'Polygon' ? [g.coordinates] : (g.type === 'MultiPolygon' ? g.coordinates : []);
}
function areaKm2(layer) {
  var km2 = 0;
  polygonsOf(layer).forEach(function (rings) {
    rings.forEach(function (ring, i) {
      var a = ringKm2(ring.map(function (c) { return L.latLng(c[1], c[0]); }));
      km2 += i === 0 ? a : -a;
    });
  });
  return km2;
}
function vertexCount(layer) {
  var n = 0;
  polygonsOf(layer).forEach(function (rings) { rings.forEach(function (ring) { n += ring.length - 1; }); });
  return n;
}
function drawPoints() { return drawHandler._markers ? drawHandler._markers.length : 0; }

// ---------- state -> Python ----------
function sync() {
  var features = sortedLayers().map(function (layer) {
    var p = layer.feature.properties, point = isPoint(layer);
    p.area_km2 = point ? 0 : Math.round(areaKm2(layer) * 1000) / 1000;
    p.vertices = point ? 1 : vertexCount(layer);
    if (layer.getTooltip()) layer.getTooltip().setLatLng(labelAnchor(layer));
    var f = layer.toGeoJSON();
    f.properties = { id: p.id, name: p.name, kind: point ? 'point' : 'outline',
                     area_km2: p.area_km2, vertices: p.vertices };
    if (p.area_ha !== undefined) f.properties.area_ha = p.area_ha;
    if (p.supplier_id !== undefined) f.properties.supplier_id = p.supplier_id;
    return f;
  });
  if (bridge) bridge.update(JSON.stringify({ type: 'FeatureCollection', features: features }));
  refreshHalos();
  report();
}

function report() {
  if (!bridge) return;
  var pts = drawPoints();
  var live = pts >= 3 ? ringKm2(drawHandler._markers.map(function (m) { return m.getLatLng(); })) : 0;
  bridge.state(JSON.stringify({ mode: mode, points: pts, area_km2: live, selected: selectedId }));
}

// ---------- selection ----------
// fromPanel: the toolbar asked for this, so reveal it on the map
function selectShape(id, fromPanel) {
  selectedId = (id === null || findLayer(id) === undefined) ? null : id;
  drawnItems.eachLayer(function (l) {
    var lid = l.feature.properties.id;
    applyStyle(l, lid === selectedId);
    if (lid === selectedId && !isPoint(l)) l.bringToFront();
  });
  if (selectedId !== null && fromPanel) {
    var b = layerBounds(findLayer(selectedId));
    if (!map.getBounds().contains(b)) map.fitBounds(b, { padding: [80, 80], maxZoom: 17 });
  }
  report();
}
function zoomToShape(id) {
  var layer = findLayer(id);
  if (layer) map.fitBounds(layerBounds(layer), { padding: [80, 80], maxZoom: 17 });
}

map.on('click', function (e) {
  if (mode !== 'idle') return;
  selectShape(null, false);
  send('mapClicked', { lat: e.latlng.lat, lng: e.latlng.lng });
});

// ---------- farms ----------
function labelFor(layer) {
  var p = layer.feature.properties, v = verdicts[p.id];
  if (!v) return esc(p.name);
  if (v.stale) return esc(p.name) + ' · edited';
  var s = CONFIG.verdicts[v.verdict];
  return esc(p.name) + ' · ' + s.icon + ' ' + esc(s.headline);
}

function addFarmLayer(layer, name, extra) {
  var id = nextId++;
  var point = isPoint(layer);
  layer.feature = { type: 'Feature', properties: Object.assign({ id: id, name: name }, extra || {}) };
  layer.bindTooltip(labelFor(layer), {
    permanent: drawnItems.getLayers().length < 150, interactive: false, className: 'shape-label',
    direction: point ? 'top' : 'center', offset: point ? [0, -10] : [0, 0]
  });
  layer.on('click', function (ev) {
    if (mode !== 'idle') return;
    L.DomEvent.stopPropagation(ev);
    selectShape(id, false);
    send('farmClicked', { id: id });
  });
  drawnItems.addLayer(layer);
  applyStyle(layer, false);
  return id;
}

// Farms from a file or from Neon (a FeatureCollection of Point / Polygon / MultiPolygon)
function addFeatures(fc) {
  var added = 0;
  L.geoJSON(fc, { pointToLayer: function (f, latlng) { return L.marker(latlng); } }).eachLayer(function (layer) {
    var p = (layer.feature && layer.feature.properties) || {};
    var name = String(p.name || p.plot_id || p.id || ((isPoint(layer) ? 'Point ' : 'Polygon ') + nextId));
    var extra = {};
    if (p.area_ha !== undefined && p.area_ha !== null) extra.area_ha = p.area_ha;
    if (p.supplier_id) extra.supplier_id = p.supplier_id;
    addFarmLayer(layer, name, extra);
    added++;
  });
  sync();
  if (added) map.fitBounds(drawnItems.getBounds(), { padding: [60, 60], maxZoom: 17 });
  return added;
}

// ---------- verdicts from the last check: Python calls setVerdicts({id: {verdict, stale}}) ----------
function setVerdicts(byId) {
  verdicts = byId || {};
  drawnItems.eachLayer(function (l) {
    applyStyle(l, l.feature.properties.id === selectedId);
    if (l.getTooltip()) l.setTooltipContent(labelFor(l));
  });
  refreshHalos();
}
function refreshHalos() {
  haloGroup.clearLayers();
  drawnItems.eachLayer(function (l) {
    var v = verdicts[l.feature.properties.id];
    if (!v || v.stale || isPoint(l)) return;
    L.polygon(l.getLatLngs(), { pane: 'halos', color: '#ffffff', weight: 7, opacity: 0.9,
                                fill: false, interactive: false }).addTo(haloGroup);
  });
}

// ---------- drawing ----------
function startDraw() {
  if (mode !== 'idle') return;
  selectShape(null, false);
  var c = colorFor(nextId);
  drawHandler.setOptions({ shapeOptions: { color: c, fillColor: c, opacity: 1, weight: 4, fillOpacity: 0.38 } });
  activeDraw = drawHandler;
  drawHandler.enable();
}
function startPoint() {
  if (mode !== 'idle') return;
  selectShape(null, false);
  pointHandler.setOptions({ icon: pointIcon(colorFor(nextId), true) });
  activeDraw = pointHandler;
  pointHandler.enable();
}
function finishDraw() { if (activeDraw === drawHandler && drawPoints() >= 3) drawHandler.completeShape(); }
function undoPoint() { if (activeDraw === drawHandler) drawHandler.deleteLastVertex(); }
function cancelDraw() { if (activeDraw) activeDraw.disable(); }

map.on('draw:drawstart', function (e) {
  mode = e.layerType === 'marker' ? 'placing' : 'drawing';
  map.doubleClickZoom.disable();
  report();
});
map.on('draw:drawstop', function () { mode = 'idle'; activeDraw = null; map.doubleClickZoom.enable(); report(); });
map.on('draw:drawvertex', function () {
  var markers = drawHandler._markers || [];
  markers.forEach(function (m, i) {
    if (m._icon) m._icon.classList.toggle('first-vertex', i === 0 && markers.length >= 3);
  });
  report();
});

map.on(L.Draw.Event.CREATED, function (e) {
  var id = addFarmLayer(e.layer, (e.layerType === 'marker' ? 'Point ' : 'Polygon ') + nextId);
  sync();
  selectShape(id, false);
});

// ---------- editing ----------
function startEdit() {
  if (mode !== 'idle' || drawnItems.getLayers().length === 0) return;
  selectShape(null, false);
  editHandler.enable();
}
function saveEdit() { editHandler.save(); editHandler.disable(); }
function cancelEdit() { editHandler.revertLayers(); editHandler.disable(); }

// Enter / Esc: act on whatever mode the map is in right now
function confirmAction() { if (mode === 'editing') saveEdit(); else finishDraw(); }
function cancelAction() {
  if (mode === 'drawing' || mode === 'placing') cancelDraw();
  else if (mode === 'editing') cancelEdit();
  else { selectShape(null, false); send('escape'); }
}

map.on('draw:editstart', function () { mode = 'editing'; report(); });
map.on('draw:editstop', function () { mode = 'idle'; selectShape(null, false); sync(); });
map.on(L.Draw.Event.EDITED, sync);

// ---------- shape management ----------
function deleteShape(id) {
  var layer = findLayer(id);
  if (!layer) return;
  drawnItems.removeLayer(layer);
  if (selectedId === id) selectedId = null;
  sync();
}
function renameShape(id, name) {
  var layer = findLayer(id);
  if (!layer) return;
  layer.feature.properties.name = name;
  if (layer.getTooltip()) layer.setTooltipContent(labelFor(layer));
  sync();
}
function clearAll() {
  if (mode === 'drawing' || mode === 'placing') cancelDraw();
  if (mode === 'editing') cancelEdit();
  drawnItems.clearLayers();
  selectedId = null;
  sync();
}

new QWebChannel(qt.webChannelTransport, function (channel) {
  bridge = channel.objects.bridge;
  sync();
  bridge.ready();
});
```

- [ ] **Step 7: Rewrite `frontend/app.py`** (whole file)

```python
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
from map_area import MapArea
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
    def __init__(self, tile_handler):
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
        basemap.addItem("Streets", "streets")
        basemap.addItem("Satellite", "satellite")
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
            hint = "Press Draw (P) and click on the map to outline a farm."
        elif has_selection:
            hint = "Farm selected. Del to delete, F2 to rename, Esc to deselect."
        else:
            hint = "Click a farm to select it, or press Draw (P) to add another."
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
    window = MainWindow(tile_handler)
    window.showMaximized()
    if args.screenshot:
        schedule_screenshot(window, args.screenshot, args.after)
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
```

- [ ] **Step 8: Run the tests and lint**

Run: `frontend\.venv\Scripts\python -m unittest discover -s frontend/tests -t frontend -v`
Expected: all tests pass, including `test_pages.py` (2 tests) and `test_map_area.py` (2 tests).
Run: `frontend\.venv\Scripts\python -m pyflakes frontend --exclude .venv` (or list the files explicitly if `--exclude` is not supported: `frontend\.venv\Scripts\python -m pyflakes frontend\app.py frontend\pages.py frontend\tiles.py frontend\worker.py frontend\neon_service.py frontend\legend.py frontend\backend.py frontend\map_area.py`)
Expected: no output.

- [ ] **Step 9: Run the app and check nothing broke**

Run: `frontend\.venv\Scripts\python frontend\app.py`
Expected:
- the window opens as before: Draw, Edit, Farm dropdown, Basemap and Export;
- drawing a polygon, renaming it (F2), editing and saving it, and View GeoJSON all work;
- the terminal shows no `[map] ... Error` lines.

Then: `frontend\.venv\Scripts\python frontend\app.py --screenshot %TEMP%\task5.png --after 8` and look at the picture.

- [ ] **Step 10: Checkpoint.** `git add frontend/pages.py frontend/map_area.py frontend/web frontend/app.py frontend/tests/test_pages.py frontend/tests/test_map_area.py` · `git commit -m "frontend: pages infrastructure, map page in web/, worker in the window"`

---

### Task 6: The colour-coded EUDR layer, with a native legend and banner

**Files:**
- Create: `frontend/eudr_layer.py`, `frontend/web/map_eudr.js`, `frontend/tests/test_eudr_layer.py`
- Modify: `frontend/web/map.html` (one include line), `frontend/app.py` (create the feature)

**Interfaces:**
- Consumes:
  - `MainWindow.run_job`, `insert_tools`, `basemap_anchor`, `icon`, `emit`, `tile_handler`, `worker.service`, `map_area`
  - `NeonService.open_map()`, plus `.grid`, `.classes` and `.to_map`
  - map JS: `bounds`, `aoi`, `lockView`, `addCss`
- Produces:
  - `eudr_layer.share_text(pct) -> str`, `colour_icon(colour) -> QIcon`
  - `Legend(QFrame)`, with signal `hidden_changed(list of codes)` and `set_info(info)`
  - `Banner(QFrame)`, with signal `retry()` and `show_message(text, error=False)`
  - `EudrLayer(window)`, which sets `window.map_info` and emits the window event `"map_loaded"(info)`
  - map JS: `setMapInfo(info)`, `setHiddenClasses(codes)`, `setOverlayOpacity(0..1)`, `setOverlayVisible(bool)`

- [ ] **Step 1: Write the failing test** `frontend/tests/test_eudr_layer.py`:

```python
import unittest

import eudr_layer
from tests.qt_app import qt_app

INFO = {"map_version": "jambi-v1", "width": 6000, "height": 6000,
        "classes": [{"code": 0, "label": "Land", "colour": "#E1E1E1", "share": 64.93},
                    {"code": 1, "label": "Water", "colour": "#6BAED6", "share": 0.097},
                    {"code": 3, "label": "Deforestation", "colour": "#E31A1C", "share": 2.26}]}


class ShareTextTest(unittest.TestCase):
    def test_share_text(self):
        self.assertEqual([eudr_layer.share_text(p) for p in (64.93, 2.26, 0.097, 0.0)],
                         ["65%", "2.3%", "<0.1%", "<0.1%"])


class LegendTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        qt_app()

    def test_unticking_a_class_hides_it(self):
        legend = eudr_layer.Legend()
        legend.set_info(INFO)
        seen = []
        legend.hidden_changed.connect(seen.append)
        boxes = legend.checkboxes
        self.assertEqual([b.text() for b in boxes], ["Land", "Water", "Deforestation"])
        boxes[0].setChecked(False)
        boxes[2].setChecked(False)
        boxes[0].setChecked(True)
        self.assertEqual(seen, [[0], [0, 3], [3]])

    def test_banner_shows_retry_only_for_errors(self):
        banner = eudr_layer.Banner()
        banner.show_message("Loading the map from Neon…")
        self.assertTrue(banner.button.isHidden())
        banner.show_message("Could not load the map from Neon. offline", error=True)
        self.assertFalse(banner.button.isHidden())
        self.assertEqual(banner.property("tone"), "error")
```

- [ ] **Step 2: Run it and see it fail**

Run: `frontend\.venv\Scripts\python -m unittest discover -s frontend/tests -t frontend -p test_eudr_layer.py -v`
Expected: ERROR, `No module named 'eudr_layer'`

- [ ] **Step 3: Write `frontend/web/map_eudr.js`** (map layer only; the legend and banner are Qt)

```javascript
// map_eudr.js - the colour-coded EUDR tile layer. Tiles come from Python (eudr://tiles/...), drawn
// from the stencils stored in Neon. The legend and the loading banner are Qt widgets over the map.
addCss('.eudr-tiles img { image-rendering: pixelated; }');

var eudr = { layer: null, hidden: [], opacity: 0.7, visible: true };

function tileUrl() { return 'eudr://tiles/{z}/{x}/{y}.png?hide=' + eudr.hidden.join(','); }

function setMapInfo(info) {
  bounds = info.bounds;  // lock the view to the map stored in Neon
  map.setMaxBounds(bounds);
  aoi.setBounds(bounds);
  lockView(true);
  if (eudr.layer) map.removeLayer(eudr.layer);
  eudr.layer = L.tileLayer(tileUrl(), {
    bounds: bounds, opacity: eudr.opacity, maxZoom: 19, zIndex: 5,
    className: 'eudr-tiles', keepBuffer: 4
  });
  if (eudr.visible) eudr.layer.addTo(map);
}

function setHiddenClasses(codes) {
  eudr.hidden = codes.slice();
  if (eudr.layer) eudr.layer.setUrl(tileUrl());
}
function setOverlayOpacity(value) { eudr.opacity = value; if (eudr.layer) eudr.layer.setOpacity(value); }
function setOverlayVisible(on) {
  eudr.visible = on;
  if (!eudr.layer) return;
  if (on) eudr.layer.addTo(map); else map.removeLayer(eudr.layer);
}
```

- [ ] **Step 4: Include it in `frontend/web/map.html`**: after the line `<script>/*INCLUDE:map.js*/</script>` add:

```html
<script>/*INCLUDE:map_eudr.js*/</script>
```

- [ ] **Step 5: Write `frontend/eudr_layer.py`**

```python
"""eudr_layer.py - the colour-coded EUDR map: loads it from Neon at start-up, then controls the layer.

NeonService.open_map() reads grid_info and every stencil page (one query, about 1.3 MB) and
unpacks the whole map in memory; tiles.py draws the tiles from that, with no database work per
tile. The legend and the loading/error banner are Qt widgets floating over the map. The toolbar
gets a Layers menu and an opacity slider; the status bar says whether Neon is connected.
"""

from functools import partial

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor, QIcon, QPixmap
from PyQt5.QtWidgets import (QAction, QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QMenu,
                             QPushButton, QSlider, QToolButton)


def share_text(pct):
    return f"{pct:.0f}%" if pct >= 10 else (f"{pct:.1f}%" if pct >= 0.1 else "<0.1%")


def colour_icon(colour, size=12):
    pix = QPixmap(size, size)
    pix.fill(QColor(colour))
    return QIcon(pix)


def label(text, role=None):
    widget = QLabel(text)
    widget.setTextFormat(Qt.PlainText)
    if role:
        widget.setProperty("role", role)
    return widget


class Legend(QFrame):
    """The map's key: each class's colour, name and share of the map. Unticking a class hides it
    on the map; hidden_changed gives the sorted list of hidden class codes."""

    hidden_changed = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("overlay")
        self.hidden = set()
        self.checkboxes = []
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
        note = label(f"From the stencils in Neon · {info['width']:,} × {info['height']:,} px", "muted")
        self.grid.addWidget(note, len(info["classes"]) + 1, 0, 1, 2)
        self.show()

    def toggle(self, code, visible):
        if visible:
            self.hidden.discard(code)
        else:
            self.hidden.add(code)
        self.hidden_changed.emit(sorted(self.hidden))


class Banner(QFrame):
    """A message floating over the top of the map: 'Loading…', or an error with a Retry button."""

    retry = pyqtSignal()

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

    def show_message(self, text, error=False):
        self.setProperty("tone", "error" if error else "info")
        self.style().unpolish(self)
        self.style().polish(self)
        self.text.setText(text)
        self.button.setVisible(error)
        self.show()


class EudrLayer:
    def __init__(self, window):
        self.w = window
        self.info = None

        self.neon_label = QLabel("Neon: connecting…")
        window.statusBar().addPermanentWidget(self.neon_label)

        self.legend = Legend()
        self.legend.hidden_changed.connect(lambda codes: window.map.call("setHiddenClasses", codes))
        window.map_area.add_overlay(self.legend, "bottom-left", (12, 34))  # above Leaflet's scale bar
        self.banner = Banner()
        self.banner.retry.connect(self.load)
        window.map_area.add_overlay(self.banner, "top-center", (0, 12))

        self.layer_act = QAction("EUDR map (from Neon)", window, checkable=True, checked=True)
        self.layer_act.toggled.connect(self.set_visible)
        self.layers_menu = QMenu(window)
        self.layers_menu.addAction(self.layer_act)
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
        self.load()

    def set_visible(self, on):
        self.w.map.call("setOverlayVisible", on)
        self.legend.setVisible(on and self.info is not None)

    def load(self):
        self.neon_label.setText("Neon: connecting…")
        self.banner.show_message("Loading the map from Neon…")
        self.w.map_area.relayout()
        self.w.run_job("open_map", "open_map", done=self.loaded, failed=self.failed)

    def loaded(self, info):
        service = self.w.worker.service
        self.w.tile_handler.set_map(service.grid, service.classes, service.to_map)
        self.info = info
        self.w.map.call("setMapInfo", info)
        self.legend.set_info(info)
        self.banner.hide()
        self.w.map_area.relayout()
        self.neon_label.setText(f"Neon: connected · {info['map_version']} · "
                                f"{info['width']:,} × {info['height']:,} px")
        self.w.map_info = info
        self.w.emit("map_loaded", info)

    def failed(self, message):
        self.neon_label.setText("Neon: not connected")
        self.banner.show_message(f"Could not load the map from Neon. {message}", error=True)
        self.w.map_area.relayout()
        return True  # shown over the map, with a Retry button
```

- [ ] **Step 6: Create the feature in `frontend/app.py`**
  - Add `from eudr_layer import EudrLayer` to the imports.
  - Below `# ----- features: each module adds its own actions, pages and listeners -----`, add:

```python
        self.eudr = EudrLayer(self)
```

- [ ] **Step 7: Run the tests**

Run: `frontend\.venv\Scripts\python -m unittest discover -s frontend/tests -t frontend -v`
Expected: all pass (`test_eudr_layer.py`: 3 tests).

- [ ] **Step 8: Run the app and check the coloured map**

Run: `frontend\.venv\Scripts\python frontend\app.py`
Expected:
- Within about 5–10 s, the "Loading the map from Neon…" banner (a Qt widget at the top of the map) disappears, and the coloured map covers 103.0–103.5 °E. The dashed box matches it and the view is locked to it.
- The Qt legend at the bottom left lists Land ~65%, Water 0.1%, Forest ~29%, Deforestation ~2.3% and Needs review ~3.0%. Unticking Land hides the grey.
- The opacity slider fades the layer, and Layers ▸ EUDR map toggles it and the legend.
- The status bar shows `Neon: connected · jambi-v1 · 6,000 × 6,000 px`.
- At maximum zoom you see crisp square pixels.
- The terminal shows no `[map]` errors.

Then take a screenshot: `frontend\.venv\Scripts\python frontend\app.py --screenshot %TEMP%\task6.png --after 15`, and look at it.

- [ ] **Step 9: Check the offline path.** Run with an unreachable database: `$env:DATABASE_URL="postgresql://u:p@127.0.0.1:9/x?connect_timeout=3"; frontend\.venv\Scripts\python frontend\app.py`. Expected: the banner turns into a red-edged error, "Could not load the map from Neon. Could not connect to the database: …", with a Retry button, and drawing still works. Afterwards, run `Remove-Item Env:DATABASE_URL`.

- [ ] **Step 10: Checkpoint.** `git add frontend/eudr_layer.py frontend/web/map_eudr.js frontend/web/map.html frontend/app.py frontend/tests/test_eudr_layer.py` · `git commit -m "frontend: colour-coded EUDR layer from Neon, native legend"`

---

## Phase 2: testing points and GeoJSONs, and the focus view

### Task 7: Results as JSON, and the plain-English verdict card

**Files:**
- Create: `frontend/report.py`, `frontend/tests/test_report.py`

**Interfaces:**
- Consumes: `common.PlotResult` (`verdict`, `present`, `point_colour`, `water_share`, `outside`, `note`, `ha(c)`, `key()`); `legend`.
- Produces:
  - `report.METHOD_KEYS`
  - `worst(verdicts) -> str | None`
  - `result_json(r) -> dict` with keys `verdict, colours, codes, point_colour, ha{"code": hectares}, water_share, outside, note`
  - `farm_row(key, name, kind, area_ha, results) -> dict` with keys `key, name, kind, area_ha, verdict, results{method: result_json}, agree{stencils_tiff, polygons_tiff, stencils_polygons}`
  - `summarize(rows, seconds) -> dict` with keys `n, points, outlines, methods, verdicts{method: {verdict: n}}, agree{pair: {same, of, pct}}, timing{method: {total_s, per_farm_ms}}, forest{points_in_forest, outlines_overlapping}`
  - `verdict_card(row) -> dict` with keys `verdict, colour, icon, headline, sentence, source`
  - `fmt_ha(ha) -> str`, `fmt_pct(pct) -> str`

- [ ] **Step 1: Write the failing test** `frontend/tests/test_report.py`:

```python
import unittest

from shapely.geometry import Point, box

import report
from backend import common
from legend import DEFORESTATION, FOREST, LAND, NO_DATA, REVIEW, WATER


def outline_farm(name="F"):
    return common.Farm(name, {}, box(103.1, -1.91, 103.11, -1.9), "outline")


def point_farm(name="P"):
    return common.Farm(name, {}, Point(103.1, -1.9), "point")


def exact(areas_m2, name="F"):
    """An answer like the outlines give, from areas in m2 (the farm's area is their sum)."""
    return common.exact_result(outline_farm(name), dict(areas_m2), sum(areas_m2.values()))


def row_for(stencils, polygons, tiff_pixel=None, tiff_exact=None, kind="outline", area_ha=1.0, key=1, name="F"):
    results = {"stencils": stencils, "polygons": polygons}
    if tiff_pixel is not None:
        results["tiff_pixel"], results["tiff_exact"] = tiff_pixel, tiff_exact
    return report.farm_row(key, name, kind, area_ha, results)


class RowTest(unittest.TestCase):
    def test_worst(self):
        self.assertEqual(report.worst(["PASS", "FAIL", None]), "FAIL")
        self.assertEqual(report.worst(["CHECK_LOCATION", "CANNOT_CHECK"]), "CANNOT_CHECK")
        self.assertIsNone(report.worst([None]))

    def test_agreement_follows_compare_py(self):
        a, b = exact({LAND: 9000, FOREST: 1000}), exact({LAND: 10000})
        row = row_for(a, a, tiff_pixel=a, tiff_exact=b)
        self.assertEqual(row["agree"], {"stencils_tiff": True, "polygons_tiff": False, "stencils_polygons": True})
        self.assertIsNone(row_for(a, a)["agree"]["stencils_tiff"])

    def test_result_json(self):
        r = report.result_json(exact({LAND: 9000, DEFORESTATION: 1000}))
        self.assertEqual(r["verdict"], "FAIL")
        self.assertEqual(r["codes"], [LAND, DEFORESTATION])
        self.assertAlmostEqual(r["ha"][str(DEFORESTATION)], 0.1)


class CardTest(unittest.TestCase):
    def card(self, stencils, polygons, kind="outline", area_ha=1.0):
        return report.verdict_card(row_for(stencils, polygons, kind=kind, area_ha=area_ha))

    def test_fail(self):
        r = exact({LAND: 9000, DEFORESTATION: 1000})
        card = self.card(r, r)
        self.assertEqual((card["headline"], card["icon"], card["colour"]), ("Not compliant", "✖", "#d03b3b"))
        self.assertEqual(card["sentence"], "Deforestation after 31 Dec 2020 on 0.10 ha (10.0% of the farm).")

    def test_the_worse_approach_wins(self):
        card = self.card(exact({LAND: 9990, DEFORESTATION: 10}), exact({LAND: 10000}))
        self.assertEqual((card["verdict"], card["source"]), ("FAIL", "stencils"))
        self.assertEqual(card["sentence"], "Deforestation after 31 Dec 2020 on <0.01 ha (0.1% of the farm).")

    def test_needs_review(self):
        r = exact({LAND: 7500, REVIEW: 2500})
        self.assertEqual(self.card(r, r)["sentence"],
                         "0.25 ha (25.0% of the farm) may have been cleared after 2020, so a person has to check.")

    def test_check_location(self):
        r = exact({LAND: 4000, WATER: 6000})
        self.assertEqual(self.card(r, r)["sentence"],
                         "60.0% of the farm is water, so its coordinates are probably wrong.")

    def test_no_data(self):
        r = exact({LAND: 9500, NO_DATA: 500})
        self.assertEqual(self.card(r, r)["sentence"], "0.05 ha of the farm has no data.")

    def test_pass_with_forest(self):
        r = exact({LAND: 5000, FOREST: 5000})
        card = self.card(r, r)
        self.assertEqual(card["headline"], "Compliant")
        self.assertEqual(card["sentence"],
                         "No deforestation after 31 Dec 2020. Forest inside: 0.50 ha (allowed under the EUDR).")

    def test_outline_outside_the_map(self):
        r = common.outside_result(outline_farm())
        self.assertEqual(self.card(r, r)["sentence"], "Part of the farm is outside the map, so it can't be checked.")

    def test_points(self):
        def card_for(colour):
            r = common.point_result(point_farm(), colour)
            return self.card(r, r, kind="point", area_ha=0.0)

        self.assertEqual(card_for(DEFORESTATION)["sentence"],
                         "The point is on deforestation: forest cleared after 31 Dec 2020.")
        self.assertEqual(card_for(REVIEW)["sentence"],
                         "The point is on land flagged for review: it may have been cleared after 2020, "
                         "so a person has to check.")
        self.assertEqual(card_for(WATER)["sentence"], "The point is on water, so its coordinates are probably wrong.")
        self.assertEqual(card_for(FOREST)["sentence"], "The point is on forest. Forest is allowed under the EUDR.")
        self.assertEqual(card_for(LAND)["sentence"], "The point is on land.")
        outside = common.outside_result(point_farm())
        self.assertEqual(self.card(outside, outside, kind="point", area_ha=0.0)["sentence"],
                         "The point is outside the map.")


class SummaryTest(unittest.TestCase):
    def test_counts_agreement_timing_and_forest(self):
        fail, ok = exact({LAND: 9000, DEFORESTATION: 1000}), exact({LAND: 5000, FOREST: 5000})
        forest_point = common.point_result(point_farm(), FOREST)
        rows = [row_for(fail, fail, key=1), row_for(ok, ok, key=2),
                report.farm_row(3, "P", "point", 0.0, {"stencils": forest_point, "polygons": forest_point})]
        s = report.summarize(rows, {"stencils": 0.03, "polygons": 0.06})
        self.assertEqual(s["verdicts"]["stencils"]["FAIL"], 1)
        self.assertEqual(s["verdicts"]["polygons"]["PASS"], 2)
        self.assertEqual(s["agree"]["stencils_polygons"], {"same": 3, "of": 3, "pct": 100.0})
        self.assertEqual(s["agree"]["stencils_tiff"], {"same": 0, "of": 0, "pct": None})
        self.assertAlmostEqual(s["timing"]["polygons"]["per_farm_ms"], 20.0)
        self.assertEqual(s["forest"], {"points_in_forest": 1, "outlines_overlapping": 1})
        self.assertEqual((s["n"], s["points"], s["outlines"]), (3, 1, 2))
```

- [ ] **Step 2: Run it and see it fail**

Run: `frontend\.venv\Scripts\python -m unittest discover -s frontend/tests -t frontend -p test_report.py -v`
Expected: ERROR, `No module named 'report'`

- [ ] **Step 3: Write `frontend/report.py`**

```python
"""report.py - turns the backend's answers (common.PlotResult) into plain JSON for the pages, and
writes the plain-English verdict card. Pure functions: no database, no Qt."""

from legend import (CLASS_LABELS, CLASSES, DEFORESTATION, FOREST, NO_DATA, REVIEW, VERDICT_ORDER,
                    VERDICT_STYLE)

METHOD_KEYS = ("stencils", "polygons", "tiff_pixel", "tiff_exact")


def worst(verdicts):
    """The worst of some verdicts (FAIL first), ignoring missing ones."""
    present = [v for v in verdicts if v]
    return min(present, key=VERDICT_ORDER.index) if present else None


def result_json(r):
    """One method's answer for one farm."""
    codes = sorted(int(c) for c in r.present)
    return {
        "verdict": r.verdict,
        "colours": [CLASS_LABELS[c] for c in codes],
        "codes": codes,
        "point_colour": None if r.point_colour is None else int(r.point_colour),
        "ha": {str(c): round(r.ha(c), 6) for c in CLASSES if r.ha(c) > 0},
        "water_share": round(float(r.water_share), 6),
        "outside": bool(r.outside),
        "note": r.note,
    }


def farm_row(key, name, kind, area_ha, results):
    """One row of the Results table. results = {method: PlotResult}: stencils and polygons always,
    tiff_pixel and tiff_exact when the TIFF was used. Agreement is measured as compare.py does:
    stencils against the TIFF pixel rule, outlines against the TIFF exact areas, on key()."""
    stencils, polygons = results["stencils"], results["polygons"]
    tiff_pixel, tiff_exact = results.get("tiff_pixel"), results.get("tiff_exact")
    return {
        "key": key, "name": name, "kind": kind, "area_ha": round(float(area_ha), 6),
        "verdict": worst([stencils.verdict, polygons.verdict]),
        "results": {method: result_json(r) for method, r in results.items()},
        "agree": {
            "stencils_tiff": None if tiff_pixel is None else stencils.key() == tiff_pixel.key(),
            "polygons_tiff": None if tiff_exact is None else polygons.key() == tiff_exact.key(),
            "stencils_polygons": stencils.verdict == polygons.verdict,
        },
    }


def _share(values):
    same = sum(1 for v in values if v)
    return {"same": same, "of": len(values), "pct": round(100.0 * same / len(values), 2) if values else None}


def summarize(rows, seconds):
    """What the Statistics tab shows about the last check. seconds = {method: batch seconds}."""
    n = len(rows)
    methods = [m for m in METHOD_KEYS if m in seconds]
    verdicts = {m: {v: 0 for v in VERDICT_ORDER} for m in methods}
    for row in rows:
        for m in methods:
            verdicts[m][row["results"][m]["verdict"]] += 1
    agree = {pair: _share([row["agree"][pair] for row in rows if row["agree"][pair] is not None])
             for pair in ("stencils_tiff", "polygons_tiff", "stencils_polygons")}
    points = [r for r in rows if r["kind"] == "point"]
    outlines = [r for r in rows if r["kind"] == "outline"]
    return {
        "n": n, "points": len(points), "outlines": len(outlines), "methods": methods,
        "verdicts": verdicts,
        "agree": agree,
        "timing": {m: {"total_s": seconds[m], "per_farm_ms": 1000.0 * seconds[m] / n if n else None}
                   for m in methods},
        "forest": {
            "points_in_forest": sum(1 for r in points if r["results"]["polygons"]["point_colour"] == FOREST),
            "outlines_overlapping": sum(1 for r in outlines if FOREST in r["results"]["polygons"]["codes"]),
        },
    }


def fmt_ha(ha):
    return "<0.01 ha" if 0 < ha < 0.005 else f"{ha:,.2f} ha"


def fmt_pct(pct):
    return "<0.1%" if 0 < pct < 0.05 else f"{pct:.1f}%"


def _point_sentence(verdict, res):
    if res["outside"]:
        return "The point is outside the map."
    if verdict == "FAIL":
        return "The point is on deforestation: forest cleared after 31 Dec 2020."
    if verdict == "MANUAL_REVIEW":
        return ("The point is on land flagged for review: it may have been cleared after 2020, "
                "so a person has to check.")
    if verdict == "CHECK_LOCATION":
        return "The point is on water, so its coordinates are probably wrong."
    if verdict == "CANNOT_CHECK":
        return "The point is on a pixel with no data."
    colour = res["point_colour"]
    text = f"The point is on {CLASS_LABELS.get(colour, 'an unknown colour').lower()}."
    return text + (" Forest is allowed under the EUDR." if colour == FOREST else "")


def _outline_sentence(verdict, res, area_ha):
    def ha(code):
        return float(res["ha"].get(str(code), 0.0))

    def pct(code):
        return fmt_pct(100.0 * ha(code) / area_ha) if area_ha else "?"

    if res["outside"]:
        return "Part of the farm is outside the map, so it can't be checked."
    if verdict == "FAIL":
        return f"Deforestation after 31 Dec 2020 on {fmt_ha(ha(DEFORESTATION))} ({pct(DEFORESTATION)} of the farm)."
    if verdict == "MANUAL_REVIEW":
        return (f"{fmt_ha(ha(REVIEW))} ({pct(REVIEW)} of the farm) may have been cleared after 2020, "
                "so a person has to check.")
    if verdict == "CHECK_LOCATION":
        return f"{fmt_pct(100.0 * res['water_share'])} of the farm is water, so its coordinates are probably wrong."
    if verdict == "CANNOT_CHECK":
        return f"{fmt_ha(ha(NO_DATA))} of the farm has no data."
    forest = ha(FOREST)
    return "No deforestation after 31 Dec 2020." + (
        f" Forest inside: {fmt_ha(forest)} (allowed under the EUDR)." if forest > 0 else "")


def verdict_card(row):
    """The focus view's headline and sentence. The headline is the worse of the two approaches'
    verdicts; the hectares come from the approach that gave it (the outlines on a tie)."""
    verdict = row["verdict"]
    colour, icon, headline = VERDICT_STYLE[verdict]
    source = "polygons" if row["results"]["polygons"]["verdict"] == verdict else "stencils"
    res = row["results"][source]
    sentence = (_point_sentence(verdict, res) if row["kind"] == "point"
                else _outline_sentence(verdict, res, row["area_ha"]))
    return {"verdict": verdict, "colour": colour, "icon": icon, "headline": headline,
            "sentence": sentence, "source": source}
```

- [ ] **Step 4: Run the test and see it pass**

Run: `frontend\.venv\Scripts\python -m unittest discover -s frontend/tests -t frontend -p test_report.py -v`
Expected: `Ran 13 tests ... OK`

- [ ] **Step 5: Checkpoint.** `git add frontend/report.py frontend/tests/test_report.py` · `git commit -m "frontend: results as JSON and the verdict card"`

---

### Task 8: Checking farms against Neon

**Files:**
- Create: `frontend/farms.py`, `frontend/tests/test_farms.py`, `frontend/tests/test_check_service.py`
- Modify: `frontend/neon_service.py` (imports; append methods)

**Interfaces:**
- Consumes:
  - `common.farms_from_features`, `common.area_m2`, `common.save_results`, `common.check_with_tiff`, `common.table_exists`
  - `stencil_approach.check(conn, grid, farms, all_touched)`, `polygon_approach.check(conn, grid, farms)`
  - `report.farm_row`, `report.summarize`
- Produces:
  - `farms.SUPPORTED`
  - `farms.prepare_farms(features, grid, point_mode="pixel") -> (farms, keys, skipped[(name, reason)])`
  - `farms.farm_area_ha(farm, grid) -> float`
  - `farms.features_from_file(data) -> (features, skipped)`, where each feature has `properties.name` and no `id`
  - `NeonService.check(features, point_mode="pixel", rule="touched", with_tiff=True, save=False) -> {"rows", "summary", "skipped": [{name, reason}], "options": {point_mode, rule, tiff, saved}}`
  - `NeonService.saved_farms() -> {"features", "verdicts": {plot_id: {method: verdict}}, "skipped"}`

- [ ] **Step 1: Write the failing tests** `frontend/tests/test_farms.py`:

```python
import unittest

import farms as farms_mod
from backend import common


def make_grid():
    return common.Grid("test", 103.0, -1.8, 1 / 12000, 1 / 12000, 6000, 6000, 4326, True,
                       default_class=0, fingerprint="test")


def square(lon, lat, size=0.002):
    return {"type": "Polygon", "coordinates": [[[lon, lat], [lon + size, lat], [lon + size, lat - size],
                                                [lon, lat - size], [lon, lat]]]}


def feature(id_, name, geometry, **props):
    return {"type": "Feature", "properties": dict(props, id=id_, name=name), "geometry": geometry}


class PrepareFarmsTest(unittest.TestCase):
    def setUp(self):
        self.grid = make_grid()

    def test_points_outlines_and_skips(self):
        features = [
            feature(1, "P", {"type": "Point", "coordinates": [103.1, -1.9]}),
            feature(2, "A", square(103.2, -2.0)),
            feature(3, "Line", {"type": "LineString", "coordinates": [[103.1, -1.9], [103.2, -1.9]]}),
            feature(4, "Empty", None),
            feature(5, "A", square(103.3, -2.1)),
        ]
        farms, keys, skipped = farms_mod.prepare_farms(features, self.grid)
        self.assertEqual([f.plot_id for f in farms], ["P", "A", "A-2"])
        self.assertEqual(keys, [1, 2, 5])
        self.assertEqual([f.kind for f in farms], ["point", "outline", "outline"])
        self.assertEqual(skipped, [("Line", "unsupported shape: LineString"),
                                   ("Empty", "unsupported shape: no geometry")])

    def test_a_bow_tie_is_repaired(self):
        bow = {"type": "Polygon", "coordinates": [[[103.1, -1.9], [103.11, -1.91], [103.11, -1.9],
                                                   [103.1, -1.91], [103.1, -1.9]]]}
        farms, _, skipped = farms_mod.prepare_farms([feature(1, "Bow", bow)], self.grid)
        self.assertEqual((len(farms), skipped), (1, []))
        self.assertTrue(farms[0].geom.is_valid)

    def test_circle_mode_turns_points_with_an_area_into_outlines(self):
        f = feature(1, "C", {"type": "Point", "coordinates": [103.1, -1.9]}, area_ha=4)
        farms, _, _ = farms_mod.prepare_farms([f], self.grid, point_mode="circle")
        self.assertEqual(farms[0].kind, "outline")
        self.assertAlmostEqual(farms_mod.farm_area_ha(farms[0], self.grid), 4, delta=0.05)

    def test_features_from_a_file(self):
        data = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "id": "f1", "properties": {"plot_id": "PLOT-7", "area_ha": 2},
             "geometry": {"type": "Point", "coordinates": [103.1, -1.9]}},
            {"type": "Feature", "properties": {}, "geometry": {"type": "GeometryCollection", "geometries": []}}]}
        kept, skipped = farms_mod.features_from_file(data)
        self.assertEqual(kept[0]["properties"], {"plot_id": "PLOT-7", "area_ha": 2, "name": "PLOT-7"})
        self.assertEqual(skipped, [("farm 2", "unsupported shape: GeometryCollection")])
        bare, _ = farms_mod.features_from_file(square(103.2, -2.0))
        self.assertEqual(len(bare), 1)
```

And `frontend/tests/test_check_service.py`:

```python
import unittest
from unittest import mock

import neon_service
from backend import common


class FakeConnection:
    closed = False

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def make_service():
    service = neon_service.NeonService(connect=FakeConnection)
    service.grid = common.Grid("test", 103.0, -1.8, 1 / 12000, 1 / 12000, 6000, 6000, 4326, True,
                               default_class=0, fingerprint="test")
    return service


def forest_everywhere(conn, grid, farms, *args):
    return [common.point_result(f, common.FOREST) for f in farms], {"total_s": 0.01}


class CheckTest(unittest.TestCase):
    def test_rows_follow_the_map_ids_and_skips_are_reported(self):
        features = [
            {"type": "Feature", "properties": {"id": 7, "name": "P"},
             "geometry": {"type": "Point", "coordinates": [103.1, -1.9]}},
            {"type": "Feature", "properties": {"id": 9, "name": "L"},
             "geometry": {"type": "LineString", "coordinates": [[103.1, -1.9], [103.2, -1.9]]}},
        ]
        with mock.patch.object(neon_service.stencil_approach, "check", forest_everywhere), \
                mock.patch.object(neon_service.polygon_approach, "check", forest_everywhere):
            out = make_service().check(features)
        self.assertEqual([r["key"] for r in out["rows"]], [7])
        self.assertEqual(out["rows"][0]["verdict"], "PASS")
        self.assertEqual(out["skipped"], [{"name": "L", "reason": "unsupported shape: LineString"}])
        self.assertFalse(out["options"]["tiff"])
        self.assertEqual(out["summary"]["verdicts"]["stencils"]["PASS"], 1)

    def test_nothing_to_check(self):
        out = make_service().check([])
        self.assertEqual((out["rows"], out["summary"]["n"]), ([], 0))
```

- [ ] **Step 2: Run them and see them fail**

Run: `frontend\.venv\Scripts\python -m unittest discover -s frontend/tests -t frontend -p "test_*.py" -v`
Expected: ERRORs: `No module named 'farms'`; `'NeonService' object has no attribute 'check'`

- [ ] **Step 3: Write `frontend/farms.py`**

```python
"""farms.py - farms from the map or from a file -> the backend's Farm objects (common.Farm)."""

from backend import common

SUPPORTED = ("Point", "Polygon", "MultiPolygon")


def _skip_reason(geometry):
    return f"unsupported shape: {geometry.get('type') or 'no geometry'}"


def prepare_farms(features, grid, point_mode="pixel"):
    """(farms, keys, skipped): one common.Farm per usable feature, the matching feature keys
    (the map's ids; the position when there is no id), and [(name, reason)] for features that
    can't be checked. plot_id = the farm's name, made unique the way the backend does
    (Name, Name-2, ...)."""
    farms, keys, skipped, seen = [], [], [], {}
    for i, feature in enumerate(features):
        props = dict(feature.get("properties") or {})
        name = str(props.get("name") or props.get("plot_id") or props.get("id") or f"farm {i + 1}")
        geometry = feature.get("geometry") or {}
        if geometry.get("type") not in SUPPORTED:
            skipped.append((name, _skip_reason(geometry)))
            continue
        seen[name] = seen.get(name, 0) + 1
        props["plot_id"] = name if seen[name] == 1 else f"{name}-{seen[name]}"
        made = common.farms_from_features([{"type": "Feature", "properties": props, "geometry": geometry}],
                                          grid, point_mode)
        if not made:
            skipped.append((name, "the outline could not be repaired"))
            continue
        farms.append(made[0])
        keys.append(props.get("id", i))
    return farms, keys, skipped


def farm_area_ha(farm, grid):
    return 0.0 if farm.kind == "point" else common.area_m2(farm.geom, grid) / 10_000


def features_from_file(data):
    """A loaded GeoJSON file -> (features to put on the map, [(name, reason)] skipped).
    Accepts a FeatureCollection, a Feature or a bare geometry, like common.load_farms."""
    if data.get("type") == "FeatureCollection":
        features = data.get("features") or []
    elif data.get("type") == "Feature":
        features = [data]
    else:
        features = [{"type": "Feature", "properties": {}, "geometry": data}]
    kept, skipped = [], []
    for i, feature in enumerate(features):
        props = dict(feature.get("properties") or {})
        name = str(props.get("plot_id") or props.get("name") or props.get("id") or feature.get("id")
                   or f"farm {i + 1}")
        geometry = feature.get("geometry") or {}
        if geometry.get("type") not in SUPPORTED:
            skipped.append((name, _skip_reason(geometry)))
            continue
        props["name"] = name
        props.pop("id", None)  # the map gives every farm its own id
        kept.append({"type": "Feature", "properties": props, "geometry": geometry})
    return kept, skipped
```

- [ ] **Step 4: Update the imports at the top of `frontend/neon_service.py`.** Replace:

```python
from backend import common, stencil_approach
from legend import CLASS_LABELS, CLASSES, MAP_COLOURS
```

with:

```python
from backend import common, polygon_approach, stencil_approach
from farms import farm_area_ha, features_from_file, prepare_farms
from legend import CLASS_LABELS, CLASSES, MAP_COLOURS
from report import farm_row, summarize
```

- [ ] **Step 5: Append these methods to the end of `frontend/neon_service.py`** (inside `class NeonService`, indented 4 spaces)

```python

    # ----- checking farms -----
    def check(self, features, point_mode="pixel", rule="touched", with_tiff=True, save=False):
        """Check farms with both approaches against Neon, and with the TIFF when it is the same map.
        features: GeoJSON features from the map (properties: id, name). Each approach runs in its
        own transaction, as a real request would."""
        grid, all_touched = self.grid, rule == "touched"
        farms, keys, skipped = prepare_farms(features, grid, point_mode)
        results, seconds = {}, {}

        def run(conn):
            for method, check in (
                    ("stencils", lambda: stencil_approach.check(conn, grid, farms, all_touched)),
                    ("polygons", lambda: polygon_approach.check(conn, grid, farms))):
                answers, timings = check()
                results[method], seconds[method] = answers, timings["total_s"]
                conn.rollback()
            if save:
                for method in ("stencils", "polygons"):
                    common.save_results(conn, grid, farms, results[method], method)

        use_tiff = bool(with_tiff and self.tif_path and farms)
        if farms:
            self.on_status(f"Checking {len(farms)} farm{'' if len(farms) == 1 else 's'} against Neon…")
            self._run(run, write=save)
            if use_tiff:
                for method, mode in (("tiff_pixel", "pixel"), ("tiff_exact", "exact")):
                    answers, timings = common.check_with_tiff(self.tif_path, grid, farms, mode, all_touched)
                    results[method], seconds[method] = answers, timings["total_s"]
        rows = [farm_row(key, farm.plot_id, farm.kind, farm_area_ha(farm, grid),
                         {method: answers[i] for method, answers in results.items()})
                for i, (key, farm) in enumerate(zip(keys, farms))]
        return {"rows": rows, "summary": summarize(rows, seconds),
                "skipped": [{"name": n, "reason": r} for n, r in skipped],
                "options": {"point_mode": point_mode, "rule": rule, "tiff": use_tiff,
                            "saved": bool(save and farms)}}

    def saved_farms(self):
        """Farms saved in Neon (the plots table) and their stored verdicts for this map version."""
        def load(conn):
            if not common.table_exists(conn, "plots"):
                return [], []
            with conn.cursor() as cur:
                cur.execute("SELECT plot_id, geojson FROM plots ORDER BY plot_id")
                plots = cur.fetchall()
                cur.execute("SELECT plot_id, method, verdict FROM plot_results WHERE map_version = %s",
                            (self.grid.map_version,))
                stored = cur.fetchall()
            return plots, stored

        plots, stored = self._run(load)
        verdicts = {}
        for plot_id, method, verdict in stored:
            verdicts.setdefault(plot_id, {})[method] = verdict
        collection = []
        for plot_id, saved in plots:
            feature = saved if saved.get("type") == "Feature" else {"type": "Feature", "geometry": saved}
            props = dict(feature.get("properties") or {}, plot_id=plot_id)
            collection.append({"type": "Feature", "properties": props, "geometry": feature.get("geometry")})
        features, skipped = features_from_file({"type": "FeatureCollection", "features": collection})
        return {"features": features, "verdicts": verdicts,
                "skipped": [{"name": n, "reason": r} for n, r in skipped]}
```

- [ ] **Step 6: Run the tests and see them pass**

Run: `frontend\.venv\Scripts\python -m unittest discover -s frontend/tests -t frontend -v`
Expected: all pass (`test_farms.py`: 4 tests, `test_check_service.py`: 2 tests).

- [ ] **Step 7: Check a forest point and a land point against Neon (read-only)**

From `frontend\` run:

```
.venv\Scripts\python -c "import numpy as np, neon_service as n; s=n.NeonService(); s.open_map(); g=s.grid; pts=[]; [pts.append(tuple(np.argwhere(s.classes==c)[12345])) for c in (0,2,3)]; feats=[{'type':'Feature','properties':{'id':i,'name':'c%d'%c},'geometry':{'type':'Point','coordinates':list(g.pixel_centre(*rc))}} for i,(c,rc) in enumerate(zip((0,2,3),pts))]; out=s.check(feats); [print(r['name'], r['verdict'], r['results']['stencils']['point_colour'], r['results']['polygons']['point_colour'], r['agree']) for r in out['rows']]; print({m: round(t['total_s']*1000) for m,t in out['summary']['timing'].items()}, 'ms')"
```

Expected:
- `c0 PASS 0 0`, `c2 PASS 2 2`, `c3 FAIL 3 3`;
- every `agree` value is `True`;
- timings print, in ms per method.

- [ ] **Step 8: Checkpoint.** `git add frontend/farms.py frontend/neon_service.py frontend/tests/test_farms.py frontend/tests/test_check_service.py` · `git commit -m "frontend: check farms with both approaches against Neon"`

---

### Task 9: Checking from the app: Point tool, Open ▾, Check ▾, verdicts on the map

**Files:**
- Create: `frontend/checks_ui.py`, `frontend/tests/test_checks_ui.py`
- Modify: `frontend/app.py` (create the feature; hint text)

**Interfaces:**
- Consumes:
  - `MainWindow` (`action`, `toolbar`, `draw_act`, `edit_act`, `insert_tools`, `spacer_anchor`, `run_job`, `listen`, `emit`, `geojson`, `state`, `map_info`, `selected_id`, `js`, `icon`, `map`)
  - `NeonService.check`, `NeonService.saved_farms`
  - `farms.features_from_file`; map JS `startPoint`, `addFeatures`, `setVerdicts`
- Produces:
  - `checks_ui.geometry_key(feature) -> str`, `stale_keys(checked, features) -> (stale set, deleted set)`, `sort_rows(rows) -> list`
  - `CheckController(window)` with:
    - `rows {key: row}`, `stale`, `last`, `point_mode()`, `rule()`, `sorted_rows()`, `update_row(row, feature)`, `publish()`
    - window events `"results"(sorted rows, each with "stale")` and `"checked"(check result)`
    - attribute `window.checks`

- [ ] **Step 1: Write the failing test** `frontend/tests/test_checks_ui.py`:

```python
import unittest

import checks_ui


def point(id_, lon, lat):
    return {"type": "Feature", "properties": {"id": id_, "name": f"farm {id_}"},
            "geometry": {"type": "Point", "coordinates": [lon, lat]}}


class StaleTest(unittest.TestCase):
    def test_edited_and_deleted_farms(self):
        """Review focus 3: an edited farm's result is out of date; a deleted farm's result goes."""
        checked = {1: checks_ui.geometry_key(point(1, 103.1, -1.9)),
                   2: checks_ui.geometry_key(point(2, 103.2, -1.9))}
        stale, deleted = checks_ui.stale_keys(checked, [point(1, 103.1, -1.95)])
        self.assertEqual((stale, deleted), ({1}, {2}))

    def test_untouched_and_renamed_farms_stay_current(self):
        checked = {1: checks_ui.geometry_key(point(1, 103.1, -1.9))}
        renamed = point(1, 103.1, -1.9)
        renamed["properties"]["name"] = "new name"
        self.assertEqual(checks_ui.stale_keys(checked, [renamed]), (set(), set()))

    def test_rows_sort_worst_first(self):
        rows = [{"verdict": "PASS", "name": "a"}, {"verdict": "FAIL", "name": "b"},
                {"verdict": "MANUAL_REVIEW", "name": "c"}, {"verdict": "FAIL", "name": "A"}]
        self.assertEqual([r["name"] for r in checks_ui.sort_rows(rows)], ["A", "b", "c", "a"])
```

- [ ] **Step 2: Run it and see it fail**

Run: `frontend\.venv\Scripts\python -m unittest discover -s frontend/tests -t frontend -p test_checks_ui.py -v`
Expected: ERROR, `No module named 'checks_ui'`

- [ ] **Step 3: Write `frontend/checks_ui.py`**

```python
"""checks_ui.py - checking farms from the app: the Point tool, Open (a GeoJSON file, or farms saved
in Neon), Check (all farms or the selected one, with options) and the results of the last check.

A result belongs to the shape that was checked: if a farm is edited afterwards its result is
marked out of date (grey, dashed) until it is checked again, and a deleted farm loses its result.
"""

import json
import os

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QActionGroup, QFileDialog, QMenu, QMessageBox, QToolButton

from farms import features_from_file
from legend import METHODS, VERDICT_ORDER, VERDICT_STYLE
from report import worst

GEOJSON_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "backend", "data", "geojson")


def geometry_key(feature):
    """A text fingerprint of a farm's shape, to notice edits made after a check."""
    return json.dumps(feature.get("geometry"), sort_keys=True)


def stale_keys(checked, features):
    """checked = {key: geometry_key at check time}. Returns (stale keys, deleted keys)."""
    now = {f["properties"]["id"]: geometry_key(f) for f in features}
    stale = {k for k, g in checked.items() if k in now and now[k] != g}
    deleted = {k for k in checked if k not in now}
    return stale, deleted


def sort_rows(rows):
    """Worst verdict first, then by name."""
    return sorted(rows, key=lambda r: (VERDICT_ORDER.index(r["verdict"]), r["name"].lower()))


class CheckController:
    def __init__(self, window):
        self.w = window
        window.checks = self
        self.rows = {}           # key -> row of the last check (or a row saved in Neon)
        self.checked = {}        # key -> geometry_key when it was checked
        self.stale = set()
        self.last = None
        self.pending_saved = None

        # Point tool, next to the polygon tool (which is now called "Polygon")
        window.draw_act.setText("Polygon")
        window.draw_act.setToolTip("Polygon  (P)")
        self.point_act = window.action("Point", "point", ["O"], lambda: window.js("startPoint()"))
        self.point_act.setCheckable(True)
        window.toolbar.insertAction(window.edit_act, self.point_act)

        # Open ▾
        open_menu = QMenu(window)
        self.open_file_act = open_menu.addAction("GeoJSON file…", self.open_file)
        self.open_neon_act = open_menu.addAction("Farms saved in Neon", self.open_saved)
        open_btn = self.menu_button("Open", "open", open_menu)

        # Check ▾ (click = check all farms; the arrow opens the options)
        check_menu = QMenu(window)
        check_menu.setToolTipsVisible(True)
        self.check_all_act = window.action("Check all farms", "verify", ["Ctrl+Return", "Ctrl+Enter"], self.check_all)
        self.check_sel_act = window.action("Check selected farm", None, ["Ctrl+Shift+Return"], self.check_selected)
        check_menu.addAction(self.check_all_act)
        check_menu.addAction(self.check_sel_act)
        check_menu.addSeparator()
        self.point_mode = self.choice(check_menu, "Check points as", [
            ("The pixel under the point", "pixel"), ("A circle of the declared area (area_ha)", "circle")])
        self.rule = self.choice(check_menu, "Stencil rule", [
            ("Every pixel an outline touches", "touched"), ("Pixels whose centre is inside", "centre")])
        check_menu.addSeparator()
        self.tiff_act = check_menu.addAction("Compare with the original TIFF")
        self.tiff_act.setCheckable(True)
        self.tiff_act.setEnabled(False)
        self.save_act = check_menu.addAction("Save results to Neon")
        self.save_act.setCheckable(True)
        self.save_act.setToolTip("Writes the farms to plots and the answers to plot_results, like the "
                                 "backend's check command. A farm's name is its plot_id.")
        check_btn = QToolButton()
        check_btn.setDefaultAction(self.check_all_act)
        check_btn.setText("Check")
        check_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        check_btn.setMenu(check_menu)
        check_btn.setPopupMode(QToolButton.MenuButtonPopup)

        window.insert_tools(window.spacer_anchor, [None, open_btn, check_btn])
        window.listen("state", self.on_state)
        window.listen("geojson", self.on_geojson)
        window.listen("map_loaded", self.on_map_loaded)

    # ----- UI helpers -----
    def menu_button(self, text, icon, menu):
        btn = QToolButton()
        btn.setText(text)
        btn.setIcon(self.w.icon(icon))
        btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        btn.setMenu(menu)
        btn.setPopupMode(QToolButton.InstantPopup)
        return btn

    def choice(self, menu, title, options):
        """A submenu of exclusive options; returns a function giving the chosen value."""
        sub = menu.addMenu(title)
        group = QActionGroup(sub)
        group.setExclusive(True)
        for i, (label, value) in enumerate(options):
            act = sub.addAction(label)
            act.setCheckable(True)
            act.setChecked(i == 0)
            act.setData(value)
            group.addAction(act)
        return lambda: group.checkedAction().data()

    def on_state(self, state):
        idle = state["mode"] == "idle"
        ready = self.w.map_info is not None
        has_farms = bool(self.w.geojson["features"])
        self.point_act.setEnabled(idle)
        self.point_act.setChecked(state["mode"] == "placing")
        self.check_all_act.setEnabled(idle and ready and has_farms)
        self.check_sel_act.setEnabled(idle and ready and state["selected"] is not None)
        self.open_neon_act.setEnabled(ready)

    def on_map_loaded(self, info):
        available = info["tiff"]["available"]
        self.tiff_act.setEnabled(available)
        self.tiff_act.setChecked(available)
        self.tiff_act.setToolTip(info["tiff"]["note"])
        self.on_state(self.w.state)

    # ----- checking -----
    def check_all(self):
        self.run_check(self.w.geojson["features"])

    def check_selected(self):
        key = self.w.selected_id()
        self.run_check([f for f in self.w.geojson["features"] if f["properties"]["id"] == key])

    def run_check(self, features):
        if not features or self.w.map_info is None:
            return
        shapes = {f["properties"]["id"]: geometry_key(f) for f in features}
        self.w.statusBar().showMessage(f"Checking {len(features)} farm{'' if len(features) == 1 else 's'}…")
        self.w.run_job("check", "check", features, point_mode=self.point_mode(), rule=self.rule(),
                       with_tiff=self.tiff_act.isChecked(), save=self.save_act.isChecked(),
                       done=lambda result: self.check_done(result, shapes))

    def check_done(self, result, shapes):
        for row in result["rows"]:
            self.rows[row["key"]] = row
            self.checked[row["key"]] = shapes.get(row["key"], "")
            self.stale.discard(row["key"])
        self.last = result
        self.publish()
        self.report(result)
        if result["skipped"]:
            names = "\n".join(f"• {s['name']}: {s['reason']}" for s in result["skipped"][:12])
            QMessageBox.information(self.w, "Some farms were not checked", names)
        self.w.emit("checked", result)

    def update_row(self, row, feature):
        """A fresh answer for one farm (from the focus view)."""
        self.rows[row["key"]] = row
        self.checked[row["key"]] = geometry_key(feature)
        self.stale.discard(row["key"])
        self.publish()

    def report(self, result):
        rows, s = result["rows"], result["summary"]
        if not rows:
            self.w.statusBar().showMessage("Nothing to check.", 6000)
            return
        counts = [(v, sum(1 for r in rows if r["verdict"] == v)) for v in VERDICT_ORDER]
        parts = ", ".join(f"{n} {VERDICT_STYLE[v][2].lower()}" for v, n in counts if n)
        times = ", ".join(f"{METHODS[m][0].lower()} {1000 * s['timing'][m]['total_s']:.0f} ms"
                          for m in ("stencils", "polygons"))
        saved = " · saved to Neon" if result["options"]["saved"] else ""
        self.w.statusBar().showMessage(f"Checked {len(rows)}: {parts} · {times} over Neon{saved}", 20000)

    # ----- results -> map and panel -----
    def sorted_rows(self):
        return [dict(row, stale=row["key"] in self.stale) for row in sort_rows(list(self.rows.values()))]

    def publish(self):
        self.w.map.call("setVerdicts", {str(k): {"verdict": r["verdict"], "stale": k in self.stale}
                                        for k, r in self.rows.items()})
        self.w.emit("results", self.sorted_rows())

    def on_geojson(self, geojson):
        features = geojson["features"]
        if self.pending_saved is not None:
            self.apply_saved(features)
        if not self.rows:
            return
        stale, deleted = stale_keys(self.checked, features)
        changed = stale != self.stale or bool(deleted)
        for key in deleted:
            self.rows.pop(key, None)
            self.checked.pop(key, None)
        names = {f["properties"]["id"]: f["properties"]["name"] for f in features}
        for key, row in self.rows.items():
            if key in names and row["name"] != names[key]:
                row["name"] = names[key]
                changed = True
        self.stale = stale
        if changed:
            self.publish()

    # ----- opening farms -----
    def open_file(self):
        folder = GEOJSON_DIR if os.path.isdir(GEOJSON_DIR) else os.path.expanduser("~")
        path, _ = QFileDialog.getOpenFileName(self.w, "Open farms", folder, "GeoJSON (*.geojson *.json)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self.w, "Couldn't open that file",
                                f"{os.path.basename(path)} isn't a readable GeoJSON file.\n\n{exc}")
            return
        features, skipped = features_from_file(data)
        self.add_features(features, skipped, os.path.basename(path))

    def open_saved(self):
        self.w.run_job("saved_farms", "saved_farms", done=self.saved_loaded)

    def saved_loaded(self, result):
        if not result["features"]:
            QMessageBox.information(self.w, "No farms saved in Neon",
                                    "Neon's plots table is empty. Tick Check ▸ Save results to Neon "
                                    "before checking, or run the backend's check command, to store some.")
            return
        self.pending_saved = result["verdicts"]
        self.add_features(result["features"], [(s["name"], s["reason"]) for s in result["skipped"]], "Neon")

    def add_features(self, features, skipped, source):
        if features:
            self.w.map.call("addFeatures", {"type": "FeatureCollection", "features": features})
        text = f"Added {len(features)} farm{'' if len(features) == 1 else 's'} from {source}."
        if skipped:
            text += f" Skipped {len(skipped)}: " + "; ".join(f"{n} ({r})" for n, r in skipped[:5])
            QMessageBox.information(self.w, "Some farms were skipped", text)
        self.w.statusBar().showMessage(text, 10000)

    def apply_saved(self, features):
        """Show the verdicts stored in Neon for farms just loaded from Neon (until they are checked)."""
        saved, self.pending_saved = self.pending_saved, None
        for f in features:
            p = f["properties"]
            stored = saved.get(p["name"])
            if not stored or p["id"] in self.rows:
                continue
            verdict = worst([stored.get("stencils"), stored.get("polygons")])
            if verdict is None:
                continue
            self.rows[p["id"]] = {
                "key": p["id"], "name": p["name"], "kind": p["kind"], "area_ha": p["area_km2"] * 100,
                "verdict": verdict, "saved": True,
                "results": {m: {"verdict": stored[m]} for m in ("stencils", "polygons") if stored.get(m)},
                "agree": {"stencils_tiff": None, "polygons_tiff": None,
                          "stencils_polygons": stored.get("stencils") == stored.get("polygons")},
            }
            self.checked[p["id"]] = geometry_key(f)
        self.publish()
```

- [ ] **Step 4: Create the feature in `frontend/app.py`**
  - Add `from checks_ui import CheckController` to the imports.
  - Below `self.eudr = EudrLayer(self)`, add:

```python
        self.checks = CheckController(self)
```

  - In `refresh()`, replace the hint for an empty map,

```python
            hint = "Press Draw (P) and click on the map to outline a farm."
```

    with:

```python
            hint = "Press Polygon (P) or Point (O) and click on the map, or Open a GeoJSON file."
```

  - Replace `hint = "Click a farm to select it, or press Draw (P) to add another."` with:

```python
            hint = "Click a farm to select it, or press Check (Ctrl+Enter) to check them all."
```

- [ ] **Step 5: Run the tests and lint**

Run: `frontend\.venv\Scripts\python -m unittest discover -s frontend/tests -t frontend -v` (expected: all pass)
Run: `frontend\.venv\Scripts\python -m pyflakes frontend\app.py frontend\checks_ui.py frontend\farms.py frontend\report.py frontend\neon_service.py` (expected: no output)

- [ ] **Step 6: Try it in the app**

Run: `frontend\.venv\Scripts\python frontend\app.py`
Expected:
- Point (O) places a point.
- Polygon (P) draws an outline. Draw one over a red (deforestation) patch and one on grey land.
- Ctrl+Enter checks them:
  - the outlines turn to their verdict colours with a white halo (red ✖ Not compliant, green ✔ Compliant), and the tooltips name the verdict;
  - the status bar says, for example, `Checked 3: 1 not compliant, 2 compliant · stencils 40 ms, outlines 35 ms over Neon`.
- Editing a checked farm turns it grey and dashed (` · edited`).
- Open ▸ GeoJSON file… loads a file and lists any skipped features.
- Open ▸ Farms saved in Neon says the table is empty.
- With Check ▸ Save results to Neon ticked, checking and then opening the saved farms brings them back.

Afterwards, run `DELETE FROM plots WHERE plot_id LIKE 'Polygon %' OR plot_id LIKE 'Point %'` in the Neon SQL editor, only if you don't want to keep those test rows.

- [ ] **Step 7: Checkpoint.** `git add frontend/checks_ui.py frontend/app.py frontend/tests/test_checks_ui.py` · `git commit -m "frontend: check points and outlines from the app"`

---


---

## Scope narrowed on 2026-10-15: Tasks 10–14 replace the old Tasks 10–19

The user chose "Core first". The old Tasks 10–19 are dropped for now:
- focus view and verdict card;
- Inspect tool;
- Storage and Fidelity statistics;
- SVG diagrams.

The approved plan for the replacement tasks is in `C:\Users\travi\.claude\plans\polished-painting-wand.md`. Built:

| Task | Files | What it does |
|---|---|---|
| 10 | `metering.py`, `tests/test_metering.py` | `MeteredConnection` logs every query an approach sends (round trips, rows, bytes, time); `explain()` re-runs the data queries under `EXPLAIN (ANALYZE, BUFFERS)` and always rolls back |
| 11 | `neon_service.py` (`check`, `_measure`), `tests/test_check_service.py` | points and outlines are checked as separate batches, one metered run per approach × question; `check()["runs"]` |
| 12 | `charts.py`, `results_panel.py`, `tests/test_results_panel.py` | results table beside the map, and per-question comparison bars (small multiples, each with a table twin) |
| 13 | `farmgen.py`, `NeonService.benchmark`, `compare_tab.py`, `tests/test_benchmark.py`, `tests/test_compare_tab.py` | Compare tab: batches of 1–1,000 farms, median of 3, log-log line charts, and "where the time goes" |
| 14 | `smoke_neon.py`, `README.md` | read-only check against Neon; how to run the app and read the numbers |
