"""
evidence_approach.py - the EVIDENCE PAGES: every EUDR decision fact, for every pixel, in the database.

The compaction step (compaction/eudr_layers.py, Joe's code) packs everything the EUDR decision needs
to know about a pixel into one 32-bit number: forest on 31 Dec 2020, forest type, the year each
source (JRC TMF, Hansen, RADD radar, optical alerts) records a clearing, rubber, water, ... (its
evidence.tif). The stencils and outlines store the rule's answer in 5 colours; these pages store the
evidence itself, so a check gives each plot everything eudr_layers.py gives it: the verdict and its
reason, the hectares of 2020 forest and of each kind of clearing, the years, and the figures the
due diligence statement asks for.

Storage: the map is cut into 256 x 256-pixel pages, as for the stencils. A page holds few distinct
evidence values (a median of 24 on a map made from Jambi's real outcome), so each page is stored as
its palette (the distinct values, 4 bytes each) and one index per pixel (1 byte, or 2 if a page has
more than 256 values), zipped together. Pages that are all the map's most common value get no row.
Nothing is lost: verify rebuilds evidence.tif bit for bit.

Checking plots: one query fetches every page any plot needs. Then each plot gets exactly what
eudr_layers.py does with evidence.tif (its plot_window, plot_mask and judge): the rule runs on the
facts under the plot. So the answers are eudr_layers.py's, plot for plot (tests/test_evidence.py).

Two steps (--two-step): the EUDR's two key questions - is the point in forest that stood on 31 Dec
2020, does the outline overlap it? - come first, from the map's stencils (already stored: no extra
storage). The rule only finds deforestation on 2020 forest, so a plot with none under it is
deforestation-free, and gets eudr_layers.py's own verdict and reason without its evidence pages
being fetched or the rule being run. Only plots on 2020 forest fetch evidence pages. Their answers
are the same as in one step; a screened plot's figures that need the evidence (water, protected
area, Hansen loss on non-forest land, ...) are left empty (SCREEN_UNKNOWN).

Protected areas: Joe's verdict and reason are kept exactly. The outcome (compliant / non-compliant /
needs review, saved in evidence_results.facts and the CSV) also counts protected areas:
deforestation -> non-compliant; deforestation-free but in a protected area -> needs review;
deforestation-free and in none -> compliant. protected_plots finds those plots with the map's
stored protected areas (one query) or Joe's own protected fact; it is kept apart from the rows, so
they still match eudr_layers.py's outputs.

Commands (from the backend folder; a results folder is given by its path or its name in layers/)
    python database/evidence_approach.py build  layers/jambi [--map NAME]
    python database/evidence_approach.py check  layers/jambi/test_plots.geojson [--map NAME]
                                                [--two-step] [--min-plot-ha HA] [--csv FILE]
    python database/evidence_approach.py verify layers/jambi [--map NAME]
(maps.py upload stores a results folder with all three approaches at once.)

Tables (in the map's own schema; defined in schema.sql, sections @evidence, @evidence_keys and
@evidence_results)
    evidence_info     one row: the bit layout, the rule's settings, the most common value, provenance
    evidence_pages    one row per page: (page_col, page_row, evidence)
    evidence_results  one row per plot checked: verdict, reason and every figure (facts, as JSON)
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import time
import zlib
from collections import Counter

import numpy as np
import rasterio
from psycopg.types.json import Jsonb
from pyproj import Transformer
from rasterio.windows import Window
from shapely.geometry import Polygon
from shapely.ops import transform as reproject

import common
import layers
import protected_areas
import stencil_approach
from layers import eudr_layers

METHOD = "evidence"
TABLES = ["evidence_pages", "evidence_info"]
PAGE = common.PAGE
FIRST_COLUMNS = ["plot_id", "outcome", "verdict", "reason", "area_ha", "forest_2020_ha", "confirmed_ha", "probable_ha",
                 "review_ha", "first_deforestation_year"]
# eudr_layers.plot_facts' figures that a plot with no 2020 forest under it can't settle without the
# evidence: they look at other layers (water, protected areas, GFT, Hansen, RADD's watch area) or at the
# 2020 forest bit itself on pixels that are now water or have no data. A screened plot leaves them empty.
# Every other figure of such a plot is 0 (or no year): each needs 2020 forest still standing, damaged or
# cleared, which the 5 colours show as forest, deforestation or needs review (common.FOREST_2020).
SCREEN_UNKNOWN = ("forest_2020_ha", "planted_forest_2020_ha", "radd_watched_ha", "radd_alert_on_2020_forest_ha",
                  "dw_trees_stayed_ha", "dw_replaced_ha", "not_forest_2020_ha", "cleared_not_forest_2020_ha",
                  "water_ha", "recent_alert_on_2020_forest_ha", "hansen_loss_2021_2023_ha",
                  "hansen_loss_2021_2025_ha", "protected_ha")


# --------------------------------------------------------------------------------------
# pages
# --------------------------------------------------------------------------------------
def pack_page(values: np.ndarray) -> bytes:
    """A page of evidence values -> zip(count of distinct values | the values | one index per pixel)."""
    palette, index = np.unique(values, return_inverse=True)
    index = index.reshape(values.shape).astype(np.uint8 if len(palette) <= 256 else "<u2")
    return zlib.compress(np.array([len(palette)], "<u4").tobytes() + palette.astype("<u4").tobytes()
                         + index.tobytes(), 9)


def unpack_page(blob: bytes, height: int, width: int) -> np.ndarray:
    """A stored page -> its evidence values (height x width, uint32)."""
    raw = zlib.decompress(blob)
    count = int.from_bytes(raw[:4], "little")
    palette = np.frombuffer(raw, "<u4", count=count, offset=4)
    index = np.frombuffer(raw, np.uint8 if count <= 256 else "<u2", offset=4 + 4 * count)
    return palette[index.reshape(height, width)].astype(np.uint32)


def page_size(grid: common.Grid, page_col: int, page_row: int) -> tuple[int, int]:
    """(height, width) in pixels - pages on the right and bottom edges can be smaller."""
    return min(PAGE, grid.height - page_row * PAGE), min(PAGE, grid.width - page_col * PAGE)


def window(pages: dict, default: int, r0: int, r1: int, c0: int, c1: int) -> np.ndarray:
    """Rows r0:r1, columns c0:c1 of the map from unpacked pages {(page_col, page_row): values};
    a page with no row is all the default value."""
    out = np.full((r1 - r0, c1 - c0), default, np.uint32)
    for pr in range(r0 // PAGE, (r1 - 1) // PAGE + 1):
        for pc in range(c0 // PAGE, (c1 - 1) // PAGE + 1):
            page = pages.get((pc, pr))
            if page is None:
                continue
            top, left = pr * PAGE, pc * PAGE
            rows = slice(max(r0, top), min(r1, top + page.shape[0]))
            cols = slice(max(c0, left), min(c1, left + page.shape[1]))
            out[rows.start - r0:rows.stop - r0, cols.start - c0:cols.stop - c0] = \
                page[rows.start - top:rows.stop - top, cols.start - left:cols.stop - left]
    return out


def ensure_built(conn, grid: common.Grid) -> None:
    if not common.has_tables(conn, grid, "evidence_info", "evidence_pages"):
        raise SystemExit(
            f"The map '{grid.map_version}' has no evidence pages: it was uploaded from a TIFF, not from "
            "a results folder of the compaction step. Upload the results folder as a map:\n"
            "  python database/maps.py upload layers/NAME --name NAME\n"
            "or build the map from your satellite layers:\n"
            "  python pipeline.py --name NAME --data-dir YOUR_LAYERS --bbox XMIN YMIN XMAX YMAX")


def fingerprint_of(path: str, grid: common.Grid) -> str:
    """evidence.tif's fingerprint, as build stores it in evidence_info (all 32 bits, strip by strip):
    maps.upload uses it to find evidence pages Neon already holds."""
    fingerprint = common.Fingerprint(grid)
    with rasterio.open(path) as src:
        for row_off in range(0, src.height, PAGE):
            fingerprint.add(src.read(1, window=Window(0, row_off, src.width, min(PAGE, src.height - row_off))))
    return fingerprint.hexdigest()


_INFO: dict = {}  # (schema, map fingerprint) -> load_info's answer: it never changes for a stored map


def load_info(conn, grid: common.Grid, cached: bool = True) -> dict:
    """The evidence's settings: most common value, the rule (from its parameters) and fingerprint.
    Read once per map (cached=True), so later checks need no round trip for it."""
    key = (grid.schema, grid.fingerprint)
    if cached and key in _INFO:
        return _INFO[key]
    with conn.cursor() as cur:
        cur.execute(f"SELECT default_value, rule_params, bit_layout, fingerprint FROM {grid.table('evidence_info')}")
        row = cur.fetchone()
    if row is None:
        raise SystemExit(f"The map '{grid.map_version}' has evidence pages but no evidence_info. Build them "
                         f"again: python database/evidence_approach.py build layers/NAME --map \"{grid.map_version}\"")
    default, params, layout, fingerprint = row
    layers.check_layout(layout)
    _INFO[key] = {"default_value": int(default), "rule_params": params, "rule": layers.rule_from(params),
                  "fingerprint": fingerprint}
    return _INFO[key]


# --------------------------------------------------------------------------------------
# build
# --------------------------------------------------------------------------------------
def build(conn, results: str, map_name: str, report=None, tidy: bool = True) -> common.Grid:
    """Store a results folder's evidence.tif as the evidence pages of the map map_name, which must
    hold the same results folder's 5 colours (maps.upload does both in one transaction).
    report(label, done, total) follows the progress. tidy=False leaves the transaction open and
    skips VACUUM."""
    start = time.perf_counter()
    grid = common.load_grid(conn, map_name)
    _INFO.pop((grid.schema, grid.fingerprint), None)  # new evidence: read its settings again
    folder = layers.results_folder(results)
    path = layers.evidence_file(folder)
    with rasterio.open(path) as src:
        common.same_grid_or_exit(src, grid)
    with rasterio.open(layers.class_map(folder)) as src:
        colours = common.scan_map(src, grid)[1]
    if colours != grid.fingerprint:
        raise SystemExit(f"This results folder is not the one the map '{grid.map_version}' was uploaded "
                         "from (its 5 colours differ). Upload it as a map of its own:\n"
                         f"  python database/maps.py upload \"{common.nice_path(folder)}\" --name NAME")

    pages, counts, fingerprint = [], Counter(), common.Fingerprint(grid)
    with rasterio.open(path) as src:
        tags = src.tags()
        layout = json.loads(tags.get("BIT_LAYOUT", "[]"))
        layers.check_layout(layout)
        params = json.loads(tags.get("RULE_PARAMS", "{}"))
        progress = common.Progress(math.ceil(src.height / PAGE), "packing the evidence", report)
        for row_off in range(0, src.height, PAGE):
            strip = src.read(1, window=Window(0, row_off, src.width, min(PAGE, src.height - row_off)))
            fingerprint.add(strip)
            values, n = np.unique(strip, return_counts=True)
            counts.update(dict(zip(values.tolist(), n.tolist())))
            for col_off in range(0, src.width, PAGE):
                page = strip[:, col_off:col_off + PAGE]
                first = int(page[0, 0])
                if (page == first).all():       # one value: packed later, unless it is the default
                    pages.append((col_off // PAGE, row_off // PAGE, first, page.shape))
                else:
                    pages.append((col_off // PAGE, row_off // PAGE, None, pack_page(page)))
            progress.step()
    top = max(counts.values())
    default = min(v for v, n in counts.items() if n == top)
    rows = [(pc, pr, blob) if value is None else (pc, pr, pack_page(np.full(blob, value, np.uint32)))
            for pc, pr, value, blob in pages if value != default]

    info, table = grid.table("evidence_info"), grid.table("evidence_pages")
    with conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {table}, {info}")
        cur.execute(common.schema_sql("evidence", grid))    # schema.sql: evidence_info, evidence_pages
        cur.execute(f"INSERT INTO {info} (producer, bit_layout, rule_params, default_value, fingerprint, "
                    "source_file, tags) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (tags.get("PRODUCER"), Jsonb(layout), Jsonb(params), default, fingerprint.hexdigest(),
                     common.stored_path(path), Jsonb(tags)))
    for i in range(0, len(rows), 500):
        common.copy_rows(conn, table, ["page_col", "page_row", "evidence"], rows[i:i + 500])

    print("  building the evidence index...")
    if report is not None:
        report("building the evidence index", 0, 1)
    with conn.cursor() as cur:
        cur.execute(common.schema_sql("evidence_keys", grid))
    if report is not None:
        report("building the evidence index", 1, 1)  # last report before the commit: Cancel undoes it all
    if tidy:
        common.tidy_up(conn, table)
        print(f"Built in {time.perf_counter() - start:.1f} s")
        common.print_storage(conn, [grid.table(t) for t in TABLES])
    return grid


# --------------------------------------------------------------------------------------
# check
# --------------------------------------------------------------------------------------
def read_plots(path: str):
    """(plots, features): the plots as eudr_layers.py reads them (WGS84; a point becomes a circle of
    its declared area, 4 ha if none is given, as the EU system assumes), with the same ids the
    stencils and outlines give them (common.plot_ids); and the GeoJSON features themselves."""
    features = common.read_features(path)
    ids = common.plot_ids(features)
    slim = {"type": "FeatureCollection", "features": [  # only what eudr_layers.read_plots reads
        {"type": "Feature", "properties": {"Area": common.declared_area(f.get("properties") or {})},
         "geometry": f.get("geometry")} for f in features]}
    plots = eudr_layers.read_plots(io.BytesIO(json.dumps(slim).encode("utf-8")))
    plots["plot_id"] = ids
    return plots, features


def pages_of(span) -> set:
    """The (page_col, page_row) pages under rows r0:r1, columns c0:c1."""
    r0, r1, c0, c1 = span
    return {(pc, pr) for pr in range(r0 // PAGE, (r1 - 1) // PAGE + 1) for pc in range(c0 // PAGE, (c1 - 1) // PAGE + 1)}


def fetch_pages(conn, grid: common.Grid, table: str, column: str, needed: set, unpack) -> dict:
    """{(page_col, page_row): unpack(blob, height, width)} for the needed pages that have a row,
    in one query."""
    pages = {}
    if needed:
        page_cols, page_rows = zip(*sorted(needed))
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT s.page_col, s.page_row, s.{column}
                FROM unnest(%s::int[], %s::int[]) AS q(pc, pr)
                JOIN {grid.table(table)} s ON s.page_col = q.pc AND s.page_row = q.pr
                """, (list(page_cols), list(page_rows)))
            for pc, pr, blob in cur.fetchall():
                pages[(pc, pr)] = unpack(blob, *page_size(grid, pc, pr))
    return pages


def screened_facts(mask: np.ndarray, row_area_w, rule, min_ha: float) -> tuple[dict, str, str]:
    """(facts, verdict, reason) for a plot with no 2020 forest under it, from eudr_layers.py itself:
    its figures for a window with no forest and no clearing, then SCREEN_UNKNOWN left empty. The
    verdict and reason are what the full rule gives such a plot (deforestation-free)."""
    nothing = np.zeros(mask.shape, eudr_layers.PACKED_DTYPE)
    facts = eudr_layers.plot_facts(eudr_layers.unpack(nothing), np.zeros(mask.shape, np.uint8), mask, row_area_w,
                                   rule[2] if len(rule) > 2 else None)
    for key in SCREEN_UNKNOWN:
        facts[key] = None
    verdict, reason = eudr_layers.plot_verdict(facts, min_ha)
    return facts, verdict, reason


def check(conn, grid: common.Grid, plots, min_ha: float = 0.0, two_step: bool = False):
    """Every plot's facts, verdict and reason, as eudr_layers.py gives them from evidence.tif.
    plots: a GeoDataFrame with plot_id (see read_plots). Returns (rows in plot order, timings).
    Round trips: one, for every page needed (the tables come with load_grid, and evidence_info is
    read once per map: the first check pays one more).
    two_step: first see which plots touch 2020 forest, from the stencils (one more round trip);
    only those fetch evidence pages (see the top of this file). Each row then has in_forest_2020."""
    ensure_built(conn, grid)
    t0 = time.perf_counter()
    info = load_info(conn, grid)
    two_step = two_step and common.has_tables(conn, grid, "stencil_pages")
    jgrid = layers.joe_grid(grid)
    row_area = eudr_layers.row_areas_m2(jgrid)
    items = list(zip(plots["plot_id"], plots.geometry))
    windows, masks = [], []
    for _, geom in items:
        span = None if geom is None or geom.is_empty else eudr_layers.plot_window(geom, jgrid)
        if span is not None and (span[1] <= span[0] or span[3] <= span[2]):
            span = ()                                   # outside the map (as eudr_layers)
        windows.append(span)
        masks.append(eudr_layers.plot_mask(geom, eudr_layers.window_grid(jgrid, *span)) if span else None)

    # step 1 (two_step only): which plots have 2020 forest under them, from the 5 colours
    in_forest = [True] * len(items)
    stencil_pages = {}
    if two_step:
        stencil_pages = stencil_approach.fetch_pages(conn, grid, set().union(*[pages_of(s) for s in windows if s]))
        for i, (span, made) in enumerate(zip(windows, masks)):
            if span:
                r0, r1, c0, c1 = span
                colours = stencil_approach.window_colours(stencil_pages, grid, r0, c0, r1 - r0, c1 - c0)
                in_forest[i] = bool(np.isin(colours[made[0]], common.FOREST_2020).any())

    # step 2: the evidence under the plots that need it
    needed = set().union(*[pages_of(s) for s, f in zip(windows, in_forest) if s and f])
    pages = fetch_pages(conn, grid, "evidence_pages", "evidence", needed, unpack_page)

    rows = []
    for (pid, geom), span, made, forest in zip(items, windows, masks, in_forest):
        note = ""
        if not span:
            facts, verdict, reason = eudr_layers.judge(
                eudr_layers.unpack(np.zeros((1, 1), eudr_layers.PACKED_DTYPE)), np.zeros((1, 1), bool),
                row_area[:1], info["rule"], min_ha)
            note = "no geometry" if span is None else ""
            forest = False
        else:
            r0, r1, c0, c1 = span
            mask, note = made
            if forest:
                values = window(pages, info["default_value"], r0, r1, c0, c1)
                facts, verdict, reason = eudr_layers.judge(eudr_layers.unpack(values), mask, row_area[r0:r1],
                                                           info["rule"], min_ha)
            else:
                facts, verdict, reason = screened_facts(mask, row_area[r0:r1], info["rule"], min_ha)
        row = {"plot_id": pid, **facts, "verdict": verdict, "reason": reason, "note": note}
        if two_step:
            row["in_forest_2020"] = forest
        rows.append(row)
    return rows, {"total_s": time.perf_counter() - t0, "pages": len(pages), "two_step": two_step,
                  "stencil_pages": len(stencil_pages), "screened": sum(1 for f in in_forest if not f)}


# --------------------------------------------------------------------------------------
# protected areas: the outcome (not Joe's verdict) of a plot in one
# --------------------------------------------------------------------------------------
FROM_EVIDENCE = "a protected area (the evidence's protected fact)"


def protected_plots(conn, grid: common.Grid, plots, rows: list[dict]) -> list[tuple]:
    """For each plot, the protected areas it lies in (sorted names; () if none): the map's stored
    protected areas (protected_areas.check, one query, as the stencils and outlines use), or else
    Joe's own protected fact (protected_ha > 0, for a map built with --protected). Kept apart from
    the rows, which stay exactly eudr_layers.py's outputs."""
    to_map = None
    if grid.srid != 4326:   # the plots are in longitude/latitude (read_plots)
        to_map = Transformer.from_crs(4326, grid.srid, always_xy=True).transform
    farms = []
    for pid, geom in zip(plots["plot_id"], plots.geometry):
        if geom is None or geom.is_empty:
            geom = Polygon()
        elif to_map:
            geom = reproject(to_map, geom)
        farms.append(common.Farm(str(pid), {}, geom, "outline"))
    found = protected_areas.check(conn, grid, farms)
    names = []
    for i, row in enumerate(rows):
        ha = row.get("protected_ha")
        if i in found:
            names.append(found[i].names)
        elif isinstance(ha, (int, float)) and ha > 0:
            names.append((FROM_EVIDENCE,))
        else:
            names.append(())
    return names


def outcome(row: dict, protected: tuple = ()) -> str:
    """A plot's outcome: deforestation -> non-compliant; deforestation-free -> compliant, unless the
    plot is in a protected area, which needs review (is farming allowed there?); every other verdict
    of Joe's needs review. His verdict and reason stay as he gives them."""
    result = common.outcome_of(row["verdict"])
    return common.NEEDS_REVIEW if result == common.COMPLIANT and protected else result


def save_results(conn, grid: common.Grid, features: list[dict], rows: list[dict],
                 protected: list[tuple] | None = None) -> None:
    """The plots (as sent) and their verdicts, reasons and figures, in the map's own schema.
    protected: protected_plots' answer (the outcome then counts protected areas)."""
    protected = protected or [()] * len(rows)
    results = grid.table("evidence_results")
    with conn.cursor() as cur:
        cur.execute(common.map_tables_sql(grid))
        cur.execute(common.schema_sql("evidence_results", grid))   # schema.sql: made on the first save
        cur.executemany(
            f"INSERT INTO {grid.table('plots')} (plot_id, supplier_id, declared_ha, geojson) VALUES (%s,%s,%s,%s) "
            "ON CONFLICT (plot_id) DO UPDATE SET supplier_id = EXCLUDED.supplier_id, "
            "declared_ha = EXCLUDED.declared_ha, geojson = EXCLUDED.geojson",
            [(r["plot_id"], (f.get("properties") or {}).get("supplier_id"),
              common.declared_area(f.get("properties") or {}), Jsonb(f)) for f, r in zip(features, rows)])
        cur.executemany(
            f"INSERT INTO {results} (plot_id, verdict, reason, facts) VALUES (%s,%s,%s,%s) "
            "ON CONFLICT (plot_id) DO UPDATE SET verdict = EXCLUDED.verdict, reason = EXCLUDED.reason, "
            "facts = EXCLUDED.facts, checked_at = now()",
            [(r["plot_id"], r["verdict"], r["reason"],
              Jsonb({"outcome": outcome(r, p), "protected_areas": list(p),
                     **{k: v for k, v in r.items() if k not in ("plot_id", "verdict", "reason")}}))
             for r, p in zip(rows, protected)])
    conn.commit()


def write_csv(path: str, rows: list[dict], protected: list[tuple] | None = None) -> None:
    common.make_parent(path)
    protected = protected or [()] * len(rows)
    rows = [{**r, "outcome": outcome(r, p), "protected_areas": "; ".join(p)} for r, p in zip(rows, protected)]
    columns = FIRST_COLUMNS + [c for c in rows[0] if c not in FIRST_COLUMNS] if rows else FIRST_COLUMNS
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, columns)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {common.nice_path(path)}")


def print_summary(rows: list[dict], timings: dict, protected: list[tuple] | None = None) -> None:
    n = len(rows)
    total = timings["total_s"]
    print(f"\n{METHOD}: checked {n} plots in {total:.3f} s ({1000 * total / max(n, 1):.2f} ms per plot), "
          f"{timings['pages']} evidence pages fetched")
    if timings.get("two_step"):
        print(f"  Two steps: {n - timings['screened']} plots touch forest of 31 Dec 2020 and were judged on "
              f"their evidence; {timings['screened']} touch none, so they were settled from the stencils alone "
              f"({timings['stencil_pages']} stencil pages fetched)")
    protected = protected or [()] * len(rows)
    outcomes = Counter(outcome(r, p) for r, p in zip(rows, protected))
    for name in common.OUTCOMES:
        print(f"  {name:24s} {outcomes.get(name, 0)}")
    moved = sum(1 for r, p in zip(rows, protected) if p and common.outcome_of(r["verdict"]) == common.COMPLIANT)
    if moved:
        print(f"  ({moved} deforestation-free plot{'' if moved == 1 else 's'} in a protected area, so needing review)")
    print("  why: " + ", ".join(f"{v} {c}" for v, c in Counter(r["verdict"] for r in rows).most_common()))


# --------------------------------------------------------------------------------------
# verify: rebuild the whole evidence from the pages and compare it with evidence.tif
# --------------------------------------------------------------------------------------
def verify(conn, grid: common.Grid, results: str) -> dict:
    ensure_built(conn, grid)
    info = load_info(conn, grid)
    path = layers.evidence_file(layers.results_folder(results))
    mismatched = total = 0
    with rasterio.open(path) as src, conn.cursor() as cur:
        fingerprint = common.Fingerprint(common.same_grid_or_exit(src, grid))
        progress = common.Progress(math.ceil(src.height / PAGE), "rebuilding the evidence")
        for row_off in range(0, src.height, PAGE):
            h = min(PAGE, src.height - row_off)
            expected = src.read(1, window=Window(0, row_off, src.width, h))
            fingerprint.add(expected)
            rebuilt = np.full((h, src.width), info["default_value"], np.uint32)
            cur.execute(f"SELECT page_col, evidence FROM {grid.table('evidence_pages')} WHERE page_row = %s",
                        (row_off // PAGE,))
            for pc, blob in cur.fetchall():
                ph, pw = page_size(grid, pc, row_off // PAGE)
                rebuilt[:, pc * PAGE:pc * PAGE + pw] = unpack_page(blob, ph, pw)
            mismatched += int(np.count_nonzero(rebuilt != expected))
            total += h * src.width
            progress.step()
    return {"pixels": total, "mismatched": mismatched, "fidelity_pct": 100.0 * (1 - mismatched / total),
            "same_map": fingerprint.hexdigest() == info["fingerprint"]}


# --------------------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="store a results folder's evidence.tif as evidence pages")
    b.add_argument("results", help="the results folder (its path, or its name in layers/)")
    b.add_argument("--map", help="the map it belongs to (needed when there are several)")
    c = sub.add_parser("check", help="every plot's facts, verdict and reason, from the database")
    c.add_argument("plots", help="GeoJSON of plots (a path, or a path inside layers/)")
    c.add_argument("--map", help="which stored map to check against (needed when there are several)")
    c.add_argument("--min-plot-ha", type=float, default=0.0,
                   help="ignore clearing of at most this many ha per plot (the EUDR has no tolerance; default 0)")
    c.add_argument("--two-step", action="store_true",
                   help="first ask the stencils which plots touch forest of 31 Dec 2020; only those fetch "
                        "evidence pages (same verdicts; a plot with none leaves some figures empty)")
    c.add_argument("--csv", default=os.path.join(common.DATABASE_DIR, "results", "results_evidence.csv"),
                   help="where to write the results (default: database/results/results_evidence.csv)")
    v = sub.add_parser("verify", help="rebuild the evidence from the database and compare it with evidence.tif")
    v.add_argument("results", help="the results folder (its path, or its name in layers/)")
    v.add_argument("--map", help="which stored map to rebuild (needed when there are several)")
    args = ap.parse_args()
    if args.cmd == "check":
        args.plots = common.find_geojson(args.plots)

    with common.connect() as conn:
        if args.cmd == "build":
            build(conn, args.results, common.find_map(conn, args.map)[0])
        elif args.cmd == "check":
            grid = common.load_grid(conn, args.map)
            plots, features = read_plots(args.plots)
            rows, timings = check(conn, grid, plots, args.min_plot_ha, two_step=args.two_step)
            protected = protected_plots(conn, grid, plots, rows)
            save_results(conn, grid, features, rows, protected)
            print_summary(rows, timings, protected)
            write_csv(args.csv, rows, protected)
        else:
            stats = verify(conn, common.load_grid(conn, args.map), args.results)
            print(f"Evidence: rebuilt {stats['pixels']:,} pixels from the database - {stats['mismatched']:,} "
                  f"differ from evidence.tif (all 32 bits) -> pixel fidelity {common.fidelity_text(stats)}")
            if not stats["same_map"]:
                print("  Warning: this evidence.tif is not the one that was uploaded.")


if __name__ == "__main__":
    main()
