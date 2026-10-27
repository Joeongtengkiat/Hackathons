"""
stencil_approach.py - the STENCILS approach: the map in zipped pages, answered on the client.

The map is cut into 256 x 256-pixel pages (Matthew's stencil idea: fetch only the pages a farm
touches, in one query, and work out the answer locally). Each page holds its pixels' colours, one
byte per pixel, compressed with lzma (Python's standard library, as raw LZMA2 with a 64 KB
dictionary, so there are no headers to store and no big buffer to allocate when unpacking).

The first version stored each colour as an 8 x 8-pixel bitmap packed into a 64-bit number, zipped.
Measured on the 6,000 x 6,000 Jambi map (30% forest), plain colour bytes take 25% less space with
lzma (0.71 MB of pages instead of 0.95 MB) - the bitmaps only win on maps where every colour is
rare - and a page unpacks in about the same time (~0.5 ms).

Nothing is lost: pages that are all the most common colour (usually land) get no row, and the
whole map can be rebuilt pixel for pixel.

Needs only plain PostgreSQL - no PostGIS. Checking a farm (one query for all the farms):
    point   -> unpack the page it is on and read one pixel
    outline -> unpack the pages it covers and count the colours of the pixels it covers
               (common.farm_pixels: pixel centres inside, as eudr_layers.py; or --rule touched)

The colours are the compaction step's outcome in 5 colours (layers/<map>/outcome_5class.tif, see
layers.py), or any TIFF in the 5 colours.

Commands (run from the backend folder; give a path, or a path inside layers/)
    python database/stencil_approach.py build  jambi/outcome_5class.tif [--map NAME]
    python database/stencil_approach.py check  jambi/test_plots.geojson [--map NAME] [--rule centre|touched]
                                               [--point-mode pixel|circle] [--csv FILE]
    python database/stencil_approach.py verify jambi/outcome_5class.tif [--map NAME]
(To upload a new map with every approach at once, use database/maps.py upload.)

Table (in the map's own schema; defined in schema.sql, sections @stencils and @stencils_keys)
    stencil_pages   one row per page: (page_col, page_row, colours)
                    colours = the page's colour codes 0-5, one byte per pixel, lzma-compressed
"""

import argparse
import lzma
import math
import os
import time

import numpy as np
import rasterio

import common
import protected_areas

METHOD = "stencils"
TABLES = ["stencil_pages"]
OTHER_TABLES = {"class_polys": "python database/polygon_approach.py build <your map>"}
PAGE = common.PAGE             # pages are 256 x 256 pixels
# raw LZMA2 (no container): a page is at most 64 KB, so a bigger dictionary would only cost memory
LZMA_FILTERS = [{"id": lzma.FILTER_LZMA2, "preset": 9 | lzma.PRESET_EXTREME, "dict_size": 1 << 16}]


def page_size(grid: common.Grid, page_col: int, page_row: int) -> tuple[int, int]:
    """(height, width) in pixels - pages on the right and bottom edges can be smaller."""
    return (min(PAGE, grid.height - page_row * PAGE), min(PAGE, grid.width - page_col * PAGE))


def pack_page(data: np.ndarray) -> bytes:
    """A page of colours (height x width, values 0-5) -> what is stored."""
    return lzma.compress(np.ascontiguousarray(data, np.uint8).tobytes(), format=lzma.FORMAT_RAW,
                         filters=LZMA_FILTERS)


def unzip_page(blob: bytes, height: int, width: int) -> np.ndarray:
    """A stored page -> its colours (height x width, uint8)."""
    raw = lzma.decompress(bytes(blob), format=lzma.FORMAT_RAW, filters=LZMA_FILTERS)
    return np.frombuffer(raw, np.uint8).reshape(height, width)


def ensure_built(conn, grid: common.Grid) -> None:
    if not common.has_tables(conn, grid, "stencil_pages"):
        raise SystemExit(f"The map '{grid.map_version}' has no stencils yet - build them first:\n"
                         f"  python database/stencil_approach.py build jambi/outcome_5class.tif --map \"{grid.map_version}\"")


# --------------------------------------------------------------------------------------
# build
# --------------------------------------------------------------------------------------
def build(conn, tif: str, map_name: str, report=None, tidy: bool = True, allow_copy: bool = False) -> common.Grid:
    """Pack the TIFF into the map's stencil_pages. report(label, done, total) follows the progress.
    tidy=False leaves the transaction open and skips VACUUM (maps.upload builds every approach
    in one transaction, then tidies up)."""
    start = time.perf_counter()
    with rasterio.open(tif) as src:
        grid, _ = common.start_build(conn, src, tif, map_name, OTHER_TABLES, report, allow_copy)
        table = grid.table("stencil_pages")
        with conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {table}")
            cur.execute(common.schema_sql("stencils", grid))    # schema.sql: the table

        rows = []
        progress = common.Progress(common.page_count(src), "packing pages", report)
        for page in common.iter_pages(src):
            if (page.data != grid.default_class).any():  # a page all the most common colour needs no row
                rows.append((page.page_col, page.page_row, pack_page(page.data)))
            if len(rows) > 2_000:
                common.copy_rows(conn, table, ["page_col", "page_row", "colours"], rows)
                rows.clear()
            progress.step()
        common.copy_rows(conn, table, ["page_col", "page_row", "colours"], rows)

        print("  building the index...")
        if report is not None:
            report("building the index", 0, 1)
        with conn.cursor() as cur:
            # The primary key IS the index: a farm's location says exactly which pages to fetch.
            cur.execute(common.schema_sql("stencils_keys", grid))
        if report is not None:
            report("building the index", 1, 1)  # last report before the commit: a Cancel still undoes it all
    if tidy:
        common.tidy_up(conn, table)
        print(f"Built in {time.perf_counter() - start:.1f} s")
        common.print_storage(conn, [grid.table(t) for t in TABLES + ["grid_info"]])
    return grid


# --------------------------------------------------------------------------------------
# check
# --------------------------------------------------------------------------------------
def fetch_pages(conn, grid: common.Grid, needed: set) -> dict:
    """{(page_col, page_row): colours} for the needed pages, in one query. A page with no row is
    all the default colour, so it is simply missing from the answer."""
    pages = {}
    if needed:
        page_cols, page_rows = zip(*needed)
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT s.page_col, s.page_row, s.colours
                FROM unnest(%s::int[], %s::int[]) AS q(pc, pr)
                JOIN {grid.table("stencil_pages")} s ON s.page_col = q.pc AND s.page_row = q.pr
                """, (list(page_cols), list(page_rows)))
            for pc, pr, blob in cur.fetchall():
                pages[(pc, pr)] = unzip_page(blob, *page_size(grid, pc, pr))
    return pages


def colour_at(pages: dict, grid: common.Grid, row: int, col: int) -> int:
    """The colour of one pixel."""
    page = pages.get((col // PAGE, row // PAGE))
    return grid.default_class if page is None else int(page[row % PAGE, col % PAGE])


def window_colours(pages: dict, grid: common.Grid, r0: int, c0: int, h: int, w: int) -> np.ndarray:
    """The colours of a block of pixels (rows r0:r0+h, columns c0:c0+w)."""
    out = np.full((h, w), grid.default_class, np.uint8)
    for pr in range(r0 // PAGE, (r0 + h - 1) // PAGE + 1):
        for pc in range(c0 // PAGE, (c0 + w - 1) // PAGE + 1):
            page = pages.get((pc, pr))
            if page is None:
                continue
            top, left = pr * PAGE, pc * PAGE  # the page's first pixel
            r_from, r_to = max(r0, top), min(r0 + h, top + page.shape[0])
            c_from, c_to = max(c0, left), min(c0 + w, left + page.shape[1])
            out[r_from - r0:r_to - r0, c_from - c0:c_to - c0] = page[r_from - top:r_to - top, c_from - left:c_to - left]
    return out


def check(conn, grid: common.Grid, farms: list[common.Farm], all_touched: bool = False):
    """Returns (results, timings). One query fetches every page that any farm needs.
    all_touched=False: an outline covers the pixels whose centre is inside it (eudr_layers.py's
    rule, so the answers match the evidence check's); True: every pixel it overlaps (cautious)."""
    ensure_built(conn, grid)
    t0 = time.perf_counter()
    results: list = [None] * len(farms)
    points, outlines, needed = {}, {}, set()
    for i, farm in enumerate(farms):
        if common.farm_outside(farm, grid):
            results[i] = common.outside_result(farm)
        elif farm.kind == "point":
            row, col = grid.point_to_pixel(farm.geom.x, farm.geom.y)
            points[i] = (row, col)
            needed.add((col // PAGE, row // PAGE))
        else:
            r0, c0, mask = common.farm_pixels(farm, grid, all_touched)
            outlines[i] = (r0, c0, mask)
            for pr in range(r0 // PAGE, (r0 + mask.shape[0] - 1) // PAGE + 1):
                for pc in range(c0 // PAGE, (c0 + mask.shape[1] - 1) // PAGE + 1):
                    needed.add((pc, pr))

    pages = fetch_pages(conn, grid, needed)
    for i, (row, col) in points.items():
        results[i] = common.point_result(farms[i], colour_at(pages, grid, row, col))
    for i, (r0, c0, mask) in outlines.items():
        colours = window_colours(pages, grid, r0, c0, *mask.shape)
        counts = np.bincount(colours[mask], minlength=len(common.COLOURS))
        results[i] = common.pixel_result(farms[i], grid, counts, r0, mask.shape[0])
    return results, {"total_s": time.perf_counter() - t0, "pages": len(pages)}


# --------------------------------------------------------------------------------------
# verify: unpack every page back into pixels and compare with the TIFF
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
            rebuilt = np.full((h, w), grid.default_class, np.uint8)
            cur.execute(f"SELECT page_col, colours FROM {grid.table('stencil_pages')} WHERE page_row = %s",
                        (row_off // PAGE,))
            for pc, blob in cur.fetchall():
                ph, pw = page_size(grid, pc, row_off // PAGE)
                rebuilt[:, pc * PAGE:pc * PAGE + pw] = unzip_page(blob, ph, pw)
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
    b = sub.add_parser("build", help="pack the TIFF into compressed pages and upload them")
    b.add_argument("tif", help="the 5-colour TIFF (a path, or a path inside layers/, e.g. jambi/outcome_5class.tif)")
    b.add_argument("--map", help="the map's name (default: the TIFF's file name)")
    c = sub.add_parser("check", help="give a verdict for every farm in a GeoJSON file")
    c.add_argument("plots", help="GeoJSON of farms (a path, or a path inside layers/, e.g. jambi/test_plots.geojson)")
    c.add_argument("--map", help="which stored map to check against (needed when there are several)")
    c.add_argument("--rule", choices=["centre", "touched"], default="centre",
                   help="centre: pixels whose centre is inside an outline (eudr_layers.py's rule, default); "
                        "touched: every pixel that overlaps it (cautious)")
    c.add_argument("--point-mode", choices=["pixel", "circle"], default="pixel",
                   help="pixel: check the pixel under each point (default); circle: a circle of its declared "
                        "area, 4 ha if none, as eudr_layers.py and the EU system do")
    c.add_argument("--csv", default=os.path.join(common.DATABASE_DIR, "results", "results_stencils.csv"),
                   help="where to write the verdicts (default: database/results/results_stencils.csv)")
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
            results, timings = check(conn, grid, farms, all_touched=(args.rule == "touched"))
            protected_areas.apply(results, protected_areas.check(conn, grid, farms))
            common.save_results(conn, grid, farms, results, METHOD)
            common.print_summary(METHOD, results, timings)
            common.write_csv(args.csv, results)
        else:
            common.verify_report("Stencils", verify(conn, common.load_grid(conn, args.map), args.tif))


if __name__ == "__main__":
    main()
