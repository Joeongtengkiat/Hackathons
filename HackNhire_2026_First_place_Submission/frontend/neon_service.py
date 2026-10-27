"""neon_service.py - the only part of the app that talks to the backend and to Neon.

Plain Python (no Qt), so it can be tested on its own and could later sit behind a web API.
It calls the backend's own functions (see backend.py), so every answer is the one the
command-line tools give.

It also lists, renames and deletes the maps stored in Neon (backend/database/maps.py): those
and saving results are the app's only writes. The app doesn't upload maps (they are ingested with
backend/ingest_app.py); upload_map is kept for smoke_maps.py's throwaway test map only.

Every public method goes through _run(): the transaction is always ended afterwards, so a
SET LOCAL jit = off never carries over and Neon's pooled connection is not held. If Neon has
gone to sleep and dropped the connection, it reconnects once and tries again.
"""

import dataclasses
import os
import shutil
import time

import numpy as np
import psycopg
import rasterio
from pyproj import Transformer

from backend import common, maps, polygon_approach, protected_areas, stencil_approach
import farmgen
import metering
from farms import farm_area_ha, features_from_file, prepare_farms
from legend import CLASS_LABELS, CLASSES, MAP_COLOURS
from report import farm_row, summarize

PAGE = common.PAGE


class Cancelled(Exception):
    """Raised inside a long job when the user presses Cancel."""


def classes_from_pages(grid, rows):
    """Stencil pages from Neon -> the whole map as a (height, width) uint8 array of colours.
    Unpacks like stencil_approach.verify; a page with no row is all the default colour."""
    out = np.full((grid.height, grid.width), grid.default_class, np.uint8)
    for page_col, page_row, blob in rows:
        h, w = stencil_approach.page_size(grid, page_col, page_row)
        out[page_row * PAGE:page_row * PAGE + h, page_col * PAGE:page_col * PAGE + w] = \
            stencil_approach.unzip_page(bytes(blob), h, w)
    return out


def need_outlines(grid):
    """The app compares stencils with outlines: stop with a clear message on a map without them."""
    if not {"class_polys", "square_labels"} <= grid.tables:
        raise SystemExit(f"The map '{grid.map_version}' was uploaded without outlines (--no-outlines), so the "
                         "app can't compare the two approaches on it. Ingest it again with the outlines (the "
                         "ingest window's 'Also store the outlines' box, or pipeline.py without --no-outlines), "
                         "or check it from the command line: "
                         "python database/stencil_approach.py check FARMS.geojson --map NAME")


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


def copy_to_tiff_folder(path):
    """Keep a copy of an uploaded TIFF in backend/data/tiff, where the app looks for the original
    TIFF to compare with. Never overwrites a file. Returns what happened, for the status bar."""
    name = os.path.basename(path)
    folder = os.path.abspath(common.TIFF_DIR)
    if os.path.dirname(os.path.abspath(path)) == folder:
        return f"{name} is already in backend/data/tiff."
    target = os.path.join(folder, name)
    if os.path.exists(target):
        return f"backend/data/tiff already has a {name}, so no copy was made."
    try:
        os.makedirs(folder, exist_ok=True)
        shutil.copy2(path, target)
    except OSError as exc:  # the map is in Neon already: a failed copy is only a note
        return f"Couldn't copy {name} to backend/data/tiff ({exc}). Copy it there by hand to compare with it."
    return f"Copied {name} to backend/data/tiff."


class UploadProgress:
    """Turns the backend's progress reports (label, done, total) into one bar for the whole upload:
    each new label is the next of maps.UPLOAD_STEPS steps. Raises Cancelled once cancel is set."""

    PART = {1: "Stencils", 2: "Stencils", 3: "Stencils", 4: "Outlines", 5: "Outlines", 6: "Outlines"}

    def __init__(self, progress, cancel=None, steps=maps.UPLOAD_STEPS):
        self.progress, self.cancel, self.steps = progress, cancel, steps
        self.step, self.label = 0, None

    def __call__(self, label, done, total):
        if self.cancel is not None and self.cancel.is_set():
            raise Cancelled()
        if label != self.label:
            self.step, self.label = min(self.step + 1, self.steps), label
        share = done / total if total else 0.0
        part = self.PART.get(self.step)
        text = f"{part} · {label}" if part else label[:1].upper() + label[1:]
        self.progress(int(1000 * (self.step - 1 + share) / self.steps), 1000,
                      f"Step {self.step} of {self.steps} · {text} · {100 * share:.0f}%")


class NeonService:
    def __init__(self, connect=None, on_status=None):
        self._connect = connect or common.connect
        self.on_status = on_status or (lambda text: None)
        self.conn = None
        self.grid = self.classes = self.counts = None
        self.protected = None                   # the map's protected areas (GeoJSON, lon/lat) or None
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

    def _run(self, work, write=False, retry=True):
        """Run work(conn) and end the transaction (commit if write, else rollback). A dropped
        connection (psycopg.OperationalError) gets one reconnect and one retry - unless
        retry=False: an upload is too long to start again by itself."""
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
                if attempt == 2 or not retry:
                    raise
                self.on_status("Reconnecting to Neon…")
            except BaseException:
                try:
                    self.conn.rollback()
                except Exception:  # noqa: BLE001 - connection broken: start fresh next time
                    self._close()
                raise

    # ----- the maps stored in Neon -----
    def list_maps(self):
        """Every map in Neon (maps.list_maps): name, schema, source file, size, approaches built."""
        return self._run(maps.list_maps)

    def upload_map(self, path, name, keep_copy=True, progress=None, cancel=None):
        """Upload a TIFF as a new map with both approaches, in one transaction: a failure or Cancel
        leaves Neon as it was. With keep_copy, the TIFF is also copied into backend/data/tiff.
        Not reachable from the app's menus: only smoke_maps.py uses it, for its throwaway map."""
        report = UploadProgress(progress or (lambda done, total, text: None), cancel)
        self.on_status(f"Uploading {os.path.basename(path)} to Neon as '{name}'…")
        grid = self._run(lambda conn: maps.upload(conn, path, name, report), write=True, retry=False)
        return {"name": grid.map_version, "copy": copy_to_tiff_folder(path) if keep_copy else None}

    def describe_map(self, name):
        """What a map uses in Neon, for the delete dialog (maps.describe)."""
        return self._run(lambda conn: maps.describe(conn, name))

    def delete_map(self, name):
        """Delete a map and everything stored with it, and forget it if it is the map shown."""
        info = self._run(lambda conn: maps.delete(conn, name), write=True)
        if self.grid is not None and self.grid.map_version == info["name"]:
            self.grid = self.classes = self.counts = None
            self.source_file = self.tif_path = None
        return info

    def rename_map(self, name, new_name):
        """Rename a map (maps.rename). If it is the map shown, it is renamed in place, without being
        read again: "map" is its new map_info(), else None."""
        info = self._run(lambda conn: maps.rename(conn, name, new_name), write=True)
        info["map"] = None
        if self.grid is not None and self.grid.map_version == info["old"]:
            self.grid = dataclasses.replace(self.grid, map_version=info["name"], schema=info["schema"])
            info["map"] = self.map_info()
        return info

    # ----- the map shown -----
    def open_map(self, name=None):
        """The map's grid_info, then every stencil page in one query, unpacked into self.classes,
        and its protected areas, if it has any (one more query), into self.protected.
        name: which stored map (None: the only one). Returns what the map page needs (JSON-ready)."""
        def load(conn):
            grid = common.load_grid(conn, name)
            stencil_approach.ensure_built(conn, grid)
            with conn.cursor() as cur:
                cur.execute(f"SELECT source_file FROM {grid.table('grid_info')} LIMIT 1")
                source_file = cur.fetchone()[0]
                cur.execute(f"SELECT page_col, page_row, colours FROM {grid.table('stencil_pages')}")
                rows = cur.fetchall()
            return grid, source_file, rows, protected_areas.load(conn, grid)

        self.on_status(f"Reading the map '{name}' from Neon…" if name else "Reading the map from Neon…")
        grid, self.source_file, rows, self.protected = self._run(load)
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
            "schema": grid.schema,
            "width": grid.width, "height": grid.height, "srid": grid.srid,
            "pixel_w": grid.pixel_w, "pixel_h": grid.pixel_h,
            "bounds": [[south, west], [north, east]],
            "default_class": grid.default_class,
            "classes": [{"code": c, "label": CLASS_LABELS[c], "colour": MAP_COLOURS[c],
                         "pixels": int(counts[c]), "share": 100.0 * int(counts[c]) / total}
                        for c in CLASSES if counts[c]],
            "tiff": {"available": self.tif_path is not None, "note": self.tif_note},
            "protected_areas": len(self.protected["features"]) if self.protected else 0,
        }

    # ----- checking farms -----
    QUESTIONS = ("point", "outline")  # "is the point in forest of 31 Dec 2020?", "does the outline overlap it?"

    def check(self, features, point_mode="pixel", rule="centre", with_tiff=True, save=False,
              explain=True):
        """Check farms with both approaches against Neon, and with the TIFF when it is the same map.
        features: GeoJSON features from the map (properties: id, name).

        Points and outlines are checked as separate batches, so each question gets its own numbers:
        four runs (approach x question), each in its own transaction, as a real request would be.
        Every run is metered (round trips, rows, bytes, time over Neon); with explain=True its data
        queries are run again under EXPLAIN (ANALYZE, BUFFERS) for the server's side."""
        grid, all_touched = self.grid, rule == "touched"
        need_outlines(grid)
        farms, keys, skipped = prepare_farms(features, grid, point_mode)
        results = {m: [None] * len(farms) for m in ("stencils", "polygons")}
        seconds = {"stencils": 0.0, "polygons": 0.0}
        runs = {m: {} for m in ("stencils", "polygons")}
        batches = {q: [i for i, f in enumerate(farms) if f.kind == q] for q in self.QUESTIONS}
        found = {}  # farm index -> protected_areas.Overlap

        def run(conn):
            for method in ("stencils", "polygons"):
                for question, idx in batches.items():
                    if not idx:
                        continue
                    answers, stats, log = self._measure(conn, method, [farms[i] for i in idx], all_touched)
                    if explain:
                        stats["server"] = metering.explain(conn, log)
                    for i, answer in zip(idx, answers):
                        results[method][i] = answer
                    seconds[method] += stats["seconds"]
                    runs[method][question] = stats
            # Protected areas send farms for review: one query, the same answer for both approaches
            # (and the TIFF below), so it is not part of either approach's numbers.
            found.update(protected_areas.check(conn, grid, farms))
            conn.rollback()
            for method in ("stencils", "polygons"):
                protected_areas.apply(results[method], found)
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
                    protected_areas.apply(answers, found)
        rows = [farm_row(key, farm.plot_id, farm.kind, farm_area_ha(farm, grid),
                         {method: answers[i] for method, answers in results.items()})
                for i, (key, farm) in enumerate(zip(keys, farms))]
        return {"map": grid.map_version, "rows": rows, "summary": summarize(rows, seconds), "runs": runs,
                "skipped": [{"name": n, "reason": r} for n, r in skipped],
                "options": {"point_mode": point_mode, "rule": rule, "tiff": use_tiff,
                            "saved": bool(save and farms), "explain": bool(explain)}}

    def _measure(self, conn, method, farms, all_touched=False):
        """One approach on one batch of farms, metered, then rolled back.
        Returns (answers, stats, query log)."""
        metered = metering.MeteredConnection(conn)
        t0 = time.perf_counter()
        if method == "stencils":
            answers, timings = stencil_approach.check(metered, self.grid, farms, all_touched)
        else:
            answers, timings = polygon_approach.check(metered, self.grid, farms)
        wall = time.perf_counter() - t0
        conn.rollback()
        stats = metering.summarise(metered.log)
        stats.update(count=len(farms), seconds=wall, approach_s=timings["total_s"], server=None)
        return answers, stats, metered.log

    def saved_farms(self):
        """Farms saved in Neon (the plots table) and their stored verdicts for this map version."""
        grid = self.grid

        def load(conn):
            if not common.table_exists(conn, grid.table("plots")):
                return [], []
            with conn.cursor() as cur:
                cur.execute(f"SELECT plot_id, geojson FROM {grid.table('plots')} ORDER BY plot_id")
                plots = cur.fetchall()
                cur.execute(f"SELECT plot_id, method, verdict FROM {grid.table('plot_results')} "
                            "WHERE map_version = %s", (grid.map_version,))
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

    # ----- benchmark -----
    def benchmark(self, sizes=(1, 10, 100, 1000), repeat=3, explain=True, progress=None, cancel=None):
        """Both approaches on made-up farms (farmgen) of growing batch sizes, one question at a time.
        Each run is its own transaction, rolled back, so nothing is written. Reports the median run
        of `repeat` (by time over Neon); explain=True adds the median run's server numbers."""
        progress = progress or (lambda done, total, text: None)
        need_outlines(self.grid)
        methods, questions = ("stencils", "polygons"), ("point", "outline")
        total = len(sizes) * len(questions) * len(methods) * repeat
        out = {"map": self.grid.map_version, "sizes": list(sizes), "repeat": repeat,
               "series": {m: {q: [] for q in questions} for m in methods},
               "agreement": {q: [] for q in questions}}

        def warm_up(conn):  # wake Neon up first, so the first timing isn't a cold start
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchall()

        progress(0, total, "Waking Neon up…")
        self._run(warm_up)
        done = 0
        for n in sizes:
            features = farmgen.make_features(map_extent(self.grid), n, n, seed=2026 + n)
            farms, _, _ = prepare_farms(features, self.grid, "pixel")
            for question in questions:
                batch = [f for f in farms if f.kind == question]
                verdicts = {}
                for method in methods:
                    measured = []
                    for r in range(repeat):
                        if cancel is not None and cancel.is_set():
                            raise Cancelled()
                        progress(done, total, f"{n:,} {question}{'' if n == 1 else 's'} · "
                                              f"{'stencils' if method == 'stencils' else 'outlines'} · "
                                              f"run {r + 1} of {repeat}")
                        measured.append(self._run(lambda conn: self._measure(conn, method, batch)))
                        done += 1
                    measured.sort(key=lambda m: m[1]["seconds"])
                    answers, stats, log = measured[len(measured) // 2]
                    if explain:
                        stats["server"] = self._run(lambda conn: metering.explain(conn, log))
                    stats["n"] = n
                    out["series"][method][question].append(stats)
                    verdicts[method] = [a.verdict for a in answers]
                same = sum(1 for a, b in zip(verdicts["stencils"], verdicts["polygons"]) if a == b)
                out["agreement"][question].append({"n": n, "same": same, "of": len(batch)})
        progress(total, total, "Done")
        return out
