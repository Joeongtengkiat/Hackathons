"""
compare.py - the evaluation: what Neon answers vs the original data, on the same plots.

Part 1, the 5 colours: stencils and outlines vs the TIFF they were built from.
  Question 1  "Is the point in forest of 31 Dec 2020?"          - every point farm
  Question 2  "Does the outline overlap forest of 31 Dec 2020?" - every outline farm
  (Forest on the EUDR cut-off date: forest, deforestation and needs review in the 5 colours,
  common.FOREST_2020. Forest still standing today is reported too.)
  For each one, every method reports
    * decision fidelity - % of farms whose answers match the TIFF exactly (in 2020 forest? /
                          overlaps it?, every colour touched, the EUDR verdict)
    * query speed       - all farms in one go, per farm, and one farm checked on its own
  Each approach is compared with the TIFF using the SAME rule, so only the storage differs:
    stencils  <->  TIFF counted in pixels   (--rule touched|centre)
    polygons  <->  TIFF measured by exact overlap area
  (For a point both rules are the same: the colour of the pixel it lands on.)
  Then pixel fidelity: the whole map rebuilt from the database, compared pixel by pixel.

Part 2, the evidence (only for a map uploaded from a results folder of the compaction step):
    * decision fidelity - every output eudr_layers.py gives a plot (verdict, reason, every hectare
                          figure, the years), from Neon vs from evidence.tif; and, from the
                          compaction step's own check (--verify), evidence.tif vs the original layers
    * query speed       - Neon vs evidence.tif on disk (and the original layers, from that check)
    * pixel fidelity    - the evidence rebuilt from Neon vs evidence.tif, all 32 bits; and what
                          evidence.tif keeps of each original layer
    * storage           - from the original layers, through evidence.tif, to each approach in Neon

Run from the backend folder, after the map is uploaded:
    python compare/compare.py layers/jambi --map jambi                    # a results folder: its test plots
    python compare/compare.py layers/jambi farms.geojson --map jambi      # other plots
    python compare/compare.py my_map.tif farms.geojson --map riau         # a map uploaded from a 5-colour TIFF
    python compare/compare.py layers/jambi --point 103.6236 -1.7794       # one point: longitude latitude
A results folder is given by its path or its name in layers/. With several maps stored, add --map NAME.
The per-farm tables go to compare/results/comparison.csv (and comparison_evidence.csv).
"""

import argparse
import csv
import json
import os
import statistics
import sys

import numpy as np
import rasterio
from pyproj import Transformer

# The approaches live in the database/ folder next to this one - make them importable.
COMPARE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(COMPARE_DIR), "database"))

import common  # noqa: E402
import evidence_approach  # noqa: E402
import layers  # noqa: E402
import polygon_approach  # noqa: E402
import protected_areas  # noqa: E402
import stencil_approach  # noqa: E402
from layers import eudr_layers  # noqa: E402

YES_NO = {True: "yes", False: "no"}


def run_batch(check, farms: list, repeat: int):
    """Check all the farms in one go, `repeat` times.
    Returns (results of the last run, median seconds, timings of the last run)."""
    runs = [check(farms) for _ in range(repeat)]
    return runs[-1][0], statistics.median(r[1]["total_s"] for r in runs), runs[-1][1]


def run_one_by_one(check, farms: list, sample: int) -> float:
    """Median seconds to check ONE farm on its own - what a single upload would wait for."""
    picks = farms[::max(1, len(farms) // sample)][:sample]
    check(picks[:1])  # warm-up
    return statistics.median(check([farm])[1]["total_s"] for farm in picks)


def share(approach: list | None, reference: list, test) -> str:
    """The share of farms where approach gives the same answer as reference ('-': not stored)."""
    if approach is None:
        return f"{'-':>10s}"
    same = sum(test(a) == test(r) for a, r in zip(approach, reference))
    return f"{100 * same / len(reference):9.2f}%"


def print_mismatches(name: str, approach: list | None, reference: list) -> None:
    if approach is None:
        return
    bad = [(a, r) for a, r in zip(approach, reference) if a.key() != r.key()]
    for a, r in bad[:10]:
        print(f"    mismatch ({name}) {a.plot_id}: {a.verdict} [{a.colours_text()}] "
              f"vs TIFF {r.verdict} [{r.colours_text()}]")
    if len(bad) > 10:
        print(f"    ... and {len(bad) - 10} more (see the CSV)")


def map_bounds_text(grid: common.Grid) -> str:
    """The map's extent in longitude/latitude, to help when a test point falls outside it."""
    xs, ys = grid.bounds_box().exterior.xy
    if grid.srid != 4326:
        xs, ys = Transformer.from_crs(grid.srid, 4326, always_xy=True).transform(np.array(xs), np.array(ys))
    return f"longitude {min(xs):.4f} to {max(xs):.4f}, latitude {min(ys):.4f} to {max(ys):.4f}"


# --------------------------------------------------------------------------------------
# --point: test single points typed on the command line
# --------------------------------------------------------------------------------------
def test_points(points, grid, methods, repeat: int) -> None:
    print("\n=== Your points: \"is the point in forest of 31 Dec 2020?\" ===")
    farms = common.farms_from_points(points, grid)
    for farm in farms:
        print(f"  {farm.plot_id[len('point '):]}  (longitude, latitude)")
        reference = None
        for name, check in methods:
            check([farm])  # warm-up
            runs = [check([farm]) for _ in range(repeat)]
            result = runs[-1][0][0]
            ms = 1000 * statistics.median(r[1]["total_s"] for r in runs)
            if result.outside:
                answer = f"{'-':14s} {'outside the map':18s}"
            else:
                answer = f"{common.CLASS_NAMES[result.point_colour]:14s} 2020 forest: {YES_NO[result.in_forest_2020]:4s}"
            note = ""
            if reference is None:
                reference = result
            else:
                note = "same as the TIFF" if result.key() == reference.key() else "DIFFERENT from the TIFF"
            print(f"    {name:14s} {answer} {result.verdict:15s} {ms:8.2f} ms   {note}".rstrip())
        if reference.outside:
            print(f"    (the map covers {map_bounds_text(grid)})")


# --------------------------------------------------------------------------------------
# a GeoJSON: question 1 on its points, question 2 on its outlines
# --------------------------------------------------------------------------------------
def speed_table(rows: list, n: int, what: str) -> None:
    print(f"\n  {'Speed':22s}{f'all {n} at once':>16s}{'per ' + what:>14s}{what + ' alone':>18s}")
    for name, seconds, single in rows:
        print(f"    {name:20s}{seconds:14.3f} s{1000 * seconds / n:11.2f} ms{1000 * single:15.2f} ms")


def label_line(timings: dict, n: int, what: str) -> None:
    if timings.get("used_labels"):
        by_label = timings["by_label"]
        print(f"  (polygons answered {by_label} of {n} {what} ({100 * by_label / n:.0f}%) from the square "
              f"labels alone - no geometry needed)")
    else:
        print(f"  (polygons skip the square labels for fewer than {polygon_approach.LABELS_FROM} farms "
              f"at once - it saves a round trip)")


def question_1(farms, idx, methods, args) -> dict:
    """Point farms: the colour under the point, so: is the point in forest of 31 Dec 2020?"""
    group = [farms[i] for i in idx]
    n = len(group)
    print(f"\n=== Question 1: \"Is the point in forest of 31 Dec 2020?\" - {n} point farm{'' if n == 1 else 's'} ===")
    out, rows = {}, []
    for key, name, check in methods:
        if key == "tiff_ex":  # for a point, exact area and the pixel rule are the same thing
            continue
        results, seconds, timings = run_batch(check, group, args.repeat)
        rows.append((name, seconds, run_one_by_one(check, group, args.single)))
        out[key] = (results, timings)
    out["tiff_ex"] = out["tiff_px"]
    tiff, sten = out["tiff_px"][0], out["stencils"][0]
    poly = out["polygons"][0] if "polygons" in out else None   # None: the map has no outlines
    in_forest = sum(r.in_forest_2020 for r in tiff)
    standing = sum(r.touches_forest for r in tiff)
    outside = sum(r.outside for r in tiff)
    answers = (f"  Answers from the original TIFF: {in_forest} in 2020 forest ({standing} of them still standing), "
               f"{n - in_forest - outside} not")
    print(answers + (f", {outside} outside the map" if outside else ""))
    print(f"\n  {'Same answer as the TIFF':38s}{'stencils':>10s}{'polygons':>10s}")
    for label, test in (("In 2020 forest? (yes/no)", lambda r: r.in_forest_2020),
                        ("In forest standing today? (yes/no)", lambda r: r.touches_forest),
                        ("Colour under the point", lambda r: r.point_colour),
                        ("EUDR verdict", lambda r: r.verdict),
                        ("Everything (decision fidelity)", lambda r: r.key())):
        print(f"    {label:36s}{share(sten, tiff, test)}{share(poly, tiff, test)}")
    print_mismatches("stencils", sten, tiff)
    print_mismatches("polygons", poly, tiff)
    speed_table(rows, n, "point")
    if poly is not None:
        label_line(out["polygons"][1], n, "points")
    return out


def question_2(farms, idx, methods, args) -> dict:
    """Outline farms: every colour inside the outline, so: does it overlap forest of 31 Dec 2020?"""
    group = [farms[i] for i in idx]
    n = len(group)
    print(f"\n=== Question 2: \"Does the outline overlap forest of 31 Dec 2020?\" - {n} outline farm{'' if n == 1 else 's'} ===")
    out, rows = {}, []
    for key, name, check in methods:
        results, seconds, timings = run_batch(check, group, args.repeat)
        rows.append((name, seconds, run_one_by_one(check, group, args.single)))
        out[key] = (results, timings)
    tiff_px, tiff_ex = out["tiff_px"][0], out["tiff_ex"][0]
    sten = out["stencils"][0]
    poly = out["polygons"][0] if "polygons" in out else None   # None: the map has no outlines
    exact = sum(r.in_forest_2020 for r in tiff_ex)
    pixel = sum(r.in_forest_2020 for r in tiff_px)
    print(f"  Answers from the original TIFF: {exact} of {n} overlap 2020 forest (exact area)"
          + ("" if pixel == exact else f", {pixel} with the pixel rule"))
    print(f"\n  {'Same answer as the TIFF':38s}{'stencils':>10s}{'polygons':>10s}")
    for label, test in (("Overlaps 2020 forest? (yes/no)", lambda r: r.in_forest_2020),
                        ("Overlaps forest standing today?", lambda r: r.touches_forest),
                        ("Every colour touched", lambda r: r.present),
                        ("EUDR verdict", lambda r: r.verdict),
                        ("Everything (decision fidelity)", lambda r: r.key())):
        print(f"    {label:36s}{share(sten, tiff_px, test)}{share(poly, tiff_ex, test)}")
    print_mismatches("stencils", sten, tiff_px)
    print_mismatches("polygons", poly, tiff_ex)
    rule_diff = sum(a.key() != b.key() for a, b in zip(tiff_px, tiff_ex))
    print(f"  (stencils are compared with the TIFF using the pixel rule, polygons using exact areas;\n"
          f"   those two rules themselves give different answers for {rule_diff} outline(s) - a policy\n"
          f"   choice about the rule, not a storage error)")
    speed_table(rows, n, "outline")
    if poly is not None:
        label_line(out["polygons"][1], n, "outlines")
    return out


def test_geojson(conn, grid, args, methods, tif_info, show_storage: bool = True) -> None:
    farms = common.load_farms(args.plots, grid, args.point_mode)
    n = len(farms)
    if not n:
        raise SystemExit("No farms found in that GeoJSON.")
    point_idx = [i for i, f in enumerate(farms) if f.kind == "point"]
    outline_idx = [i for i, f in enumerate(farms) if f.kind == "outline"]
    print(f"Each method checks the farms {args.repeat} times and the median is reported; "
          f"'alone' = up to {args.single} farms per question, each checked on its own.")

    # Put each method's answers for both groups back into one list, in the file's order.
    merged = {key: [None] * n for key, _, _ in methods}
    for idx, question, skipped in (
            (point_idx, question_1, "No point farms to test, so question 1 is skipped"
             + (" (--point-mode circle turned them into outlines)." if args.point_mode == "circle" else ".")
             + " Try --point LON LAT."),
            (outline_idx, question_2, "No outline farms in this file, so question 2 is skipped.")):
        if not idx:
            print(f"\n({skipped})")
            continue
        for key, (results, _) in question(farms, idx, methods, args).items():
            for i, res in zip(idx, results):
                merged[key][i] = res
    tiff_px, tiff_ex = merged["tiff_px"], merged["tiff_ex"]
    sten, poly = merged["stencils"], merged.get("polygons")   # poly None: the map has no outlines
    modules = [("stencils", stencil_approach)] + ([("polygons", polygon_approach)] if poly else [])

    print(f"\n=== All {n} farms: decision fidelity (every answer matches the TIFF) ===")
    print(f"    stencils {share(sten, tiff_px, lambda r: r.key()).strip()}    "
          f"polygons {share(poly, tiff_ex, lambda r: r.key()).strip()}")
    # Protected areas send farms for review (protected_areas.apply). The same step for every method,
    # after the fidelity figures above, which compare the stored maps only.
    found = protected_areas.check(conn, grid, farms)
    for results in (tiff_px, tiff_ex, sten, poly):
        if results is not None:
            protected_areas.apply(results, found)
    if found:
        print(f"\n{len(found)} farm{'' if len(found) == 1 else 's'} lie in a protected area, so they need review "
              "(saved verdicts include this).")
    for results, method in ((tiff_px, "tiff-pixel"), (tiff_ex, "tiff-exact"),
                            (sten, stencil_approach.METHOD), (poly, polygon_approach.METHOD)):
        if results is not None:
            common.save_results(conn, grid, farms, results, method)

    if not args.skip_verify:
        print("\n=== Pixel fidelity (rebuild the whole map from the database) ===")
        for name, module in modules:
            stats = module.verify(conn, grid, args.tif)
            print(f"  {name:9s} {stats['mismatched']:,} of {stats['pixels']:,} pixels differ  "
                  f"-> {common.fidelity_text(stats)} (all colours, including land and forest)")

    if show_storage:
        print("\n=== Storage ===")
        file_bytes, raw_bytes = tif_info
        print(f"  original TIFF file (compressed)     {file_bytes / 1e6:8.3f} MB")
        print(f"  original TIFF, uncompressed pixels  {raw_bytes / 1e6:8.3f} MB")
        shared_bytes = sum(s for s, _ in common.table_sizes(conn, [grid.table("grid_info")]).values())
        for name, module in modules:
            sizes = common.table_sizes(conn, [grid.table(t) for t in module.TABLES])
            total = sum(s for s, _ in sizes.values()) + shared_bytes
            detail = ", ".join(f"{t.rsplit('.', 1)[-1]}: {r:,} rows" for t, (s, r) in sizes.items())
            print(f"  {name:9s} database                  {total / 1e6:8.3f} MB   ({detail})")
        print("  (Database sizes include indexes and PostgreSQL's per-row bookkeeping.)")

    common.make_parent(args.csv)
    with open(args.csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["plot_id", "kind",
                    "tiff_pixel_verdict", "stencils_verdict", "stencils_match",
                    "tiff_exact_verdict", "polygons_verdict", "polygons_match",
                    "point_colour_tiff", "point_colour_stencils", "point_colour_polygons",
                    "forest_tiff_pixel", "forest_stencils", "forest_tiff_exact", "forest_polygons",
                    "colours_tiff_pixel", "colours_stencils", "colours_tiff_exact", "colours_polygons",
                    "forest_ha_tiff_exact", "forest_ha_polygons", "note"])
        for tp, st, te, po in zip(tiff_px, sten, tiff_ex, poly or [None] * n):
            w.writerow([tp.plot_id, tp.kind,
                        tp.verdict, st.verdict, st.key() == tp.key(),
                        te.verdict, po.verdict if po else "", po.key() == te.key() if po else "",
                        *(common.CLASS_NAMES.get(r.point_colour, "") if r else "" for r in (tp, st, po)),
                        *(YES_NO[r.touches_forest] if r else "" for r in (tp, st, te, po)),
                        tp.colours_text(), st.colours_text(), te.colours_text(), po.colours_text() if po else "",
                        f"{te.ha(common.FOREST):.4f}", f"{po.ha(common.FOREST):.4f}" if po else "", tp.note])
    print(f"\nWrote {common.nice_path(args.csv)} (one row per farm)")


# --------------------------------------------------------------------------------------
# Part 2: the evidence (a map uploaded from a results folder of the compaction step)
# --------------------------------------------------------------------------------------
def storage_rows(summary: dict | None, folder: str, db: dict) -> list[tuple[str, int, str]]:
    """(what, bytes, note) for each form the map takes, from the original layers to Neon.
    summary: the compaction step's summary.json (None: the original layers are left out);
    db: bytes per approach in Neon - "evidence", "stencils", "outlines" - and the shared "grid_info"."""
    sizes = (summary or {}).get("sizes_bytes", {})
    raw = sizes.get("inputs_raw_for_bbox")
    rows = []
    if raw:
        rows.append(("original layers for the box, uncompressed", raw))
    if sizes.get("inputs_clipped_deflate"):
        rows.append(("original layers, clipped + DEFLATE", sizes["inputs_clipped_deflate"]))
    outcome = os.path.join(folder, "outcome.tif")
    for path, label in ((layers.evidence_file(folder), "evidence.tif (every fact, 32 bits a pixel)"),
                        (outcome, "outcome.tif (outcome classes with years)"),
                        (os.path.join(folder, layers.CLASS_MAP),
                         "outcome in 5 colours (the TIFF the stencils and outlines are built from)")):
        if os.path.isfile(path):
            rows.append((label, os.path.getsize(path)))
    for key, label in (("evidence", "Neon: evidence pages"), ("stencils", "Neon: stencils (5 colours)"),
                       ("outlines", "Neon: outlines (5 colours)")):
        if db.get(key) is not None:
            rows.append((label, db[key] + db.get("grid_info", 0)))
    return [(label, size, f"{100 * size / raw:.1f}% of the uncompressed layers" if raw and i else "")
            for i, (label, size) in enumerate(rows)]


def evidence_fidelity(rows: list[dict], reference: list[dict]) -> dict:
    """How many plots get every output (verdict, reason, every hectare figure, the years) the same
    as in reference, and which outputs differ where they don't."""
    differ = [(r["plot_id"], eudr_layers.compare_outputs(r, e)) for r, e in zip(rows, reference)]
    differ = [(pid, outputs) for pid, outputs in differ if outputs]
    return {"plots": len(rows), "identical": len(rows) - len(differ),
            "same_verdict": sum(r["verdict"] == e["verdict"] for r, e in zip(rows, reference)), "differ": differ}


def two_step_fidelity(rows: list[dict], reference: list[dict]) -> dict:
    """Like evidence_fidelity, for the two-step check: every output it gives must match. The figures a
    screened plot leaves empty (evidence_approach.SCREEN_UNKNOWN) aren't compared; they are counted."""
    differ, empty = [], 0
    for r, e in zip(rows, reference):
        given = {k: v for k, v in r.items() if v is not None and k != "in_forest_2020"}
        empty += sum(1 for v in r.values() if v is None)
        outputs = eudr_layers.compare_outputs(given, {k: e.get(k) for k in given})
        if outputs:
            differ.append((r["plot_id"], outputs))
    return {"plots": len(rows), "identical": len(rows) - len(differ), "empty": empty,
            "same_verdict": sum(r["verdict"] == e["verdict"] for r, e in zip(rows, reference)), "differ": differ}


def one_plot_at_a_time(check, plots, sample: int) -> float:
    """Median seconds to check ONE plot on its own (plots: a GeoDataFrame)."""
    picks = [plots.iloc[[i]] for i in range(0, len(plots), max(1, len(plots) // sample))][:sample]
    check(picks[0])  # warm-up
    return statistics.median(check(p)[1]["total_s"] for p in picks)


def evidence_part(conn, grid, folder: str, plots_path: str, args) -> None:
    print("\n=== Part 2: the evidence - Neon vs evidence.tif vs the original layers ===")
    try:
        evidence_approach.ensure_built(conn, grid)
    except SystemExit:
        print("  This map has no evidence pages (it was uploaded from a TIFF), so part 2 is skipped.")
        return
    evidence = layers.evidence_file(folder)
    rule = layers.rule_from(json.loads(layers.read_tags(evidence).get("RULE_PARAMS", "{}")))
    jgrid = layers.joe_grid(grid)
    row_area = eudr_layers.row_areas_m2(jgrid)
    plots, _ = evidence_approach.read_plots(plots_path)
    n = len(plots)

    def from_file(p):
        rows, seconds = eudr_layers.query_plots(p, evidence, jgrid, row_area, rule, args.min_plot_ha)
        return rows, {"total_s": seconds}

    def from_neon(p):
        return evidence_approach.check(conn, grid, p, args.min_plot_ha)

    def from_neon_two_step(p):
        return evidence_approach.check(conn, grid, p, args.min_plot_ha, two_step=True)

    print(f"  {n} plots, as eudr_layers.py reads them (a point is a circle of its declared area, 4 ha if none);"
          f"\n  each way checks them {args.repeat} times and the median is reported.")
    neon_rows, neon_s, timings = run_batch(from_neon, plots, args.repeat)
    two_rows, two_s, two_timings = run_batch(from_neon_two_step, plots, args.repeat)
    file_rows, file_s, _ = run_batch(from_file, plots, args.repeat)
    singles = {"neon": one_plot_at_a_time(from_neon, plots, args.single),
               "two_step": one_plot_at_a_time(from_neon_two_step, plots, args.single),
               "file": one_plot_at_a_time(from_file, plots, args.single)}
    conn.rollback()                                # the checks only read: end the transaction

    fid = evidence_fidelity(neon_rows, file_rows)
    two = two_step_fidelity(two_rows, file_rows)
    in_2020 = sum(1 for r in two_rows if r.get("in_forest_2020"))
    print(f"\n  The two key questions, forest on 31 Dec 2020 (from the stencils): {in_2020:,} of {n:,} plots "
          f"touch it, {n - in_2020:,} don't")
    summary = layers.summary(folder) or {}
    joe = summary.get("fidelity") or {}
    outputs = len(neon_rows[0]) - 1 if neon_rows else 0
    print(f"\n  Decision fidelity ({outputs} outputs a plot: verdict, reason, every hectare figure, the years)")
    print(f"    Neon vs evidence.tif              {fid['identical']:,} of {fid['plots']:,} plots identical "
          f"({100 * fid['identical'] / max(n, 1):.2f}%); same verdict {100 * fid['same_verdict'] / max(n, 1):.2f}%")
    print(f"    Neon two steps vs evidence.tif    {two['identical']:,} of {two['plots']:,} plots identical in every "
          f"output given ({100 * two['identical'] / max(n, 1):.2f}%); same verdict "
          f"{100 * two['same_verdict'] / max(n, 1):.2f}%\n"
          f"      ({two_timings['screened']:,} plots on no 2020 forest were settled from the stencils, leaving "
          f"{two['empty']:,} figures empty in all)")
    for pid, differ in two["differ"][:10]:
        print(f"    two-step mismatch {pid}: {', '.join(differ)}")
    if joe.get("plots_compared"):
        print(f"    evidence.tif vs original layers   {joe['plots_with_every_output_identical']:,} of "
              f"{joe['plots_compared']:,} plots identical ({joe['decision_fidelity_pct']}%); same verdict "
              f"{joe['same_verdict_pct']}%  (the compaction step's own check, --verify)")
    else:
        print("    evidence.tif vs original layers   not measured: build with --verify (pipeline.py passes it on)")
    for pid, differ in fid["differ"][:10]:
        print(f"    mismatch {pid}: {', '.join(differ)}")
    counts = {v: sum(r["verdict"] == v for r in neon_rows) for v in dict.fromkeys(r["verdict"] for r in neon_rows)}
    print("  Verdicts: " + ", ".join(f"{v} {c:,}" for v, c in sorted(counts.items(), key=lambda kv: -kv[1])))

    print(f"\n  {'Speed':34s}{f'all {n} at once':>16s}{'per plot':>12s}{'one plot alone':>17s}")
    for name, seconds, single in (("evidence.tif on disk (local)", file_s, singles["file"]),
                                  ("Neon evidence pages (over Neon)", neon_s, singles["neon"]),
                                  ("Neon, two steps (over Neon)", two_s, singles["two_step"])):
        print(f"    {name:32s}{seconds:14.3f} s{1000 * seconds / max(n, 1):9.2f} ms{1000 * single:14.2f} ms")
    if joe.get("query_ms_median"):
        files = (joe.get("files_read_per_plot") or {}).get("original_layers")
        print(f"    {'original layers (local, --verify)':32s}{'':16s}{joe['query_ms_median']['original_layers']:9.2f} ms"
              f"   median, {files} files a plot")
    print(f"  (Neon: 1 round trip per batch - the pages ({timings['pages']} fetched); the map's tables come with "
          f"its grid and evidence_info is read once per map."
          f"\n   Two steps: 2 - the stencil pages ({two_timings['stencil_pages']}), then the evidence pages "
          f"({two_timings['pages']}) of the plots on 2020 forest. No extra storage: the stencils are stored anyway.)")

    print("\n  Pixel fidelity")
    if not args.skip_verify:
        stats = evidence_approach.verify(conn, grid, folder)
        print(f"    evidence pages vs evidence.tif    {stats['mismatched']:,} of {stats['pixels']:,} pixels differ "
              f"(all 32 bits) -> {common.fidelity_text(stats)}")
    for r in summary.get("pixel_fidelity") or []:
        pct = "n/a" if r["exact_values_recovered_pct"] is None else f"{r['exact_values_recovered_pct']:.2f}%"
        print(f"    {r['layer'][:46]:46s} exact {pct:>8s}  kept as {r['stored_as']}")

    common.make_parent(args.csv_evidence)
    with open(args.csv_evidence, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["plot_id", "verdict_evidence_tif", "verdict_neon", "all_outputs_match", "outputs_that_differ",
                    "area_ha", "forest_2020_ha", "confirmed_ha", "probable_ha", "review_ha", "reason"])
        for r, e in zip(neon_rows, file_rows):
            differ = eudr_layers.compare_outputs(r, e)
            w.writerow([r["plot_id"], e["verdict"], r["verdict"], not differ, " ".join(differ), r["area_ha"],
                        r["forest_2020_ha"], r["confirmed_ha"], r["probable_ha"], r["review_ha"], r["reason"]])
    print(f"\nWrote {common.nice_path(args.csv_evidence)} (one row per plot)")


def storage_chain(conn, grid, folder: str) -> None:
    print("\n=== Storage: from the original layers to Neon ===")
    db = {"grid_info": sum(s for s, _ in common.table_sizes(conn, [grid.table("grid_info")]).values())}
    for key, module in (("evidence", evidence_approach), ("stencils", stencil_approach), ("outlines", polygon_approach)):
        sizes = common.table_sizes(conn, [grid.table(t) for t in module.TABLES])
        if sizes:
            db[key] = sum(s for s, _ in sizes.values())
    summary = layers.summary(folder)
    for label, size, note in storage_rows(summary, folder, db):
        print(f"  {label:72s} {size / 1e6:9.3f} MB  {note}")
    if summary is None:
        print("  (No summary.json in the results folder, so the original layers are left out.)")
    print("  (Neon sizes include indexes, PostgreSQL's per-row bookkeeping and the shared grid_info.\n"
          "   evidence.tif and outcome.tif also hold zoomed-out copies (overviews) for viewing.)")


def source_folder(path: str):
    """The results folder given (its path, or its name in layers/), or None for a TIFF."""
    for candidate in (path, os.path.join(common.ROOT_DIR, path), os.path.join(common.LAYERS_DIR, path)):
        if os.path.isdir(candidate):
            return layers.results_folder(candidate)
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", help="a results folder (its path, or its name in layers/), or a 5-colour TIFF")
    ap.add_argument("plots", nargs="?",
                    help="GeoJSON of farms (default for a results folder: its test_plots.geojson)")
    ap.add_argument("--map", help="which stored map to compare with (needed when there are several)")
    ap.add_argument("--point", nargs=2, type=float, action="append", metavar=("LON", "LAT"),
                    help="test one point: longitude first, then latitude (repeat for more points)")
    ap.add_argument("--rule", choices=["centre", "touched"], default="centre",
                    help="pixel rule for the stencils and the pixel baseline (default centre, as eudr_layers.py)")
    ap.add_argument("--point-mode", choices=["pixel", "circle"], default="pixel",
                    help="GeoJSON points: the pixel under the point (default), or a circle of the declared "
                         "area (4 ha if none, as eudr_layers.py)")
    ap.add_argument("--repeat", type=int, default=3, help="timing runs per method (median is reported)")
    ap.add_argument("--single", type=int, default=20,
                    help="how many farms to also time one at a time (default 20)")
    ap.add_argument("--skip-verify", action="store_true", help="skip the pixel-by-pixel rebuilds")
    ap.add_argument("--min-plot-ha", type=float, default=0.0,
                    help="part 2: ignore clearing of at most this many ha per plot, as eudr_layers.py "
                         "--min-plot-ha (default 0: the EUDR has no tolerance)")
    ap.add_argument("--csv", default=os.path.join(COMPARE_DIR, "results", "comparison.csv"),
                    help="where to write the per-farm table (default: compare/results/comparison.csv)")
    ap.add_argument("--csv-evidence", default=os.path.join(COMPARE_DIR, "results", "comparison_evidence.csv"),
                    help="part 2's per-plot table (default: compare/results/comparison_evidence.csv)")
    args = ap.parse_args()
    folder = source_folder(args.source)
    if folder:
        args.tif = layers.class_map(folder)
        test_plots = os.path.join(folder, "test_plots.geojson")
        if not args.plots and os.path.isfile(test_plots):
            args.plots = test_plots
    else:
        args.tif = common.find_tiff(args.source)
    if not args.plots and not args.point:
        ap.error("give a GeoJSON file of farms, or --point LON LAT (or both)")
    for lon, lat in args.point or []:
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            ap.error(f"--point {lon:g} {lat:g} is not a longitude/latitude pair - "
                     "give the longitude first (e.g. 103.5), then the latitude (e.g. -1.6)")
    args.repeat, args.single = max(1, args.repeat), max(1, args.single)
    if args.plots:
        args.plots = common.find_geojson(args.plots)
    all_touched = args.rule == "touched"

    with common.connect() as conn:
        conn.execute("SELECT 1")  # wake Neon up before timing anything
        grid = common.load_grid(conn, args.map)
        stencil_approach.ensure_built(conn, grid)
        has_outlines = common.has_tables(conn, grid, *polygon_approach.TABLES)
        if not has_outlines:
            print("This map was uploaded without outlines (--no-outlines): only the stencils are compared.")
        with rasterio.open(args.tif) as src:
            tif_grid = common.same_grid_or_exit(src, grid)
            tif_info = (os.path.getsize(args.tif), src.width * src.height * np.dtype(src.dtypes[0]).itemsize)
            _, fingerprint = common.scan_map(src, tif_grid)
        if fingerprint != grid.fingerprint:
            raise SystemExit(f"This TIFF is not the map stored as '{grid.map_version}' (same place, different "
                             "pixels).\nUpload it as a map of its own (python database/maps.py upload ...), "
                             "then compare with --map.")

        pixel_rule = "pixel rule" if all_touched else "centre rule"
        methods = [
            ("tiff_px", f"TIFF, {pixel_rule}", lambda fs: common.check_with_tiff(args.tif, grid, fs, "pixel", all_touched)),
            ("tiff_ex", "TIFF, exact area", lambda fs: common.check_with_tiff(args.tif, grid, fs, "exact")),
            ("stencils", "stencils", lambda fs: stencil_approach.check(conn, grid, fs, all_touched)),
        ]
        if has_outlines:
            methods.append(("polygons", "polygons", lambda fs: polygon_approach.check(conn, grid, fs)))
        if args.point:
            point_methods = [("original TIFF", methods[0][2])] + [(name, check) for _, name, check in methods[2:]]
            test_points(args.point, grid, point_methods, args.repeat)
            conn.rollback()  # test points are not saved
        if args.plots:
            print("\n=== Part 1: the 5 colours - stencils and outlines vs the TIFF ===" if folder else "")
            test_geojson(conn, grid, args, methods, tif_info, show_storage=folder is None)
            if folder:
                evidence_part(conn, grid, folder, args.plots, args)
        if folder:
            storage_chain(conn, grid, folder)


if __name__ == "__main__":
    main()
