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
