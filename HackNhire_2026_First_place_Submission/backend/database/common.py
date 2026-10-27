"""
common.py - code shared by BOTH storage approaches (and the TIFF baseline).

Anything that must be done identically everywhere lives here:
  * the project folders, and connecting to the Neon (PostgreSQL) database
  * the colour codes and the EUDR verdict rule
  * the "address system": turning coordinates into pixel positions
  * reading the TIFF page by page, and choosing the one colour that is NOT stored
  * turning a farm outline into the pixels it covers
  * loading farms from a GeoJSON file, and saving verdicts
  * checking farms directly against the original TIFF (the baseline)
  * several maps: each one in its own schema, listed in public.maps

Nothing is lost: every colour is stored except the map's MOST COMMON colour, which is
"whatever is not in the database" (usually land). Rebuilding the map gives back every pixel.

Keeping these rules in one place guarantees that any difference between the approaches
comes from how the map is STORED, never from slightly different rules.
"""

from __future__ import annotations

import csv
import dataclasses
import functools
import hashlib
import json
import math
import os
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass, field

import numpy as np
import psycopg
import rasterio
import shapely
from affine import Affine
from psycopg.types.json import Jsonb
from pyproj import Geod, Transformer
from rasterio.features import geometry_mask, shapes
from rasterio.windows import Window
from shapely import affinity
from shapely.geometry import Point, Polygon, box, shape
from shapely.ops import transform as reproject

# --------------------------------------------------------------------------------------
# Colours (the values inside the TIFF)
# --------------------------------------------------------------------------------------
LAND, WATER, FOREST, DEFORESTATION, REVIEW, NO_DATA = 0, 1, 2, 3, 4, 5
CLASS_NAMES = {
    LAND: "land",
    WATER: "water",
    FOREST: "forest",
    DEFORESTATION: "deforestation",
    REVIEW: "needs review",
    NO_DATA: "no data",  # anything in the TIFF that is not 0-4 (e.g. the nodata value 255)
}
COLOURS = tuple(CLASS_NAMES)

# Forest on the EUDR cut-off date (31 Dec 2020) in the 5 colours. The compaction step (layers.five_classes)
# turns every pixel JRC GFC2020 calls forest into forest (still standing, damaged or rubber-map forest),
# deforestation or needs review, and every pixel it doesn't into land, so these three colours are the
# 2020 forest (minus pixels that are now water or have no data). Its rule only ever finds deforestation
# on 2020 forest, so a farm touching none of them can't be deforestation, whatever else is under it.
FOREST_2020 = (FOREST, DEFORESTATION, REVIEW)

PAGE = 256  # the TIFF is read, and stencils are packed, in pages of 256 x 256 pixels

# Overlap rules (exact-area mode): a colour counts as "touched" if it covers more than
# 1 cm2 AND more than a millionth of the farm - anything smaller is floating-point dust.
MIN_OVERLAP_M2 = 1e-4
MIN_OVERLAP_SHARE = 1e-6
WATER_MAJORITY = 0.5  # an outline that is more than half water -> location probably wrong

FAIL = "FAIL"                      # deforestation found
MANUAL_REVIEW = "MANUAL_REVIEW"    # the map is unsure, or the farm is in a protected area
CANNOT_CHECK = "CANNOT_CHECK"      # outside the map, or on no-data pixels
CHECK_LOCATION = "CHECK_LOCATION"  # on water: coordinates are probably wrong
PASS = "PASS"                      # only land and/or forest
VERDICTS = (FAIL, MANUAL_REVIEW, CANNOT_CHECK, CHECK_LOCATION, PASS)

# A plot's result has three outcomes. The verdicts above (and eudr_layers.plot_verdict's, for the
# evidence) say why:
#   red (deforestation)       -> non-compliant
#   purple (needs review)     -> needs review
#   green (forest) and land   -> compliant: standing forest isn't deforestation under the EUDR
# A farm that can't be checked (no data, outside the map, on water) also needs review.
NON_COMPLIANT, NEEDS_REVIEW, COMPLIANT = "NON_COMPLIANT", "NEEDS_REVIEW", "COMPLIANT"
OUTCOMES = (NON_COMPLIANT, NEEDS_REVIEW, COMPLIANT)   # worst first
OUTCOME_OF = {
    FAIL: NON_COMPLIANT,              # deforestation after the cut-off
    MANUAL_REVIEW: NEEDS_REVIEW,      # the map is unsure (probable, or one source only)
    CANNOT_CHECK: NEEDS_REVIEW,       # outside the map or no data: nobody can say it's compliant
    CHECK_LOCATION: NEEDS_REVIEW,     # on water: the coordinates are probably wrong
    PASS: COMPLIANT,
    # eudr_layers.plot_verdict (the evidence check)
    "non-compliant": NON_COMPLIANT,
    "probable deforestation": NEEDS_REVIEW,   # shown purple on the map: needs evidence
    "needs review": NEEDS_REVIEW,
    "not assessed": NEEDS_REVIEW,
    "deforestation-free": COMPLIANT,
}


def outcome_of(verdict: str) -> str:
    """Compliant, non-compliant or needs review, for a verdict of any check."""
    return OUTCOME_OF[verdict]

GEOD = Geod(ellps="WGS84")


def decide(present: set[int], water_share: float, outside: bool = False) -> str:
    """The EUDR verdict. The worst colour the farm touches wins."""
    if DEFORESTATION in present:
        return FAIL
    if REVIEW in present:
        return MANUAL_REVIEW
    if outside or NO_DATA in present:
        return CANNOT_CHECK
    if water_share > WATER_MAJORITY:
        return CHECK_LOCATION
    return PASS


# --------------------------------------------------------------------------------------
# Project folders
#   backend/                 <- ROOT_DIR: .env and pipeline.py live here
#   ├── compaction/          <- step 1: satellite layers -> compact layers (eudr_layers.py)
#   ├── database/            <- step 2: this file, the storage approaches (+ results/ for their CSVs)
#   ├── compare/             <- the evaluation (+ results/ for its CSV)
#   └── layers/<map>/        <- what step 1 builds, one folder per map (evidence.tif, outcome.tif,
#                               test plots, ...). Made by pipeline.py; never in Git.
# --------------------------------------------------------------------------------------
DATABASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(DATABASE_DIR)
COMPACTION_DIR = os.path.join(ROOT_DIR, "compaction")
LAYERS_DIR = os.path.join(ROOT_DIR, "layers")
TIFF_DIR = LAYERS_DIR  # where the app looks for the TIFF a map was uploaded from (grid_info.source_file)


def nice_path(path: str) -> str:
    """A short, readable version of a path for messages."""
    try:
        return os.path.relpath(path)
    except ValueError:  # on a different drive (Windows)
        return path


def find_input(path: str, what: str) -> str:
    """Accept a full path, a path from the backend folder, or a path inside layers/
    (e.g. jambi/test_plots.geojson)."""
    for candidate in (path, os.path.join(ROOT_DIR, path), os.path.join(LAYERS_DIR, path)):
        if os.path.isfile(candidate):
            return candidate
    sys.exit(f"Can't find the {what} file '{path}'.\n"
             f"Give its full path, or its path inside {nice_path(LAYERS_DIR)}{os.sep} "
             f"(e.g. jambi{os.sep}test_plots.geojson).")


def find_tiff(path: str) -> str:
    return find_input(path, "TIFF")


def find_geojson(path: str) -> str:
    return find_input(path, "GeoJSON")


def stored_path(path: str) -> str:
    """How a file is named in the database: its path inside layers/ (e.g. 'jambi/outcome_5class.tif',
    so the app can find it again), else just its file name."""
    try:
        inside = os.path.relpath(os.path.abspath(path), LAYERS_DIR)
    except ValueError:  # on a different drive (Windows)
        return os.path.basename(path)
    if inside.startswith(os.pardir) or os.path.isabs(inside):
        return os.path.basename(path)
    return inside.replace(os.sep, "/")


# --------------------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------------------
def _read_env_file(path: str) -> None:
    with open(path, encoding="utf-8-sig") as fh:
        for line in fh:
            line = line.strip()
            if line.startswith("export "):
                line = line[len("export "):]
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _load_dotenv() -> None:
    """Read DATABASE_URL from a .env file, unless it is already set in the terminal.
    Looks in the folder you run from, then in the project folder (next to README.md)."""
    if os.environ.get("DATABASE_URL"):
        return
    for folder in dict.fromkeys((os.getcwd(), ROOT_DIR, DATABASE_DIR)):
        for name in (".env", ".env.txt"):  # Windows Notepad sometimes saves .env as .env.txt
            path = os.path.join(folder, name)
            if os.path.isfile(path):
                _read_env_file(path)
                if os.environ.get("DATABASE_URL"):
                    if name != ".env":
                        print(f"Note: using {nice_path(path)} - renaming it to .env is cleaner.")
                    return


def connect() -> psycopg.Connection:
    """Open a connection to Neon using DATABASE_URL (from the .env file or the terminal)."""
    _load_dotenv()
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        sys.exit(
            "DATABASE_URL is not set.\n"
            f"Create a file called .env in the project folder ({ROOT_DIR}), next to README.md,\n"
            "with one line (your own string from Neon's Connect button):\n"
            "  DATABASE_URL=postgresql://user:password@ep-xxxx.ap-southeast-1.aws.neon.tech/neondb?sslmode=require"
        )
    if "ep-xxxx" in url:
        sys.exit("Your .env still has the example connection string - "
                 "paste your own one from Neon's Connect button.")
    # prepare_threshold=None keeps this working on Neon's pooled (PgBouncer) connection too.
    try:
        return psycopg.connect(url, prepare_threshold=None, connect_timeout=30)
    except psycopg.OperationalError as exc:
        sys.exit(f"Could not connect to the database:\n  {exc}\n"
                 "Check that DATABASE_URL is copied exactly from Neon (including ?sslmode=require) "
                 "and that you are online.")


# --------------------------------------------------------------------------------------
# Several maps. Each map's tables live in its own schema (a folder of tables), e.g.
# map_jambi_v1, and public.maps lists the maps. Every query names its tables with the schema
# (grid.table("stencil_pages") -> "map_jambi_v1.stencil_pages"), so a query can never read the
# wrong map, and no SET search_path is needed (that would cost a round trip per transaction).
# --------------------------------------------------------------------------------------
MAP_NAME_MAX = 40                          # keeps "map_" + the name far below PostgreSQL's 63
SCHEMA_RE = re.compile(r"^map_[a-z0-9_]+$")

SCHEMA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")
SECTION_RE = re.compile(r"^--\s*@(\w+)\s*$")


@functools.lru_cache(maxsize=1)
def _schema_sections() -> dict[str, str]:
    """schema.sql cut into its sections: {name: SQL}. A section starts with a line '-- @name'."""
    sections, name = {}, None
    with open(SCHEMA_FILE, encoding="utf-8") as fh:
        for line in fh:
            found = SECTION_RE.match(line)
            if found:
                name = found.group(1)
                sections[name] = ""
            elif name is not None:
                sections[name] += line
    return sections


def schema_sql(section: str, grid: "Grid | None" = None) -> str:
    """One section of schema.sql, ready to run (several statements, no parameters). With a grid,
    {schema} becomes its schema and {srid} its EPSG code."""
    sql = _schema_sections()[section]
    if grid is not None:
        sql = sql.replace("{schema}", grid.schema or "public").replace("{srid}", str(int(grid.srid)))
    return sql


CATALOGUE_SQL = schema_sql("shared")   # public.maps

NO_MAP_MESSAGE = ("No map in the database yet - build and upload one first, e.g.\n"
                  "  python pipeline.py --name jambi --data-dir YOUR_LAYERS --bbox 103.0 -2.3 103.5 -1.8\n"
                  "or upload a results folder you already have:\n"
                  "  python database/maps.py upload layers/jambi --name jambi")
OLD_LAYOUT_MESSAGE = ("Your map is stored the old way (one map in the public schema). Move it into "
                      "the new layout first - it takes a second and nothing is re-uploaded:\n"
                      "  python database/maps.py migrate")


def map_schema(name: str) -> str:
    """A map's name -> the schema its tables live in: 'Jambi v1' -> 'map_jambi_v1'.
    Stops with a message saying what to change if the name can't be used."""
    name = (name or "").strip()
    if not name:
        sys.exit("A map needs a name, e.g. jambi-v1.")
    if len(name) > MAP_NAME_MAX:
        sys.exit(f"A map name can be at most {MAP_NAME_MAX} characters; this one has {len(name)}.")
    if not name.isprintable():
        sys.exit("A map name can't contain tabs, line breaks or other control characters.")
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    if not slug:
        sys.exit(f"'{name}' can't be used as a map name: include at least one letter (a-z) or digit.")
    return "map_" + slug


def default_map_name(tif_path: str) -> str:
    """The name a map gets when none is given: the TIFF's file name without .tif."""
    return os.path.splitext(os.path.basename(tif_path))[0]


def map_tables_sql(grid: Grid) -> str:
    """The tables every map has, whichever approach is built: its address system, and the farms
    and verdicts saved for it - all in the map's own schema (schema.sql: @map, @map_upgrade)."""
    return schema_sql("map", grid) + schema_sql("map_upgrade", grid)


def copy_rows(conn: psycopg.Connection, table: str, columns: list[str], rows: list) -> None:
    """Bulk-load rows with COPY - far faster than INSERT over a network."""
    if not rows:
        return
    with conn.cursor() as cur:
        with cur.copy(f"COPY {table} ({', '.join(columns)}) FROM STDIN") as cp:
            for row in rows:
                cp.write_row(row)


def tidy_up(conn: psycopg.Connection, table: str) -> None:
    """VACUUM ANALYZE after an upload: packs the table tightly (measured: the stencil table
    shrank by about 15%) and refreshes the statistics the database uses to plan fast queries."""
    conn.commit()
    conn.autocommit = True  # VACUUM cannot run inside a transaction
    try:
        conn.execute(f"VACUUM ANALYZE {table}")
    finally:
        conn.autocommit = False


def no_jit(cur) -> None:
    """Switch off PostgreSQL's JIT compiler until the end of this transaction.
    PostGIS marks its shape functions as expensive, so a batch overlap query looks huge to the
    planner, and PostgreSQL then spends ~150 ms compiling it - longer than the query itself
    takes (measured on 915 farms: 215 ms with JIT, 65 ms without). The real work happens
    inside PostGIS, which JIT cannot speed up anyway. SET LOCAL ends with the transaction,
    so it is also safe on Neon's pooled connection."""
    cur.execute("SET LOCAL jit = off")


def table_exists(conn: psycopg.Connection, table: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s) IS NOT NULL", (table,))
        return cur.fetchone()[0]


def table_sizes(conn: psycopg.Connection, tables: list[str]) -> dict[str, tuple[int, int]]:
    """{table: (bytes including indexes, row count)} for the tables that exist."""
    sizes = {}
    with conn.cursor() as cur:
        for t in tables:
            if table_exists(conn, t):
                cur.execute(f"SELECT pg_total_relation_size('{t}'), (SELECT count(*) FROM {t})")
                sizes[t] = cur.fetchone()
    return sizes


def print_storage(conn: psycopg.Connection, tables: list[str]) -> None:
    for table, (size, rows) in table_sizes(conn, tables).items():
        print(f"  {table.rsplit('.', 1)[-1]:14s} {rows:>10,} rows  {size / 1e6:8.3f} MB")


# --------------------------------------------------------------------------------------
# The address system
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Grid:
    map_version: str
    x0: float
    y0: float
    pixel_w: float
    pixel_h: float
    width: int
    height: int
    srid: int
    geographic: bool
    default_class: int = field(default=LAND, compare=False)  # the colour that is not stored
    fingerprint: str = field(default="", compare=False)
    schema: str = field(default="", compare=False)  # where the map's tables live, e.g. map_jambi_v1
    # the map's tables when it was loaded (load_grid), so a check needn't ask again (see has_tables)
    tables: frozenset = field(default=frozenset(), compare=False)

    def table(self, name: str) -> str:
        """One of this map's tables, named with its schema: 'map_jambi_v1.stencil_pages'."""
        return f"{self.schema}.{name}" if self.schema else name

    @classmethod
    def from_dataset(cls, src, map_version: str) -> "Grid":
        t = src.transform
        if t.b != 0 or t.d != 0 or t.e >= 0:
            sys.exit("The TIFF must be north-up with no rotation.")
        epsg = src.crs.to_epsg() if src.crs else None
        if epsg is None:
            sys.exit("Could not read an EPSG code from the TIFF's coordinate system.")
        if src.crs.is_geographic and epsg != 4326:
            sys.exit("Maps in degrees must use EPSG:4326 (WGS 84).")
        if src.nodata is not None and 0 <= src.nodata <= 4:
            print(f"Warning: the TIFF says {src.nodata:g} means 'no data', but that value is also "
                  f"a colour ({CLASS_NAMES[int(src.nodata)]}). Treating it as the colour.")
        return cls(map_version, t.c, t.f, t.a, -t.e, src.width, src.height, epsg,
                   bool(src.crs.is_geographic))

    def window_transform(self, row_off: int, col_off: int) -> Affine:
        return Affine(self.pixel_w, 0.0, self.x0 + col_off * self.pixel_w,
                      0.0, -self.pixel_h, self.y0 - row_off * self.pixel_h)

    def point_to_pixel(self, x: float, y: float) -> tuple[int, int]:
        """(row, col) of the pixel containing a point. Used by EVERY method, so they agree."""
        col = math.floor((x - self.x0) / self.pixel_w)
        row = math.floor((self.y0 - y) / self.pixel_h)
        return row, col

    def pixel_centre(self, row: int, col: int) -> tuple[float, float]:
        return self.x0 + (col + 0.5) * self.pixel_w, self.y0 - (row + 0.5) * self.pixel_h

    def inside(self, row: int, col: int) -> bool:
        return 0 <= row < self.height and 0 <= col < self.width

    def bounds_box(self) -> Polygon:
        return box(self.x0, self.y0 - self.height * self.pixel_h,
                   self.x0 + self.width * self.pixel_w, self.y0)

    def window_bounds(self, row_off: int, col_off: int, height: int, width: int):
        """(minx, miny, maxx, maxy) of a block of pixels."""
        return (self.x0 + col_off * self.pixel_w, self.y0 - (row_off + height) * self.pixel_h,
                self.x0 + (col_off + width) * self.pixel_w, self.y0 - row_off * self.pixel_h)

    def pixel_area_m2(self, row: int) -> float:
        return _pixel_area_m2(self, row)

    def pixel_range(self, geom) -> tuple[int, int, int, int]:
        """(r0, c0, r1, c1): pixels around a geometry, with a 1-pixel safety margin, clipped to the map."""
        minx, miny, maxx, maxy = geom.bounds
        r0, c0 = self.point_to_pixel(minx, maxy)
        r1, c1 = self.point_to_pixel(maxx, miny)
        return (max(r0 - 1, 0), max(c0 - 1, 0),
                min(r1 + 1, self.height - 1), min(c1 + 1, self.width - 1))


@functools.lru_cache(maxsize=100_000)
def _pixel_area_m2(grid: Grid, row: int) -> float:
    if not grid.geographic:
        return grid.pixel_w * grid.pixel_h  # projected maps are in metres
    top = grid.y0 - row * grid.pixel_h
    bottom = top - grid.pixel_h
    lons = [grid.x0, grid.x0 + grid.pixel_w, grid.x0 + grid.pixel_w, grid.x0]
    lats = [top, top, bottom, bottom]
    return abs(GEOD.polygon_area_perimeter(lons, lats)[0])


def area_m2(geom, grid: Grid) -> float:
    """Area of the polygon parts of a geometry, in square metres (geodesic for maps in degrees)."""
    if geom.is_empty:
        return 0.0
    if geom.geom_type == "GeometryCollection":
        return sum(area_m2(g, grid) for g in geom.geoms)
    if geom.geom_type not in ("Polygon", "MultiPolygon"):
        return 0.0
    if not grid.geographic:
        return geom.area
    if geom.geom_type == "Polygon" and not geom.interiors:  # the common case, done directly (faster)
        lons, lats = shapely.get_coordinates(geom.exterior).T
        return abs(GEOD.polygon_area_perimeter(lons, lats)[0])
    return abs(GEOD.geometry_area_perimeter(geom)[0])


def snap_to_grid(geom, grid: Grid):
    """Put traced corners exactly on pixel edges, so every method uses identical coordinates."""
    def snap(coords):
        cols = np.rint((coords[:, 0] - grid.x0) / grid.pixel_w)
        rows = np.rint((grid.y0 - coords[:, 1]) / grid.pixel_h)
        return np.column_stack([grid.x0 + cols * grid.pixel_w, grid.y0 - rows * grid.pixel_h])
    return shapely.transform(geom, snap)


def trace(data: np.ndarray, classes, grid: Grid, row_off: int, col_off: int):
    """Trace patches of the given colours into outlines: yields (colour, shapely Polygon).
    Outlines follow pixel edges exactly, so nothing is lost."""
    mask = np.isin(data, classes)
    if not mask.any():
        return
    for geom, value in shapes(data, mask=mask, connectivity=4,
                              transform=grid.window_transform(row_off, col_off)):
        yield int(value), snap_to_grid(shape(geom), grid)


def save_grid(conn: psycopg.Connection, grid: Grid, source_file: str) -> None:
    """Store the map's address system in its own schema, and list the map in public.maps."""
    with conn.cursor() as cur:
        cur.execute(f"DELETE FROM {grid.table('grid_info')}")
        cur.execute(
            f"INSERT INTO {grid.table('grid_info')} (map_version, x0, y0, pixel_w, pixel_h, width, "
            "height, srid, geographic, default_class, fingerprint, source_file) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (grid.map_version, grid.x0, grid.y0, grid.pixel_w, grid.pixel_h, grid.width,
             grid.height, grid.srid, grid.geographic, grid.default_class, grid.fingerprint,
             stored_path(source_file)))
        cur.execute(
            "INSERT INTO public.maps (name, schema_name, source_file, fingerprint, width, height) "
            "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (name) DO UPDATE SET "
            "schema_name = EXCLUDED.schema_name, source_file = EXCLUDED.source_file, "
            "fingerprint = EXCLUDED.fingerprint, width = EXCLUDED.width, height = EXCLUDED.height",
            (grid.map_version, grid.schema, stored_path(source_file), grid.fingerprint,
             grid.width, grid.height))


def find_map(conn: psycopg.Connection, name: str | None = None) -> tuple[str, str]:
    """(name, schema) of a stored map. Without a name: the only map stored. A name matches a map's
    exact name, or else its schema ('Jambi V1' finds 'jambi-v1'). Stops with a message saying what
    to do if there is no such map, or several maps and no name."""
    if not table_exists(conn, "public.maps"):
        sys.exit(OLD_LAYOUT_MESSAGE if table_exists(conn, "public.grid_info") else NO_MAP_MESSAGE)
    with conn.cursor() as cur:
        cur.execute("SELECT name, schema_name FROM public.maps ORDER BY lower(name)")
        stored = [tuple(row) for row in cur.fetchall()]
    if not stored:
        sys.exit(NO_MAP_MESSAGE)
    names = ", ".join(n for n, _ in stored)
    if name is None:
        if len(stored) > 1:
            sys.exit(f"Several maps are stored ({names}). Choose one with --map NAME.")
        found = stored[0]
    else:
        wanted = name.strip()
        found = next((row for row in stored if row[0] == wanted), None)
        if found is None:
            try:
                schema = map_schema(wanted)
            except SystemExit:
                schema = None
            found = next((row for row in stored if row[1] == schema), None)
        if found is None:
            sys.exit(f"There's no map called '{wanted}'. Maps in the database: {names}.")
    if not SCHEMA_RE.match(found[1]):
        sys.exit(f"The list of maps says '{found[0]}' is in schema '{found[1]}', which isn't a map's "
                 "schema (map_...). Fix or remove that row in public.maps.")
    return found


def load_grid(conn: psycopg.Connection, name: str | None = None) -> Grid:
    """The address system of a stored map (see find_map for how the map is chosen)."""
    map_name, schema = find_map(conn, name)
    with conn.cursor() as cur:
        # the map's tables come in the same query, so the checks don't each ask whether theirs exist
        cur.execute("SELECT map_version, x0, y0, pixel_w, pixel_h, width, height, srid, geographic, "
                    "default_class, fingerprint, "
                    "ARRAY(SELECT tablename::text FROM pg_catalog.pg_tables WHERE schemaname = %s) "
                    f"FROM {schema}.grid_info", (schema,))
        row = cur.fetchone()
    if row is None or row[9] is None or row[10] is None:
        sys.exit(f"The map '{map_name}' is incomplete in the database. Delete it and upload it again:\n"
                 f"  python database/maps.py delete \"{map_name}\" --yes\n"
                 f"  python database/maps.py upload layers/YOUR_FOLDER --name \"{map_name}\"")
    return Grid(*row[:11], schema=schema, tables=frozenset(row[11]))


def has_tables(conn: psycopg.Connection, grid: Grid, *names: str) -> bool:
    """Whether the map has these tables. Answered from the list load_grid read (no round trip);
    only a table missing from that list is looked up, in case it was built since."""
    return all(name in grid.tables or table_exists(conn, grid.table(name)) for name in names)


# --------------------------------------------------------------------------------------
# Reading the TIFF page by page
# --------------------------------------------------------------------------------------
@dataclass
class Page:
    row_off: int
    col_off: int
    data: np.ndarray  # colours 0-5 (anything unknown becomes NO_DATA)

    @property
    def page_col(self) -> int:
        return self.col_off // PAGE

    @property
    def page_row(self) -> int:
        return self.row_off // PAGE


def normalise(arr: np.ndarray) -> np.ndarray:
    """Make sure every pixel is 0-5: anything that is not a known colour is NO_DATA."""
    bad = (arr < 0) | (arr > 4)
    if np.issubdtype(arr.dtype, np.floating):
        bad |= ~np.isfinite(arr)
    return np.where(bad, NO_DATA, arr).astype(np.uint8)


def iter_pages(src):
    """Yield the map one page (256 x 256 pixels) at a time, so any size of TIFF fits in memory."""
    for row_off in range(0, src.height, PAGE):
        for col_off in range(0, src.width, PAGE):
            win = Window(col_off, row_off, min(PAGE, src.width - col_off),
                         min(PAGE, src.height - row_off))
            yield Page(row_off, col_off, normalise(src.read(1, window=win)))


def iter_strips(src):
    """Yield (row_off, pixels) for full-width strips of 256 rows - used to rebuild and compare."""
    for row_off in range(0, src.height, PAGE):
        win = Window(0, row_off, src.width, min(PAGE, src.height - row_off))
        yield row_off, normalise(src.read(1, window=win))


def page_count(src) -> int:
    return math.ceil(src.height / PAGE) * math.ceil(src.width / PAGE)


class Progress:
    """Prints progress every 10% - but only for jobs slow enough to need it. With report, also
    calls report(label, done, total) on every step (the app's progress bar); report may raise
    to stop the job."""

    def __init__(self, total: int, label: str, report=None):
        self.total, self.label, self.done, self.start = total, label, 0, time.perf_counter()
        self.next_report = 0.1
        self.report = report

    def step(self) -> None:
        self.done += 1
        if self.report is not None:
            self.report(self.label, self.done, self.total)
        elapsed = time.perf_counter() - self.start
        if self.done / self.total >= self.next_report:
            if elapsed > 2 or (self.done == self.total and elapsed > 0.5):
                print(f"  {self.label}: {100 * self.done / self.total:5.1f}% ({elapsed:.1f}s)", flush=True)
            while self.next_report <= self.done / self.total:
                self.next_report += 0.1


def scan_map(src, grid: Grid, report=None) -> tuple[np.ndarray, str]:
    """One pass over the TIFF: how many pixels of each colour, and a fingerprint that
    identifies this exact map (so programs can tell if the database holds a different one)."""
    counts = np.zeros(len(COLOURS), np.int64)
    fingerprint = Fingerprint(grid)
    progress = Progress(math.ceil(src.height / PAGE), "reading the map", report)
    for _, strip in iter_strips(src):
        counts += np.bincount(strip.ravel(), minlength=len(COLOURS))[:len(COLOURS)]
        fingerprint.add(strip)
        progress.step()
    return counts, fingerprint.hexdigest()


class Fingerprint:
    """A short code that identifies one exact map: same pixels + same place = same code.
    Built from the map's strips in order, so build, verify and compare all agree."""

    def __init__(self, grid: Grid):
        self._digest = hashlib.blake2b(digest_size=16)
        self._digest.update(repr((grid.x0, grid.y0, grid.pixel_w, grid.pixel_h, grid.width,
                                  grid.height, grid.srid)).encode())

    def add(self, strip: np.ndarray) -> None:
        self._digest.update(np.ascontiguousarray(strip).tobytes())

    def hexdigest(self) -> str:
        return self._digest.hexdigest()


def same_grid_or_exit(src, grid: Grid) -> Grid:
    """Stop with a clear message if a TIFF is not the same size and place as the stored map."""
    tif_grid = Grid.from_dataset(src, grid.map_version)
    if tif_grid != grid:
        sys.exit(f"This TIFF is not the map '{grid.map_version}' in the database (different size or "
                 "location). Check --map, or upload it as a map of its own:\n"
                 "  python database/maps.py upload layers/YOUR_FOLDER --name NAME")
    return tif_grid


def fidelity_text(stats: dict) -> str:
    """'100% (exact match)' only when every pixel matches - otherwise enough decimals to show it."""
    if stats["mismatched"] == 0:
        return "100% (exact match)"
    return f"{stats['fidelity_pct']:.6f}%"


def verify_report(name: str, stats: dict) -> None:
    print(f"{name}: rebuilt {stats['pixels']:,} pixels from the database - {stats['mismatched']:,} "
          f"differ from the TIFF -> pixel fidelity {fidelity_text(stats)}, all colours")
    if not stats["same_map"]:
        print("  Warning: this TIFF is not the map that was uploaded, so differences are expected. "
              "Run the build command with this TIFF first.")


def start_build(conn: psycopg.Connection, src, tif_path: str, map_name: str,
                other_tables: dict[str, str], report=None, allow_copy: bool = False) -> tuple[Grid, np.ndarray]:
    """Scan the map, choose the colour to leave out, and store the address system in the map's own
    schema (created if needed) and in the list of maps. Refuses a name too close to another map's,
    and a TIFF that is already stored under another name (unless allow_copy: maps.upload is making
    a copy of that map, reusing what Neon has). If this map held a DIFFERENT TIFF before, the other
    approach's tables are removed, so a map never mixes two TIFFs."""
    name = map_name.strip()
    schema = map_schema(name)
    grid = Grid.from_dataset(src, name)
    print(f"Map '{name}': {grid.width} x {grid.height} pixels, EPSG:{grid.srid}, "
          f"pixel {grid.pixel_w:g} x {grid.pixel_h:g}, stored in schema {schema}")
    counts, fingerprint = scan_map(src, grid, report)
    default = int(np.argmax(counts))
    grid = dataclasses.replace(grid, default_class=default, fingerprint=fingerprint, schema=schema)

    with conn.cursor() as cur:
        cur.execute(CATALOGUE_SQL)
        cur.execute("SELECT name FROM public.maps WHERE schema_name = %s AND name <> %s", (schema, name))
        clash = cur.fetchone()
        if clash:
            sys.exit(f"'{name}' is too close to the map '{clash[0]}': both would be stored as {schema}. "
                     "Choose another name.")
        cur.execute("SELECT name FROM public.maps WHERE fingerprint = %s AND name <> %s",
                    (fingerprint, name))
        same = cur.fetchone()
        if same and not allow_copy:
            sys.exit(f"This TIFF is already stored as '{same[0]}'. Use that map, or upload with "
                     "python database/maps.py upload, which reuses what Neon already has.")
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        cur.execute(map_tables_sql(grid))
        cur.execute(f"SELECT fingerprint FROM {grid.table('grid_info')} LIMIT 1")
        previous = cur.fetchone()
        if previous is not None and previous[0] != fingerprint:
            for table, command in other_tables.items():
                if table_exists(conn, grid.table(table)):
                    cur.execute(f"DROP TABLE {grid.table(table)}")
                    print(f"  Removed '{table}' from '{name}': it was built from a different TIFF. "
                          f"Rebuild it for this TIFF with:\n    {command} --map \"{name}\"")
    save_grid(conn, grid, tif_path)

    total = counts.sum()
    shares = ", ".join(f"{CLASS_NAMES[c]} {100 * counts[c] / total:.1f}%" for c in COLOURS if counts[c])
    print(f"  Colours: {shares}")
    print(f"  Not stored: {CLASS_NAMES[default]} (the most common colour). "
          f"Anything not in the database is {CLASS_NAMES[default]}.")
    return grid, counts


def stored_colours(grid: Grid, counts: np.ndarray) -> list[int]:
    """Every colour that appears in the map, except the one left out."""
    return [c for c in COLOURS if c != grid.default_class and counts[c]]


# --------------------------------------------------------------------------------------
# Farms
# --------------------------------------------------------------------------------------
@dataclass
class Farm:
    plot_id: str
    feature: dict          # the original GeoJSON feature (saved to the database as-is)
    geom: object           # shapely geometry in the MAP's coordinate system
    kind: str              # "point" or "outline"
    declared_ha: float | None = None
    supplier_id: str | None = None


POINT_AREA_HA = 4.0  # a point with no declared area stands for a plot of up to 4 ha (EU Information System)


def circle_from_point(lon: float, lat: float, area_ha: float | None) -> Polygon:
    """A circle with the farm's declared area (4 ha if none), centred on its point, in degrees.
    Made exactly as eudr_layers.read_plots makes it, so a point covers the same pixels here as in
    the evidence check."""
    area_ha = area_ha or POINT_AREA_HA
    r = math.sqrt(area_ha * 1e4 / math.pi)
    unit = Point(0, 0).buffer(1, 64)
    return affinity.translate(affinity.scale(unit, r / (111320 * math.cos(math.radians(lat))), r / 110574,
                                             origin=(0, 0)), lon, lat)


def _polygonal(geom):
    """Keep only the polygon parts (make_valid can return a mix of shapes)."""
    if geom.geom_type in ("Polygon", "MultiPolygon"):
        return geom
    parts = [g for g in getattr(geom, "geoms", []) if g.geom_type in ("Polygon", "MultiPolygon")]
    return shapely.unary_union(parts) if parts else None


def read_features(path: str) -> list[dict]:
    """The features of a GeoJSON file: a FeatureCollection, one Feature, or a bare shape."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if data.get("type") == "FeatureCollection":
        return data["features"]
    if data.get("type") == "Feature":
        return [data]
    return [{"type": "Feature", "properties": {}, "geometry": data}]  # e.g. {"type": "Polygon", ...}


def load_farms(path: str, grid: Grid, point_mode: str = "pixel") -> list[Farm]:
    """Read farms from a GeoJSON file (coordinates are longitude/latitude, EPSG:4326).

    point_mode:
      "pixel"  - a point is checked against the single pixel it lands on
      "circle" - a point is turned into a circle of its declared area, 4 ha if none (as
                 eudr_layers.py and the EU Information System read it)
    """
    farms = farms_from_features(read_features(path), grid, point_mode)
    print(f"Loaded {len(farms)} farm{'' if len(farms) == 1 else 's'} from {os.path.basename(path)} "
          f"({sum(f.kind == 'point' for f in farms)} points, "
          f"{sum(f.kind == 'outline' for f in farms)} outlines)")
    return farms


def farms_from_points(points: list[tuple[float, float]], grid: Grid) -> list[Farm]:
    """Single test points typed on the command line, as (longitude, latitude)."""
    features = [{"type": "Feature", "properties": {"plot_id": f"point {lon:.6f}, {lat:.6f}"},
                 "geometry": {"type": "Point", "coordinates": [lon, lat]}} for lon, lat in points]
    return farms_from_features(features, grid)


def plot_ids(features: list[dict]) -> list[str]:
    """One id per GeoJSON feature: its plot_id, id or ProductionPlace (the EU Information System's
    name, used by eudr_layers.py's test plots), else plot-<n>. Repeats become <id>-2, <id>-3, ..."""
    ids, seen = [], Counter()
    for i, feat in enumerate(features):
        props = feat.get("properties") or {}
        plot_id = str(props.get("plot_id") or props.get("id") or props.get("ProductionPlace")
                      or feat.get("id") or f"plot-{i + 1}")
        seen[plot_id] += 1
        if seen[plot_id] > 1:
            plot_id = f"{plot_id}-{seen[plot_id]}"
            print(f"Warning: duplicate plot id, renamed to {plot_id}")
        ids.append(plot_id)
    return ids


def declared_area(props: dict) -> float | None:
    """A farm's declared size in hectares (area_ha, declared_ha, area, or the EU format's Area)."""
    declared = props.get("area_ha", props.get("declared_ha", props.get("area", props.get("Area"))))
    return float(declared) if declared not in (None, "") else None


def farms_from_features(features: list[dict], grid: Grid, point_mode: str = "pixel") -> list[Farm]:
    """GeoJSON features -> farms in the map's coordinate system."""
    to_map = None
    if grid.srid != 4326:
        to_map = Transformer.from_crs(4326, grid.srid, always_xy=True).transform

    farms = []
    for feat, plot_id in zip(features, plot_ids(features)):
        props = feat.get("properties") or {}
        declared = declared_area(props)

        if not feat.get("geometry"):
            print(f"Skipping {plot_id}: no geometry")
            continue
        geom = shape(feat["geometry"])
        if geom.geom_type == "Point":
            kind = "point"
            if point_mode == "circle":
                geom, kind = circle_from_point(geom.x, geom.y, declared), "outline"
        elif geom.geom_type in ("Polygon", "MultiPolygon"):
            kind = "outline"
            if not geom.is_valid:
                geom = _polygonal(shapely.make_valid(geom))
                if geom is None or geom.is_empty:
                    print(f"Skipping {plot_id}: outline could not be repaired")
                    continue
        else:
            print(f"Skipping {plot_id}: unsupported geometry {geom.geom_type}")
            continue
        if to_map:
            geom = reproject(to_map, geom)
        farms.append(Farm(plot_id, feat, geom, kind, declared, props.get("supplier_id")))
    return farms


@functools.lru_cache(maxsize=16)
def _map_box(grid: Grid) -> Polygon:
    outline = grid.bounds_box()
    shapely.prepare(outline)
    return outline


def farm_outside(farm: Farm, grid: Grid) -> bool:
    if farm.kind == "point":
        return not grid.inside(*grid.point_to_pixel(farm.geom.x, farm.geom.y))
    return not _map_box(grid).contains(farm.geom)


def farm_pixels(farm: Farm, grid: Grid, all_touched: bool = False):
    """Pixels an outline covers: returns (row_off, col_off, mask), mask True = covered.

    all_touched=False - pixels whose centre is inside the farm (the default). This is
                        eudr_layers.plot_mask's rule, done the same way (GDAL rasterising, in C):
                        a farm smaller than a pixel gets every pixel it touches.
    all_touched=True  - every pixel that overlaps the farm, however little (the cautious rule)
    Every method uses this same function, so they always look at exactly the same pixels.
    """
    r0, c0, r1, c1 = grid.pixel_range(farm.geom)
    if not all_touched:
        shape_ = (r1 - r0 + 1, c1 - c0 + 1)
        transform = grid.window_transform(r0, c0)
        mask = geometry_mask([farm.geom], out_shape=shape_, transform=transform, invert=True)
        if not mask.any():  # farm smaller than a pixel: every pixel it touches, as eudr_layers.plot_mask
            mask = geometry_mask([farm.geom], out_shape=shape_, transform=transform, invert=True,
                                 all_touched=True)
        return r0, c0, mask
    cols = np.arange(c0, c1 + 1)
    rows = np.arange(r0, r1 + 1)
    shapely.prepare(farm.geom)
    left, top = np.meshgrid(grid.x0 + cols * grid.pixel_w, grid.y0 - rows * grid.pixel_h)
    pixels = shapely.box(left, top - grid.pixel_h, left + grid.pixel_w, top)
    # overlapping by some area = intersecting, but not merely touching along an edge
    mask = shapely.intersects(farm.geom, pixels) & ~shapely.touches(farm.geom, pixels)
    if not mask.any():  # farm smaller than a pixel: use the pixel under its middle
        row, col = grid.point_to_pixel(*farm.geom.representative_point().coords[0])
        mask[row - r0, col - c0] = True
    return r0, c0, mask


# --------------------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------------------
@dataclass
class PlotResult:
    plot_id: str
    kind: str
    verdict: str = PASS
    present: frozenset = frozenset()            # every colour the farm touches
    area_m2: dict = field(default_factory=dict)  # colour -> m2 inside the farm (outlines)
    point_colour: int | None = None             # point farms: the colour under the point
    water_share: float = 0.0
    outside: bool = False
    note: str = ""
    # protected areas (protected_areas.apply): the names of those the farm lies in, the hectares of
    # it inside them (outlines; 0 for points), and the verdict the map alone gave before that
    protected_areas: tuple = ()
    protected_ha: float = 0.0
    map_verdict: str | None = None

    def key(self) -> tuple:
        """Everything a compliance decision uses - what decision fidelity compares:
        the verdict, every colour touched (so: in forest? overlaps forest?) and the point colour."""
        return (self.verdict, self.present, self.point_colour)

    @property
    def outcome(self) -> str:
        """COMPLIANT, NON_COMPLIANT or NEEDS_REVIEW (the verdict says why)."""
        return outcome_of(self.verdict)

    @property
    def touches_forest(self) -> bool:
        """Touches forest still standing today."""
        return FOREST in self.present

    @property
    def in_forest_2020(self) -> bool:
        """The EUDR's two key questions: is the point in forest that stood on 31 Dec 2020, and does the
        outline overlap it? (Forest since cleared counts: that is what the cut-off date asks about.)"""
        return any(c in self.present for c in FOREST_2020)

    @property
    def in_protected(self) -> bool:
        """Lies in a protected area (WDPA), which sends it for review (protected_areas.apply)."""
        return bool(self.protected_areas)

    def ha(self, colour: int) -> float:
        return self.area_m2.get(colour, 0.0) / 10_000

    def forest_2020_ha(self) -> float:
        """Hectares of the farm that were forest on 31 Dec 2020 (outlines; 0 for points)."""
        return sum(self.ha(c) for c in FOREST_2020)

    def colours_text(self) -> str:
        return ", ".join(CLASS_NAMES[c] for c in sorted(self.present))


def _finish(res: PlotResult, present: set, area: dict, water_share: float) -> PlotResult:
    res.present = frozenset(present)
    res.area_m2 = {c: a for c, a in area.items() if c in present}
    res.water_share = water_share
    res.verdict = decide(present, water_share, res.outside)
    return res


def outside_result(farm: Farm) -> PlotResult:
    res = PlotResult(farm.plot_id, farm.kind, outside=True, note="outside the map")
    res.verdict = decide(set(), 0.0, outside=True)
    return res


def point_result(farm: Farm, colour: int) -> PlotResult:
    """A point farm takes the colour of the pixel it lands on."""
    res = PlotResult(farm.plot_id, farm.kind, point_colour=colour)
    return _finish(res, {colour}, {}, 1.0 if colour == WATER else 0.0)


def pixel_result(farm: Farm, grid: Grid, counts, r0: int, nrows: int) -> PlotResult:
    """Pixel rule: counts[c] = pixels of colour c that the farm covers."""
    res = PlotResult(farm.plot_id, farm.kind)
    total = int(sum(counts))
    px_area = grid.pixel_area_m2(r0 + nrows // 2)
    present = {c for c in COLOURS if counts[c] > 0}
    area = {c: int(counts[c]) * px_area for c in present}
    return _finish(res, present, area, counts[WATER] / total if total else 0.0)


def exact_result(farm: Farm, areas: dict, farm_area: float) -> PlotResult:
    """Exact rule: areas[c] = m2 of colour c inside the farm; farm_area = the farm's m2."""
    res = PlotResult(farm.plot_id, farm.kind)
    threshold = max(MIN_OVERLAP_M2, MIN_OVERLAP_SHARE * farm_area)
    present = {c for c, a in areas.items() if a > threshold}
    water = areas.get(WATER, 0.0)
    return _finish(res, present, areas, water / farm_area if farm_area else 0.0)


def save_results(conn: psycopg.Connection, grid: Grid, farms: list[Farm],
                 results: list[PlotResult], method: str) -> None:
    with conn.cursor() as cur:
        cur.execute(map_tables_sql(grid))
        cur.executemany(
            f"INSERT INTO {grid.table('plots')} (plot_id, supplier_id, declared_ha, geojson) VALUES (%s,%s,%s,%s) "
            "ON CONFLICT (plot_id) DO UPDATE SET supplier_id = EXCLUDED.supplier_id, "
            "declared_ha = EXCLUDED.declared_ha, geojson = EXCLUDED.geojson",
            [(f.plot_id, f.supplier_id, f.declared_ha, Jsonb(f.feature)) for f in farms])
        cur.executemany(
            f"INSERT INTO {grid.table('plot_results')} (plot_id, map_version, method, verdict, colours, point_colour, "
            "forest_ha, land_ha, deforested_ha, review_ha, water_ha, no_data_ha, note) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (plot_id, map_version, method) DO UPDATE SET verdict = EXCLUDED.verdict, "
            "colours = EXCLUDED.colours, point_colour = EXCLUDED.point_colour, "
            "forest_ha = EXCLUDED.forest_ha, land_ha = EXCLUDED.land_ha, "
            "deforested_ha = EXCLUDED.deforested_ha, review_ha = EXCLUDED.review_ha, "
            "water_ha = EXCLUDED.water_ha, no_data_ha = EXCLUDED.no_data_ha, "
            "note = EXCLUDED.note, checked_at = now()",
            [(r.plot_id, grid.map_version, method, r.verdict, r.colours_text(),
              CLASS_NAMES.get(r.point_colour), r.ha(FOREST), r.ha(LAND), r.ha(DEFORESTATION),
              r.ha(REVIEW), r.ha(WATER), r.ha(NO_DATA), r.note) for r in results])
    conn.commit()


def make_parent(path: str) -> None:
    """Create the folder a file will be written into (e.g. results/) if it doesn't exist."""
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)


def write_csv(path: str, results: list[PlotResult]) -> None:
    make_parent(path)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["plot_id", "kind", "outcome", "verdict", "point_colour", "colours_touched", "in_forest_2020",
                    "forest_2020_ha", "touches_forest", "forest_ha", "land_ha", "deforested_ha", "review_ha",
                    "water_ha", "no_data_ha", "water_share", "in_protected_area", "protected_ha",
                    "protected_areas", "note"])
        for r in results:
            w.writerow([r.plot_id, r.kind, r.outcome, r.verdict, CLASS_NAMES.get(r.point_colour, ""),
                        r.colours_text(), r.in_forest_2020, f"{r.forest_2020_ha():.4f}", r.touches_forest,
                        f"{r.ha(FOREST):.4f}", f"{r.ha(LAND):.4f}", f"{r.ha(DEFORESTATION):.4f}",
                        f"{r.ha(REVIEW):.4f}", f"{r.ha(WATER):.4f}", f"{r.ha(NO_DATA):.4f}",
                        f"{r.water_share:.3f}", r.in_protected, f"{r.protected_ha:.4f}",
                        "; ".join(r.protected_areas), r.note])
    print(f"Wrote {nice_path(path)}")


def print_summary(method: str, results: list[PlotResult], timings: dict) -> None:
    n = len(results)
    counts = Counter(r.verdict for r in results)
    total = timings["total_s"]
    print(f"\n{method}: checked {n} farms in {total:.3f} s ({1000 * total / max(n, 1):.2f} ms per farm)")
    for o in OUTCOMES:
        print(f"  {o:15s} {sum(counts.get(v, 0) for v in VERDICTS if OUTCOME_OF[v] == o)}")
    print("  why: " + ", ".join(f"{v} {counts.get(v, 0)}" for v in VERDICTS))
    in_2020 = sum(r.kind == "point" and r.in_forest_2020 for r in results)
    overlap_2020 = sum(r.kind == "outline" and r.in_forest_2020 for r in results)
    in_forest = sum(r.kind == "point" and r.touches_forest for r in results)
    overlap = sum(r.kind == "outline" and r.touches_forest for r in results)
    print(f"  Forest on 31 Dec 2020: {in_2020} point farms are in it, {overlap_2020} outline farms overlap it")
    print(f"  Forest standing today: {in_forest} point farms are in it, {overlap} outline farms overlap it")
    if timings.get("used_labels"):
        print(f"  {timings['by_label']} of {n} farms were answered from the square labels alone (no geometry needed)")


# --------------------------------------------------------------------------------------
# The baseline: checking farms directly against the original TIFF
# --------------------------------------------------------------------------------------
def check_with_tiff(tif_path: str, grid: Grid, farms: list[Farm], mode: str = "pixel",
                    all_touched: bool = False) -> tuple[list[PlotResult], dict]:
    """Check every farm by reading the original TIFF - the reference answer.

    mode="pixel" - count the pixels each farm covers (same rule as the stencil approach)
    mode="exact" - measure the exact overlap area (same rule as the polygon approach)
    """
    start = time.perf_counter()
    results = []
    with rasterio.open(tif_path) as src:
        for farm in farms:
            if farm_outside(farm, grid):
                results.append(outside_result(farm))
                continue
            if farm.kind == "point":
                row, col = grid.point_to_pixel(farm.geom.x, farm.geom.y)
                colour = int(normalise(src.read(1, window=Window(col, row, 1, 1)))[0, 0])
                results.append(point_result(farm, colour))
            elif mode == "pixel":
                r0, c0, mask = farm_pixels(farm, grid, all_touched)
                data = normalise(src.read(1, window=Window(c0, r0, mask.shape[1], mask.shape[0])))
                counts = np.bincount(data[mask], minlength=len(COLOURS))
                results.append(pixel_result(farm, grid, counts, r0, mask.shape[0]))
            else:
                r0, c0, r1, c1 = grid.pixel_range(farm.geom)
                data = normalise(src.read(1, window=Window(c0, r0, c1 - c0 + 1, r1 - r0 + 1)))
                areas = Counter()
                for colour, poly in trace(data, COLOURS, grid, r0, c0):
                    if poly.intersects(farm.geom):
                        areas[colour] += area_m2(poly.intersection(farm.geom), grid)
                results.append(exact_result(farm, dict(areas), area_m2(farm.geom, grid)))
    return results, {"total_s": time.perf_counter() - start}
