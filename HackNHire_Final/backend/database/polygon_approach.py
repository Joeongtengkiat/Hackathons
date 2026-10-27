"""
polygon_approach.py - the OUTLINES approach (your team's vector idea).

Every patch of colour is traced into an outline that follows the pixel edges exactly, and
stored in PostGIS with a spatial index. Only the map's most common colour (usually land) is
left out: anything that is not inside an outline is that colour. Nothing is lost - the whole
map can be rebuilt pixel for pixel.

Three things keep the checks fast:
  * Outlines are traced in small 64 x 64-pixel tiles, so every piece has few corners
    (measured: about 30% faster than 256-pixel tiles, for about 40% more storage).
  * Square labels: for every 16 x 16-pixel square, a label says "all one colour (which)" or
    "mixed". In a big batch (LABELS_FROM farms or more), a farm whose squares are all the same
    colour is answered from the labels alone, with no geometry at all. The labels are zipped
    per page, so they take very little space. Smaller checks skip them: fetching them is one
    more round trip to Neon, which costs more than they save on a few farms.
  * Before a big query, PostgreSQL's JIT compiler is switched off: it spent ~150 ms compiling
    the overlap query, which then ran in ~65 ms (see common.no_jit).
  * Points and outlines are answered in one query, and an outline that covers a whole farm is
    measured without cutting it (ST_Covers): the overlap is simply the farm.

Outlines are an alternative kept for the evaluation: they take ~24x the stencils' storage for the
same 5 colours and measure exact areas (a different rule from eudr_layers.py's pixel centres).
maps.py upload --no-outlines leaves them out.

The colours are the compaction step's outcome in 5 colours (layers/<map>/outcome_5class.tif, see
layers.py), or any TIFF in the 5 colours.

Commands (run from the backend folder; give a path, or a path inside layers/)
    python database/polygon_approach.py build  jambi/outcome_5class.tif [--map NAME]
    python database/polygon_approach.py check  jambi/test_plots.geojson [--map NAME] [--point-mode pixel|circle] [--csv FILE]
    python database/polygon_approach.py verify jambi/outcome_5class.tif [--map NAME]
(To upload a new map with every approach at once, use database/maps.py upload.)

Tables (in the map's own schema; defined in schema.sql, sections @outlines and @outlines_keys)
    class_polys     one row per outline: its colour and its shape (with a spatial index)
    square_labels   one row per 256 x 256 page: the colour of each 16 x 16 square (255 = mixed), zipped
"""

import argparse
import math
import os
import time
import zlib
from collections import defaultdict

import numpy as np
import rasterio
import shapely
from rasterio.features import rasterize

import common
import protected_areas

METHOD = "polygons"
TABLES = ["class_polys", "square_labels"]
OTHER_TABLES = {"stencil_pages": "python database/stencil_approach.py build <your map>"}
TRACE_TILE = 64              # outlines are traced in 64 x 64-pixel tiles (must divide 256)
SQUARE = 16                  # label squares are 16 x 16 pixels (must divide 256)
MIXED = 255                  # label value for a square that holds more than one colour
LABELS_FROM = 200            # checks of at least this many farms at once use the labels
JIT_OFF_OUTLINES = 50        # PostgreSQL starts JIT-compiling the overlap query at ~100 outlines,
JIT_OFF_POINTS = 800         # and the point query at ~1,600 points: switch JIT off well before
PAGE = common.PAGE
SQUARES_PER_PAGE = PAGE // SQUARE


def ewkb(geom, srid: int) -> str:
    """Shape -> hex EWKB text. Binary coordinates, so nothing is rounded on the way in."""
    return shapely.to_wkb(shapely.set_srid(geom, srid), hex=True, include_srid=True)


def ewkb_many(geoms, srid: int) -> list[bytes]:
    """Many shapes -> binary EWKB, in one go. Binary is half the size of hex text, so less to send."""
    return list(shapely.to_wkb(shapely.set_srid(np.asarray(geoms, dtype=object), srid), include_srid=True))


def square_labels(data: np.ndarray) -> np.ndarray:
    """A page of colours -> one label per 16 x 16 square: its colour if it is all one colour,
    otherwise MIXED. Squares cut off by the map edge only look at the pixels that exist."""
    h, w = data.shape
    H, W = -(-h // SQUARE) * SQUARE, -(-w // SQUARE) * SQUARE
    low = np.full((H, W), 255, np.uint8)   # padding never lowers the minimum...
    high = np.zeros((H, W), np.uint8)      # ...or raises the maximum
    low[:h, :w] = data
    high[:h, :w] = data
    low = low.reshape(H // SQUARE, SQUARE, W // SQUARE, SQUARE).min(axis=(1, 3))
    high = high.reshape(H // SQUARE, SQUARE, W // SQUARE, SQUARE).max(axis=(1, 3))
    return np.where(low == high, low, MIXED).astype(np.uint8)


def ensure_built(conn, grid: common.Grid) -> None:
    if not common.has_tables(conn, grid, "class_polys", "square_labels"):
        raise SystemExit(f"The map '{grid.map_version}' has no outlines (it was uploaded with --no-outlines, "
                         "or by an older version). Build them for it:\n"
                         f"  python database/polygon_approach.py build jambi/outcome_5class.tif --map \"{grid.map_version}\"\n"
                         "or upload the map again without --no-outlines.")


# --------------------------------------------------------------------------------------
# build: trace every colour except the most common one, tile by tile, and upload
# --------------------------------------------------------------------------------------
def build(conn, tif: str, map_name: str, report=None, tidy: bool = True, allow_copy: bool = False) -> common.Grid:
    """Trace the TIFF into the map's class_polys and square_labels. report(label, done, total)
    follows the progress. tidy=False leaves the transaction open and skips VACUUM (maps.upload
    builds both approaches in one transaction, then tidies up)."""
    start = time.perf_counter()
    with rasterio.open(tif) as src:
        grid, counts = common.start_build(conn, src, tif, map_name, OTHER_TABLES, report, allow_copy)
        stored = common.stored_colours(grid, counts)
        polys, labels_table = grid.table("class_polys"), grid.table("square_labels")
        with conn.cursor() as cur:
            # PostGIS lives in public, so deleting a map's schema can never remove it (maps.py init
            # sets it up; this makes sure, for a database set up before)
            cur.execute(common.schema_sql("postgis"))
            cur.execute(f"DROP TABLE IF EXISTS {polys}, {labels_table}")
            cur.execute(common.schema_sql("outlines", grid))    # schema.sql: the two tables

        rows, labels = [], []
        progress = common.Progress(common.page_count(src), "tracing outlines", report)
        for page in common.iter_pages(src):
            h, w = page.data.shape
            for r in range(0, h, TRACE_TILE):
                for c in range(0, w, TRACE_TILE):
                    tile = page.data[r:r + TRACE_TILE, c:c + TRACE_TILE]
                    for colour, poly in common.trace(tile, stored, grid, page.row_off + r, page.col_off + c):
                        rows.append((colour, ewkb(poly, grid.srid)))
            labels.append((page.page_col, page.page_row, zlib.compress(square_labels(page.data).tobytes(), 9)))
            if len(rows) > 20_000:
                common.copy_rows(conn, polys, ["class_id", "geom"], rows)
                rows.clear()
            progress.step()
        common.copy_rows(conn, polys, ["class_id", "geom"], rows)
        common.copy_rows(conn, labels_table, ["page_col", "page_row", "labels"], labels)

        print("  building the indexes...")
        if report is not None:
            report("building the indexes", 0, 1)
        with conn.cursor() as cur:
            cur.execute(common.schema_sql("outlines_keys", grid))
        if report is not None:
            report("building the indexes", 1, 1)  # last report before the commit: a Cancel still undoes it all
    if tidy:
        common.tidy_up(conn, polys)
        common.tidy_up(conn, labels_table)
        print(f"Built in {time.perf_counter() - start:.1f} s")
        common.print_storage(conn, [grid.table(t) for t in TABLES + ["grid_info"]])
    return grid


# --------------------------------------------------------------------------------------
# check: labels first; then one query for the remaining points, one for the remaining outlines
# --------------------------------------------------------------------------------------
def fetch_labels(cur, grid: common.Grid, spans: dict) -> dict:
    """Fetch and unzip the label pages that the farms' pixel ranges fall on."""
    needed = set()
    for r0, c0, r1, c1 in spans.values():
        for pr in range(r0 // PAGE, r1 // PAGE + 1):
            for pc in range(c0 // PAGE, c1 // PAGE + 1):
                needed.add((pc, pr))
    if not needed:
        return {}
    page_cols, page_rows = zip(*needed)
    cur.execute(f"""
        SELECT s.page_col, s.page_row, s.labels
        FROM unnest(%s::int[], %s::int[]) AS q(pc, pr)
        JOIN {grid.table("square_labels")} s ON s.page_col = q.pc AND s.page_row = q.pr
        """, (list(page_cols), list(page_rows)))
    pages = {}
    for pc, pr, blob in cur.fetchall():
        h, w = min(PAGE, grid.height - pr * PAGE), min(PAGE, grid.width - pc * PAGE)
        pages[(pc, pr)] = np.frombuffer(zlib.decompress(blob), np.uint8).reshape(-(-h // SQUARE), -(-w // SQUARE))
    return pages


def uniform_colour(pages: dict, span: tuple) -> int | None:
    """If every 16 x 16 square under a farm is the same single colour, return it; else None."""
    r0, c0, r1, c1 = span
    found = None
    for sr in range(r0 // SQUARE, r1 // SQUARE + 1):
        for sc in range(c0 // SQUARE, c1 // SQUARE + 1):
            label = int(pages[(sc // SQUARES_PER_PAGE, sr // SQUARES_PER_PAGE)]
                        [sr % SQUARES_PER_PAGE, sc % SQUARES_PER_PAGE])
            if label == MIXED or (found is not None and label != found):
                return None
            found = label
    return found


def check(conn, grid: common.Grid, farms: list[common.Farm]):
    """Returns (results, timings). Every answer is exact; the labels only skip geometry work
    for farms that sit entirely in one colour (checked: identical answers).
    Round trips to the database: one for the points and outlines together, plus one for the
    labels in a big batch, and one to switch JIT off before a big query."""
    ensure_built(conn, grid)
    t0 = time.perf_counter()
    results: list = [None] * len(farms)
    spans = {}
    for i, farm in enumerate(farms):
        if common.farm_outside(farm, grid):
            results[i] = common.outside_result(farm)
        elif farm.kind == "point":
            row, col = grid.point_to_pixel(farm.geom.x, farm.geom.y)
            spans[i] = (row, col, row, col)
        else:
            spans[i] = grid.pixel_range(farm.geom)  # the pixels around the outline, plus a margin

    points, outlines, by_label = [], [], 0
    colour_at, overlaps = {}, defaultdict(dict)
    with conn.cursor() as cur:
        # 1. Labels (big batches only): farms that sit entirely in one colour are answered
        #    straight away. For fewer farms, the extra round trip costs more than it saves.
        pages = fetch_labels(cur, grid, spans) if len(spans) >= LABELS_FROM else {}
        for i, span in spans.items():
            colour = uniform_colour(pages, span) if pages else None
            if colour is None:
                (points if farms[i].kind == "point" else outlines).append(i)
            elif farms[i].kind == "point":
                results[i] = common.point_result(farms[i], colour)
                by_label += 1
            else:
                area = common.area_m2(farms[i].geom, grid)
                results[i] = common.exact_result(farms[i], {colour: area}, area)
                by_label += 1

        if len(outlines) >= JIT_OFF_OUTLINES or len(points) >= JIT_OFF_POINTS:
            common.no_jit(cur)  # a big query: PostgreSQL's JIT compiler would waste up to ~150 ms on it
        # Points and outlines go in ONE query (one round trip), as two parts joined by UNION ALL.
        parts, params = [], []
        if points:
            # 2. "Is the point in forest?" - which outline holds the point. The centre of the
            #    pixel under the point is used, so a point on a pixel edge never matches two.
            xs, ys = [], []
            for i in points:
                x, y = grid.pixel_centre(*grid.point_to_pixel(farms[i].geom.x, farms[i].geom.y))
                xs.append(x)
                ys.append(y)
            parts.append(f"""
                SELECT 0 AS part, q.idx, r.class_id, 0::float8 AS m2
                FROM unnest(%s::int[], %s::float8[], %s::float8[]) AS q(idx, x, y)
                JOIN {grid.table("class_polys")} r ON ST_Intersects(r.geom, ST_SetSRID(ST_MakePoint(q.x, q.y), {grid.srid}))""")
            params += [points, xs, ys]
        if outlines:
            # 3. "Does the outline overlap forest?" - how much of each colour lies inside each farm.
            #    The spatial index finds the few outlines near each farm; only those are measured.
            #    An outline that covers the whole farm needs no intersection: the overlap is the farm.
            geography = "::geography" if grid.geographic else ""
            parts.append(f"""
                SELECT 1 AS part, f.idx, r.class_id,
                       SUM(CASE WHEN ST_Covers(r.geom, f.geom) THEN ST_Area(f.geom{geography})
                                ELSE ST_Area(ST_Intersection(r.geom, f.geom){geography}) END) AS m2
                FROM (SELECT q.idx, ST_GeomFromEWKB(q.g) AS geom
                      FROM unnest(%s::int[], %s::bytea[]) AS q(idx, g)) AS f
                JOIN {grid.table("class_polys")} r ON ST_Intersects(r.geom, f.geom)
                GROUP BY f.idx, r.class_id""")
            params += [outlines, ewkb_many([farms[i].geom for i in outlines], grid.srid)]
        if parts:
            cur.execute(" UNION ALL ".join(f"({p})" for p in parts), params)
            for part, idx, colour, m2 in cur.fetchall():
                if part == 0:
                    colour_at[idx] = colour
                else:
                    overlaps[idx][colour] = m2

    default = grid.default_class
    for i in points:
        results[i] = common.point_result(farms[i], colour_at.get(i, default))
    for i in outlines:
        farm_area = common.area_m2(farms[i].geom, grid)  # agrees with PostGIS to 1 part in 100 million
        areas = overlaps.get(i, {})
        areas[default] = max(farm_area - sum(areas.values()), 0.0)  # whatever is left over
        results[i] = common.exact_result(farms[i], areas, farm_area)
    return results, {"total_s": time.perf_counter() - t0, "by_label": by_label, "used_labels": bool(pages)}


# --------------------------------------------------------------------------------------
# verify: rebuild the whole map from the outlines and compare it with the TIFF
# --------------------------------------------------------------------------------------
def verify(conn, grid: common.Grid, tif: str) -> dict:
    ensure_built(conn, grid)
    mismatched = total = 0
    with rasterio.open(tif) as src, conn.cursor() as cur:
        fingerprint = common.Fingerprint(common.same_grid_or_exit(src, grid))
        progress = common.Progress(math.ceil(src.height / PAGE), "rebuilding the map")
        for row_off, expected in common.iter_strips(src):
            fingerprint.add(expected)
            h, w = expected.shape
            cur.execute(f"SELECT class_id, ST_AsBinary(geom) FROM {grid.table('class_polys')} "
                        f"WHERE geom && ST_MakeEnvelope(%s, %s, %s, %s, {grid.srid})",
                        grid.window_bounds(row_off, 0, h, w))
            outlines = [(shapely.from_wkb(bytes(b)), colour) for colour, b in cur.fetchall()]
            rebuilt = np.full((h, w), grid.default_class, np.uint8)
            if outlines:
                rebuilt = rasterize(outlines, out_shape=(h, w), fill=grid.default_class, dtype="uint8",
                                    transform=grid.window_transform(row_off, 0))
            mismatched += int((rebuilt != expected).sum())
            total += h * w
            progress.step()
    return {"pixels": total, "mismatched": mismatched,
            "fidelity_pct": 100.0 * (1 - mismatched / total),
            "same_map": fingerprint.hexdigest() == grid.fingerprint}


# --------------------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="trace the TIFF and upload the outlines")
    b.add_argument("tif", help="the 5-colour TIFF (a path, or a path inside layers/, e.g. jambi/outcome_5class.tif)")
    b.add_argument("--map", help="the map's name (default: the TIFF's file name)")
    c = sub.add_parser("check", help="give a verdict for every farm in a GeoJSON file")
    c.add_argument("plots", help="GeoJSON of farms (a path, or a path inside layers/, e.g. jambi/test_plots.geojson)")
    c.add_argument("--map", help="which stored map to check against (needed when there are several)")
    c.add_argument("--point-mode", choices=["pixel", "circle"], default="pixel",
                   help="pixel: check the pixel under each point; circle: use the declared area")
    c.add_argument("--csv", default=os.path.join(common.DATABASE_DIR, "results", "results_polygons.csv"),
                   help="where to write the verdicts (default: database/results/results_polygons.csv)")
    v = sub.add_parser("verify", help="rebuild the map from the database and compare it with the TIFF")
    v.add_argument("tif", help="the 5-colour TIFF (a path, or a path inside layers/, e.g. jambi/outcome_5class.tif)")
    v.add_argument("--map", help="which stored map to rebuild (needed when there are several)")
    args = ap.parse_args()
    if args.cmd == "check":
        args.plots = common.find_geojson(args.plots)
    else:
        args.tif = common.find_tiff(args.tif)

    with common.connect() as conn:
        if args.cmd == "build":
            build(conn, args.tif, args.map or common.default_map_name(args.tif))
        elif args.cmd == "check":
            grid = common.load_grid(conn, args.map)
            farms = common.load_farms(args.plots, grid, args.point_mode)
            results, timings = check(conn, grid, farms)
            protected_areas.apply(results, protected_areas.check(conn, grid, farms))
            common.save_results(conn, grid, farms, results, METHOD)
            common.print_summary(METHOD, results, timings)
            common.write_csv(args.csv, results)
        else:
            common.verify_report("Outlines", verify(conn, common.load_grid(conn, args.map), args.tif))


if __name__ == "__main__":
    main()
