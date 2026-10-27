"""smoke_neon.py - a quick end-to-end check of the app's Neon link. Read-only: nothing is written.

    .venv\\Scripts\\python smoke_neon.py [--map NAME]        (from frontend\\)

1. Opens the map from Neon (grid_info + every stencil page) and prints the class shares.
2. Puts a point on one pixel of each class and checks that both approaches name that class.
3. Checks 20 made-up outlines and reports how often the two approaches give the same verdict.
4. Prints each approach's query efficiency for both questions.
Exits with code 1 if a point check is wrong.
"""

import argparse
import sys

import numpy as np

import farmgen
import neon_service
from legend import CLASS_LABELS


def main():
    ap = argparse.ArgumentParser(description="Read-only check of the app's link to Neon.")
    ap.add_argument("--map", help="which stored map to check (needed when Neon holds several)")
    args = ap.parse_args()
    service = neon_service.NeonService(on_status=lambda text: print(" ", text.replace("…", "...")))
    info = service.open_map(args.map)
    print(f"Map {info['map_version']}: {info['width']:,} x {info['height']:,} px")
    for c in info["classes"]:
        print(f"  {c['label']:<14} {c['share']:6.2f}%")

    grid, classes = service.grid, service.classes
    features, expected = [], {}
    for c in info["classes"]:
        rows, cols = np.nonzero(classes == c["code"])
        pick = len(rows) // 2
        x, y = grid.pixel_centre(int(rows[pick]), int(cols[pick]))
        if service.to_lonlat is not None:
            x, y = service.to_lonlat(x, y)
        key = len(features) + 1
        expected[key] = c["code"]
        features.append({"type": "Feature", "properties": {"id": key, "name": f"{c['label']} point"},
                         "geometry": {"type": "Point", "coordinates": [float(x), float(y)]}})
    outlines = farmgen.make_features(neon_service.map_extent(grid), 0, 20, seed=99)
    for i, f in enumerate(outlines):
        f["properties"]["id"] = 100 + i
    out = service.check(features + outlines)

    wrong = 0
    print("\nPoints (is the point in forest of 31 Dec 2020?):")
    for row in out["rows"]:
        if row["kind"] != "point":
            continue
        got = {m: row["results"][m]["point_colour"] for m in ("stencils", "polygons")}
        ok = all(v == expected[row["key"]] for v in got.values())
        wrong += not ok
        print(f"  {'OK ' if ok else 'BAD'} {row['name']:<22} stencils: {CLASS_LABELS[got['stencils']]:<14} "
              f"outlines: {CLASS_LABELS[got['polygons']]:<14} 2020 forest: "
              f"{'yes' if row['results']['polygons']['in_forest_2020'] else 'no':<4} verdict: {row['verdict']}")

    shapes = [r for r in out["rows"] if r["kind"] == "outline"]
    same = sum(1 for r in shapes if r["agree"]["stencils_polygons"])
    print(f"\nOutlines (does the outline overlap forest of 31 Dec 2020?): same verdict on {same} of {len(shapes)}")
    for r in shapes:
        if not r["agree"]["stencils_polygons"]:  # pixel rule vs exact area: can differ on slivers
            print(f"  differ: {r['name']}: stencils {r['results']['stencils']['verdict']}, "
                  f"outlines {r['results']['polygons']['verdict']}")

    print("\nQuery efficiency (one batch per approach, over Neon):")
    for method, label in (("stencils", "Stencils"), ("polygons", "Outlines")):
        for question, stats in out["runs"][method].items():
            server = stats["server"] or {}
            print(f"  {label:<9} {question:<8} {1000 * stats['seconds']:7.0f} ms  "
                  f"{stats['round_trips']} round trips  {stats['rows']:5} rows  {stats['bytes']:9,} B  "
                  f"server {server.get('server_exec_ms', 0):7.2f} ms  "
                  f"{server.get('shared_hit', 0) + server.get('shared_read', 0):5} blocks")
    service.close()
    if wrong:
        print(f"\n{wrong} point check(s) wrong.")
        return 1
    print("\nAll point checks match the map.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
