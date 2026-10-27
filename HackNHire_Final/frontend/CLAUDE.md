# CLAUDE.md (frontend)

This is the desktop app for the EUDR storage prototype (PyQt5, plus one Leaflet map view).

It does three things:
- reads an EUDR map stored in Neon and shows it colour-coded. Neon can hold several maps, one schema each; the Map dropdown switches between them, and Maps ▾ renames or deletes them. The app doesn't upload maps: they are ingested with `backend/ingest_app.py` (or `pipeline.py`);
- lets the user draw points and polygons (or open GeoJSON), then checks them with both storage approaches;
- compares the approaches on verdicts, speed and query efficiency, for the two key questions: "is the point in forest?" and "does the outline overlap forest?".

`README.md` is the user guide and explains what every number means. `../backend/CLAUDE.md` has the map's classes, the EUDR rules and how the two approaches store the map. Read it before touching checking logic.

## How it fits together

```
app.py  MainWindow ─┬─ toolbar, tabs [Map | Compare], status bar, event hub (listen/emit)
                    ├─ MapArea(QWebEngineView)  ← web/map.html + map.js + map_eudr.js (Leaflet)
                    │     └─ Qt overlays floating on the map: Legend, Banner (eudr_layer.py)
                    ├─ ResultsPanel (results_panel.py)  right of the map, in a QSplitter
                    ├─ CompareTab   (compare_tab.py)    second tab
                    └─ Worker(QThread) ─► NeonService (neon_service.py) ─► backend modules ─► Neon
```

- **Native Qt for all UI.** The only web content is the Leaflet map. Its JavaScript draws map layers only (farms, tiles, halos), never panels or controls. The user chose this ("stay in PyQt"), so don't add HTML panels.
- **One background thread does all Neon work.** The UI thread never touches the database:
  - `window.run_job(kind, method, *args, done=, failed=, progress=, **kwargs)` runs `NeonService.<method>` on the worker.
  - Callbacks come back on the UI thread.
  - A `failed` callback that returns True means "I showed the error myself"; otherwise a dialog shows it.
  - `progress=` jobs get `progress(done, total, text)` and a `cancel` `threading.Event`; long jobs check it between steps and raise `Cancelled`.
- **Python and the map talk through QWebChannel (`pages.py`):**
  - Python → JS: `window.map.call("fnName", *json_args)`, which is queued until the page is ready. For one-off code there is `window.js("code")`.
  - JS → Python: `bridge.update(geojson)`, `bridge.state(state)`, `bridge.send(kind, json)`. Handlers are registered with `window.map.on(kind, fn)`.
  - JS console messages and errors are printed to the terminal as `[map] msg (line N)`.
- **Map tiles never touch the network.** `open_map()` downloads every stencil page once and rebuilds the full class array (6,000 × 6,000 uint8). `tiles.TileHandler` then serves `eudr://tiles/{z}/{x}/{y}.png?hide=…`, drawn by numpy from that array:
  - nearest pixel, never blended;
  - same floor rule as `Grid.point_to_pixel`;
  - transparent off the map.

  `tiles.register_scheme()` must run before `QApplication` is created.
- **Events between features** (`window.listen(event, fn)` / `window.emit(event, ...)`):

  | Event | Payload | Emitted by |
  |---|---|---|
  | `state` | map mode and selection | `app.py`, on every JS state change |
  | `geojson` | current FeatureCollection | `app.py`, after every edit |
  | `map_switching` | `(old, new)` map names (either may be None) | `maps_ui.py`, before another map loads or after the shown map is deleted |
  | `map_loaded` | `map_info()` dict (includes `map_version` and `schema`) | `eudr_layer.py` |
  | `checked` | the full `check()` result (rows, summary, runs, skipped, options) | `checks_ui.py` |
  | `results` | sorted rows, with a `stale` flag | `checks_ui.py` |

  New features are controller modules created in `MainWindow.__init__` under `# ----- features -----`. They add their own actions, listeners and widgets. Keep `app.py` thin.

## Files

| File | Job |
|---|---|
| `app.py` | the main window, toolbar, actions, events, job routing, CLI flags, stylesheet |
| `backend.py` | puts `../backend/database` on `sys.path` and re-exports `common`, `maps`, `polygon_approach`, `protected_areas`, `stencil_approach` |
| `neon_service.py` | **the only code that talks to Neon.** Plain Python, no Qt. `list_maps`, `open_map(name)`, `upload_map` (only for `smoke_maps.py`'s throwaway map; no menu reaches it), `describe_map`, `rename_map`, `delete_map`, `map_info`, `check`, `_measure`, `saved_farms`, `benchmark`, plus `UploadProgress` and `copy_to_tiff_folder` |
| `maps_ui.py` | the Map dropdown and Maps ▾ menu (`MapsController`), `RenameDialog`, `DeleteDialog`; with no maps, points to the ingest window; which map shows at start-up; each map's drawn farms while the app is open |
| `metering.py` | `MeteredConnection` (logs every query an approach sends: round trips, rows, bytes, time), `summarise`, `explain` (EXPLAIN ANALYZE re-run, always rolled back) |
| `farms.py` | map or file GeoJSON → backend `Farm`s (unique `plot_id`s, skipped shapes with reasons) |
| `report.py` | `PlotResult` → JSON rows, the verdict as the worse of the two approaches, agreement, summaries, sentences |
| `legend.py` | class codes, labels, map colours (the TIFF's legend), chart colours, method colours, verdict styles |
| `tiles.py` | tile maths, palette PNG encoder (zlib only), the `eudr://` scheme handler |
| `worker.py` | the `QThread` job queue: `submit`, `cancel`, `stop`, and coalescing of repeated jobs |
| `pages.py`, `map_area.py` | the map page loader, the bridge, and Qt widgets floating over the map |
| `eudr_layer.py`, `web/map_eudr.js` | the colour-coded layer, legend, opacity, Layers menu, loading/error banner with Retry; the map's protected areas (`open_map` reads them into `service.protected`; dashed outlines in the `protected` pane, with a legend row and a Layers toggle) |
| `checks_ui.py` | Point tool, Open ▾, Check ▾ and its options, stale and deleted detection, `open_path` |
| `results_panel.py` | results table (with a Protected column), and per-question "Stencils vs outlines · last check" comparison. `neon_service.check` runs the backend's `protected_areas.check` once and applies it to every method, so a farm in a protected area needs review |
| `compare_tab.py`, `farmgen.py` | the benchmark tab, and seeded made-up farms (points, plus 0.5–20 ha polygons) |
| `charts.py` | QPainter charts: `ChartCard` (title, legend, "Show table" twin), `PairBars` (small multiples), `LineChart` (log-log with crosshair) |
| `web/map.html`, `web/map.js` | the Leaflet map: drawing, editing, points, verdict styling, `addFeatures`, `setVerdicts`, `selectShape`, `zoomToShape` |
| `smoke_neon.py` | read-only end-to-end check against the real Neon (`--map NAME`) |
| `smoke_maps.py` | end-to-end check of several maps. **Writes**, but only a throwaway map (`zz smoke test`) that it deletes in `finally` |
| `tests/` | `unittest` suite. `tests/qt_app.py` holds the one shared `QApplication`; `tests/helpers.py` has `Script` (a fake connection answering by SQL fragment), `write_tiff` (made-up GeoTIFFs) and `FakeWindow` |

## Commands

Run from the project root (`HackNHire\`) in PowerShell. The venv is `frontend\.venv` (Python 3.13). Keep the code 3.10-compatible.

```
frontend\.venv\Scripts\python -m pip install -r requirements.txt pyflakes   # setup (the one requirements.txt, at the root)
frontend\.venv\Scripts\python frontend\app.py                                     # run the app
frontend\.venv\Scripts\python -m unittest discover -s frontend/tests -t frontend -v  # all tests
frontend\.venv\Scripts\python -m unittest discover -s frontend/tests -t frontend -p test_metering.py  # one file
frontend\.venv\Scripts\python -m pyflakes frontend\*.py frontend\tests\*.py         # lint
```

From `frontend\`:
- `.venv\Scripts\python smoke_neon.py --map jambi-v1` runs the read-only check against Neon. It exits with code 1 if a point check is wrong.
- `.venv\Scripts\python smoke_maps.py` tests upload, open, check, verify, cancel and delete on a throwaway map. It exits with code 1 on any mismatch. `--keep` leaves the map for screenshots; delete it afterwards with `backend\venv\Scripts\python backend\database\maps.py delete "zz smoke test" --yes`.

`app.py` flags for scripted checks and demos:
- `--map NAME` shows this stored map at start-up. Otherwise the app shows the last one used (`QSettings`), or the first.
- `--open FILE --check` adds a GeoJSON file's farms once the map has loaded, and checks them.
- `--benchmark` opens Compare and runs the benchmark.
- `--screenshot PATH --after SECONDS` saves `window.grab()`, then quits. Use it to look at the UI. `grabWindow` returns black for the web view, so don't use that.

`DATABASE_URL` comes from the environment, else `frontend/.env` (optional, read by `backend.py` at import with `common._read_env_file`; see `.env.example`), else `../backend/.env` (`common.connect()`).

## Checking a change

A change is done when:
1. All unit tests pass and pyflakes is clean.
2. For anything touching Neon, checks or numbers, `smoke_neon.py --map jambi-v1` passes: every class point matches, and the outlines agree. For anything touching maps (upload, switching, delete), `smoke_maps.py` passes too.
3. For UI changes, you took a `--screenshot` (with `--open … --check` or `--benchmark`) and looked at it for clipping, overlaps and truncation.

Show the commands and their output as evidence.

## Rules

- **Call the backend; don't copy it.** Call its functions through `backend.py`, so the app gives the same answers as the command-line tools. Change `backend/` only together with its "What the frontend relies on" list in `../backend/CLAUDE.md`.
- **Read-only by default.** The app never uploads maps (the user chose this: maps are ingested with `backend/ingest_app.py`, which builds the evidence too). The only writes are:
  - **Maps ▸ Rename map…** (`maps.rename`; the schema is renamed with it, nothing is copied. Renaming the map shown keeps it on screen, with its farms and results: `EudrLayer.renamed`);
  - **Maps ▸ Delete map…** (`maps.delete`; the user must type the exact name);
  - **Check ▸ Save results to Neon** (`common.save_results`, off by default).

  Never write map tables any other way. The user's real map is `jambi-v1` (schema `map_jambi_v1`): never upload over it or delete it without asking.
- **Every table is named with its map's schema** (`grid.table("stencil_pages")`). Never rely on `search_path`.
- **Every database call goes through `NeonService._run(work, write=False, retry=True)`.**
  - It ends the transaction: rollback, or commit when `write=True`.
  - On `psycopg.OperationalError` it reconnects once and retries. Uploads pass `retry=False`, because they are too long to restart by themselves.
  - Metered runs and EXPLAIN re-runs roll back too.
- **One batched query per approach and batch, never one per farm.** Each query is a round trip to Neon: about 70–100 ms from here.
- **Points and outlines are separate batches** in `check()` and `benchmark()`, so each question gets its own numbers. `check()["runs"][method][question]` holds:
  - `seconds`, `client_s`, `setup_s`, `round_trips`, `data_queries`, `rows`, `bytes`, `count`, `approach_s`;
  - `server`: `{server_exec_ms, planning_ms, shared_hit, shared_read, queries}` or None.
- **Honest numbers:**
  - Timings are "over Neon" from this computer, and benchmarks are the median of 3.
  - EXPLAIN numbers are a second, warm run and are labelled that way.
  - Round trips include each approach's own `table_exists` checks and `SET LOCAL jit = off`, because those are real costs. Data queries are also counted on their own.
  - The "which is faster" sentences are worked out from the data (`compare_tab.speed_story`). Never hard-code them.
- **The map's extent and default class come from `grid_info`** (`service.grid`), never from constants. The TIFF in `backend/data/tiff/` is used as a reference only when its fingerprint matches Neon's (`_find_tiff`).

## Gotchas

- **The map view is Qt WebEngine 5.15 (Chromium 83): JavaScript must be ES2019.** These are not available: `??=`, `||=`, `&&=`, `replaceAll`, `.at()`, `Object.hasOwn`, private class fields. Leaflet 1.9.4 and leaflet.draw 1.0.4 load from cdnjs.
- **Data text is untrusted.** Farm names come from files. In JS, use `esc()` or `textContent`, never raw `innerHTML`. In Qt, give labels that hold data `Qt.PlainText`.
- **Qt tests:** call `tests.qt_app.qt_app()` in `setUpClass`. It keeps the `QApplication` alive in a module global (an unreferenced one is deleted at once, and widgets then crash without a traceback). It also imports QtWebEngineWidgets first, which Qt requires.
- **pyflakes ignores `# noqa`.** Restructure the code instead (for example, `importlib.import_module` for a side-effect import).
- **Test grids:** use pixel sizes that are exact in binary (for example 1/8192°) when a test checks map edges. 1/12000° puts the "east edge" just inside the map.
- **`QStackedWidget` doesn't grow when a child's minimum height changes.** `PairBars.set_metrics` pushes its height to its parent. Do the same for any new chart that changes size.
- **psycopg's `PGresult` has no `get_length`.** `metering.result_size` sums `len(get_value(r, c))` to count the bytes Neon sent.
- **`EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` works with bound parameters on Neon.** Replay the run's `SET LOCAL` statements before it.
- **The Windows console is cp1252.** "…" in printed status text shows as garbage, so `smoke_neon.py` replaces it.
- **Tile URLs carry the map's schema** (`?map=map_jambi_v1&hide=…`). Blink caches images by URL, so without it a tile of one map could show on another.
- **Switching maps quickly.**
  - `open_map` jobs coalesce, so only the latest waiting one runs.
  - `EudrLayer.loaded` and `failed` ignore a map other than `EudrLayer.name`.
  - `MapsController.on_loaded` ignores a map other than `current`.
- **`CompareTab.job` is `None` when no benchmark is running.** `_idle` resets it. Switching maps cancels a running benchmark.
- **Cancelling an upload.** The worker clears its cancel flag when a job starts, so a Cancel pressed before the upload has begun is remembered by `UploadDialog` and passed on at the first progress report.
- **Smoke tests that write** use a throwaway map, and clean up in `finally`.

## Style

- **Colours** (from `legend.py`):
  - Map: the TIFF's legend.
  - Charts: stencils `#eb6834`, outlines `#1baf7a`.
  - Verdicts: status colours, always with an icon and a label (✖ Not compliant, ⚠ Needs review, ? Cannot check, ⚠ Check the location, ✔ Compliant).
  - Chart text stays in ink colours, never the series colour. A legend is shown for 2 or more series. Every chart has a "Show table" twin.
- **Wording:** plain English, British spelling (colour, metres). Error messages say how to fix the problem.
- **Dependencies:** none beyond the one `requirements.txt` in the HackNHire folder (shared with the backend). pyflakes is for lint only.
- **Docs:** when behaviour or numbers change, update `README.md`.

## Privacy

- Never print or log `DATABASE_URL` or anything from `.env`.
- Real GeoJSONs and TIFFs are customer data. Make demo farms with `farmgen.py` or in a temporary folder, not in the repo.
