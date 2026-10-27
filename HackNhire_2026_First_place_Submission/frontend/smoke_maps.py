"""smoke_maps.py - an end-to-end check of several maps in Neon.

WRITES to Neon, but only to a throwaway map ('zz smoke test'), which it deletes again even if a step
fails. Your own maps are only listed (before and after, to show they are untouched).

    .venv\\Scripts\\python smoke_maps.py [--keep]        (from frontend\\)

1. Makes a small made-up TIFF in a temporary folder (in Java, far from any real map) with every
   class, including a no-data corner.
2. Uploads it with both approaches (one transaction), lists it, and opens it: every pixel the app
   rebuilds from Neon must match the TIFF.
3. Checks a point on every class and 30 made-up outlines with both approaches, and with the TIFF
   (stencils against its pixel rule, outlines against its exact areas).
4. Rebuilds the whole map from each approach (backend verify): 0 differing pixels.
5. Starts a second upload and cancels it: nothing may be left behind.
   Renames the test map (its schema follows), checks it under the new name, and renames it back.
6. Deletes the test map, and checks that it is gone (skipped with --keep, e.g. for screenshots;
   delete it later with: python ..\\backend\\database\\maps.py delete "zz smoke test" --yes).
Exits with code 1 if anything doesn't match.
"""

import argparse
import os
import sys
import tempfile
import threading

import numpy as np
import rasterio
from rasterio.transform import from_origin

import farmgen
import neon_service
from backend import common, polygon_approach, stencil_approach
from legend import CLASS_LABELS

NAME, CANCEL_NAME, RENAMED = "zz smoke test", "zz smoke cancel", "ZZ Smoke Renamed"
X0, Y0, PIXEL = 110.0, -7.0, 1 / 12000   # Central Java: far from the real maps
WIDTH, HEIGHT = 700, 600


def made_up_map(seed=2026):
    """Class codes with every class present: forest blobs with deforestation inside them, a river,
    some 'needs review', and a no-data corner (255, which is read as class 5)."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:HEIGHT, 0:WIDTH]
    data = np.zeros((HEIGHT, WIDTH), np.uint8)
    for _ in range(14):
        cy, cx, r = rng.integers(0, HEIGHT), rng.integers(0, WIDTH), rng.integers(25, 90)
        data[(yy - cy) ** 2 + (xx - cx) ** 2 < r * r] = common.FOREST
    for _ in range(10):
        cy, cx, r = rng.integers(0, HEIGHT), rng.integers(0, WIDTH), rng.integers(5, 25)
        data[((yy - cy) ** 2 + (xx - cx) ** 2 < r * r) & (data == common.FOREST)] = common.DEFORESTATION
    for _ in range(6):
        cy, cx, r = rng.integers(0, HEIGHT), rng.integers(0, WIDTH), rng.integers(5, 20)
        data[(yy - cy) ** 2 + (xx - cx) ** 2 < r * r] = common.REVIEW
    data[np.abs(yy - (HEIGHT / 2 + 60 * np.sin(xx / 70))) < 6] = common.WATER
    data[:40, :50] = 255
    return data


def write_tiff(path, data):
    with rasterio.open(path, "w", driver="GTiff", width=data.shape[1], height=data.shape[0], count=1,
                       dtype="uint8", crs="EPSG:4326", transform=from_origin(X0, Y0, PIXEL, PIXEL),
                       nodata=255) as dst:
        dst.write(data, 1)
    return path


def points_on_every_class(service):
    """A point in the middle of each class's pixels, and the class it must get."""
    grid, classes = service.grid, service.classes
    features, expected = [], {}
    for code in np.unique(classes):
        rows, cols = np.nonzero(classes == code)
        pick = len(rows) // 2
        x, y = grid.pixel_centre(int(rows[pick]), int(cols[pick]))
        key = len(features) + 1
        expected[key] = int(code)
        features.append({"type": "Feature", "properties": {"id": key, "name": f"{CLASS_LABELS[int(code)]} point"},
                         "geometry": {"type": "Point", "coordinates": [float(x), float(y)]}})
    return features, expected


def schema_exists(service, schema):
    def ask(conn):
        with conn.cursor() as cur:
            cur.execute("SELECT to_regnamespace(%s) IS NOT NULL", (schema,))
            return cur.fetchone()[0]
    return service._run(ask)


def main():
    ap = argparse.ArgumentParser(description="End-to-end check of several maps in Neon (writes a throwaway map).")
    ap.add_argument("--keep", action="store_true", help="leave the test map in Neon (e.g. for screenshots)")
    args = ap.parse_args()
    service = neon_service.NeonService(on_status=lambda text: print(" ", text.replace("…", "...")))
    problems = []

    def expect(ok, what):
        print(f"  {'OK ' if ok else 'BAD'} {what}")
        if not ok:
            problems.append(what)

    def names():
        return [m["name"] for m in service.list_maps()]

    before = {m["name"]: m for m in service.list_maps()}
    for leftover in (NAME, CANCEL_NAME, RENAMED):  # from an earlier run that was stopped
        if leftover in before:
            service.delete_map(leftover)
            before.pop(leftover)
    print("Your maps:", ", ".join(before) or "none")

    with tempfile.TemporaryDirectory() as tmp:
        data = made_up_map()
        tif = write_tiff(os.path.join(tmp, "smoke_map.tif"), data)
        try:
            print(f"\n1. Uploading a made-up {WIDTH} x {HEIGHT} map as '{NAME}'...")
            shown = set()

            def progress(done, total, text):
                step = text.split(" · ")[0]
                if step not in shown:
                    shown.add(step)
                    print("   ", text.replace("·", "-"))
            result = service.upload_map(tif, NAME, keep_copy=False, progress=progress)
            listed = {m["name"]: m for m in service.list_maps()}
            entry = listed.get(NAME)
            expect(result["name"] == NAME and entry is not None and entry["stencils"] and entry["outlines"],
                   "listed, with both approaches built")

            print("\n2. Opening it from Neon...")
            info = service.open_map(NAME)
            expect(info["map_version"] == NAME and info["schema"] == common.map_schema(NAME),
                   f"opened from schema {info['schema']}")
            expect(np.array_equal(service.classes, common.normalise(data)),
                   "every pixel rebuilt from Neon matches the TIFF")

            print("\n3. Checking farms with both approaches, and with the TIFF...")
            service.tif_path = tif
            features, expected = points_on_every_class(service)
            outlines = farmgen.make_features(neon_service.map_extent(service.grid), 0, 30, seed=7)
            for i, f in enumerate(outlines):
                f["properties"]["id"] = 100 + i
            out = service.check(features + outlines, explain=False)
            for row in out["rows"]:
                if row["kind"] == "point":
                    got = {row["results"][m]["point_colour"] for m in ("stencils", "polygons", "tiff_pixel")}
                    expect(got == {expected[row["key"]]}, f"{row['name']}: {CLASS_LABELS[expected[row['key']]]}")
            shapes = [r for r in out["rows"] if r["kind"] == "outline"]
            expect(all(r["agree"]["stencils_tiff"] for r in shapes),
                   f"stencils match the TIFF (pixel rule) on all {len(shapes)} outlines")
            expect(all(r["agree"]["polygons_tiff"] for r in shapes),
                   f"outlines match the TIFF (exact areas) on all {len(shapes)} outlines")
            expect(out["map"] == NAME, "the results say which map they were checked against")

            print("\n4. Rebuilding the map from each approach (verify)...")
            for label, module in (("stencils", stencil_approach), ("outlines", polygon_approach)):
                stats = service._run(lambda conn: module.verify(conn, service.grid, tif))
                expect(stats["mismatched"] == 0,
                       f"{label}: {stats['mismatched']:,} of {stats['pixels']:,} pixels differ")

            print("\n5. Cancelling an upload part-way...")
            other = write_tiff(os.path.join(tmp, "cancel_map.tif"), np.flipud(data).copy())
            cancel = threading.Event()
            try:
                service.upload_map(other, CANCEL_NAME, keep_copy=False,
                                   progress=lambda done, total, text: cancel.set(), cancel=cancel)
                expect(False, "the cancelled upload stopped")
            except neon_service.Cancelled:
                expect(True, "the cancelled upload stopped")
            expect(CANCEL_NAME not in names(), "nothing of the cancelled upload is listed")
            expect(not schema_exists(service, common.map_schema(CANCEL_NAME)), "and no schema was left behind")

            print(f"\n5b. Renaming '{NAME}' to '{RENAMED}' and back...")
            renamed = service.rename_map(NAME, RENAMED)
            new_schema = common.map_schema(RENAMED)
            expect(renamed["map"] is not None and renamed["map"]["schema"] == new_schema,
                   f"the map shown is renamed in place (schema {new_schema})")
            expect(RENAMED in names() and NAME not in names(), "listed under its new name only")
            expect(schema_exists(service, new_schema) and not schema_exists(service, common.map_schema(NAME)),
                   "its schema was renamed")
            again = service.check(features, explain=False)
            expect(again["map"] == RENAMED and all(
                row["results"]["polygons"]["point_colour"] == expected[row["key"]] for row in again["rows"]),
                "checks still work under the new name")
            service.open_map(RENAMED)
            expect(np.array_equal(service.classes, common.normalise(data)), "and it opens with every pixel")
            service.rename_map(RENAMED, NAME)
            expect(NAME in names() and RENAMED not in names(), "renamed back")
        finally:
            if args.keep:
                print(f"\n6. Keeping '{NAME}' (--keep).")
            else:
                print(f"\n6. Deleting '{NAME}'...")
                for name in (NAME, RENAMED):
                    if name in names():
                        service.delete_map(name)
                expect(NAME not in names(), "deleted from the list of maps")
                expect(not schema_exists(service, common.map_schema(NAME)), "its schema is gone")

    after = {m["name"]: m for m in service.list_maps() if m["name"] != NAME}
    expect(after == before, "your other maps are untouched")
    service.close()
    if problems:
        print(f"\n{len(problems)} problem(s).")
        return 1
    print("\nEverything matches.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
