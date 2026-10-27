# Frontend ↔ Neon link: design

Date: 2026-10-15 · Status: approved in chat, awaiting spec review

## Goal

Connect the PyQt map app (`frontend/app.py`) to the EUDR map stored in Neon so that, for the
SystemEarth demo on a local laptop, you can:

1. see the map colour-coded by class, drawn from Neon;
2. test points and outlines (drawn, or loaded from GeoJSON) against both storage approaches;
3. compare the two approaches with statistics, charts and diagrams.

**Done means:**
- one command (`python frontend/app.py`) opens the app, and the coloured map appears from Neon;
- checking a farm zooms to it and says, in plain words, whether it is compliant, not compliant or
  needs review, with the same verdict `compare.py` gives;
- the areas inside the farm that cause a problem verdict are highlighted;
- the Statistics tab shows test results, storage, a speed benchmark and fidelity for both approaches.

## Decisions

| Decision | Choice | Why |
|---|---|---|
| Frontend | Keep the PyQt5 app. **Everything new is native Qt widgets**: results table, verdict card, legend, statistics, charts (drawn with QPainter) and diagrams (QPainter / QSvgWidget). Only the map itself stays the Leaflet view inside the window, as today | Your choice ("stay in PyQt"); no new packages |
| How the app reaches the backend | In-process: the app imports `backend/database/*.py` and queries Neon on one background thread (approach A) | One process, fewest moving parts, answers identical to the CLI. A local web API (approach B) stays possible later: it would wrap `neon_service.py` |
| Where map data comes from | Neon only. The coloured map is rebuilt from `stencil_pages`; checks query Neon | Your requirement |
| The TIFF | Optional reference only: TIFF baseline answers and fidelity, when `backend/data/tiff/<grid_info.source_file>` exists and its fingerprint matches Neon's | Fidelity needs a reference; everything else works without it |
| Map extent | Read from `grid_info`, replacing the hard-coded box in `app.py` | Neon holds 103.0–103.5 °E, −2.3 to −1.8 °N; the old box ran to 104.0 °E |
| Saving test farms | Off by default; "Save results to Neon" option | Keeps test drawings out of `plots` / `plot_results` |
| Colours | The legend embedded in `my_map.tif` | Matches the map's own key |
| Backend code | Unchanged | The CLI rules in `backend/CLAUDE.md` stay intact |

## What is in Neon today (read-only probe, 2026-10-15)

| | |
|---|---|
| Server | PostgreSQL 18.6, PostGIS 3.6.4, pooled (PgBouncer) connection |
| Map | `jambi-v1`, 6000 × 6000 px, EPSG:4326, pixel 0.0000833°, default (not stored) colour: land |
| `stencil_pages` | 416 rows, 1.343 MB |
| `class_polys` | 100,927 outlines, 32.195 MB; corners: water 2,333, forest 781,028, deforestation 199,473, needs review 410,168 |
| `square_labels` | 576 rows, 0.123 MB |
| `plots`, `plot_results` | empty |

Map legend (from the TIFF's metadata): 0 land `#E1E1E1`, 1 water `#6BAED6`, 2 forest `#1B7837`,
3 deforestation `#E31A1C`, 4 needs review `#6A3D9A`, no data `#000000`.

## Architecture

```
PyQt window (app.py)                         background thread                        Neon
 ├─ Map tab
 │   ├─ map area: Leaflet view (web/map.html) ─jobs─► worker.py ─► neon_service.py ─► backend/database/*.py ─► grid_info
 │   │    tiles: eudr://tiles/{z}/{x}/{y}.png ◄── tiles.py (from the in-memory map)                            stencil_pages
 │   │    + native Qt overlays: legend, loading banner, verdict card                                          class_polys
 │   └─ right panel (Qt): Results table | Inspect diagram  ◄─results─┘                                        square_labels
 └─ Statistics tab (Qt): QPainter charts + How it works (QSvgWidget)                                          plots, plot_results
```

### Files (all new work lives in `frontend/`)

| File | Job | Depends on |
|---|---|---|
| `neon_service.py` | The only module that touches the backend or Neon. Plain Python, no Qt, so it can be tested and later wrapped by an API | `backend/database` (via `sys.path`, as `compare.py` does), numpy, shapely, pyproj, psycopg |
| `worker.py` | One `QThread` that owns the `NeonService` (and so the connection) and runs jobs one at a time; emits `finished(job_id, result)`, `failed(job_id, message)`, `progress(job_id, done, total, text)` | `neon_service.py`, PyQt5 |
| `tiles.py` | `render_tile(...)` (pure numpy) and a `QWebEngineUrlSchemeHandler` serving PNG tiles at `eudr://tiles/...` | numpy, PyQt5 |
| `app.py` | The window: existing toolbar plus new actions, the right panel, the Statistics tab, wiring between pages and the worker | all of the above |
| `web/map.html`, `web/map*.js` | The map only (Leaflet, as today): farms, EUDR tiles, focus highlights, stored outlines. `app.py` loads it the way it does today: inline `qwebchannel.js`, `setHtml` with a base URL | Leaflet 1.9.4, leaflet.draw 1.0.4 (cdnjs) |
| `charts.py`, `results_panel.py`, `focus.py`, `stats_tab.py`, `inspect_view.py`, `diagrams/*.svg` | Native Qt: QPainter charts, the results panel, the verdict card over the map, the Statistics tab, the Inspect diagram, the How it works pictures | PyQt5 (QtWidgets, QtSvg) |
| `smoke_neon.py` | Read-only end-to-end check against Neon | `neon_service.py` |
| `tests/` | `unittest` tests for the pure parts | stdlib `unittest` |
| `requirements.txt` | Adds `-r ../backend/requirements.txt` | |

### `neon_service.py` interface

```python
class NeonService:
    def open_map(self) -> dict                    # grid, lon/lat extent, legend, colour counts, TIFF status;
                                                  # keeps the 6000x6000 colour array in self.classes
    def check(self, features, point_mode="pixel", rule="touched", with_tiff=True, save=False) -> dict
    def farm_detail(self, feature, point_mode="pixel", rule="touched") -> dict
                                                  # one farm's problem areas (exact shapes + stencil pixels)
    def saved_farms(self) -> dict                 # plots + their stored plot_results
    def storage(self) -> dict                     # table sizes and rows, TIFF file/raw bytes, corners per colour
    def benchmark(self, sizes=(1, 10, 100, 1000), repeat=3, progress=None, cancel=None) -> dict
    def fidelity(self, progress=None, cancel=None) -> dict
    def inspect(self, lon, lat) -> dict           # what Neon stores for the 256x256 page under a point
    def outlines_in(self, west, south, east, north, limit=5000) -> dict
```

Every public method runs through one wrapper that:
- ends the transaction afterwards (`rollback`, or `commit` after a save), so `SET LOCAL jit = off`
  never carries over and the pooled connection is not held;
- on `psycopg.OperationalError` (Neon asleep or the connection dropped) reconnects once and retries;
- lets `SystemExit` from the backend through as a readable message (the worker shows it in a dialog).

Progress and cancel for `verify()` come from temporarily wrapping `common.Progress`, which `verify()`
already calls once per 256-row strip. No backend file changes.

## Data flow

**Start-up.** The window opens straight away. The worker connects (`common.connect()` reads
`backend/.env`), runs `common.load_grid`, then fetches all stencil pages in one query and unzips them
into a 6000 × 6000 `uint8` colour array (default colour where a page has no row). Colour counts come
from the same array (`np.bincount`). Then it looks for the TIFF and checks its fingerprint
(`common.scan_map`), so the TIFF column can switch on. The map page receives the extent, legend and
counts, and re-locks its view to the extent.

**Tiles.** Leaflet asks for `eudr://tiles/{z}/{x}/{y}.png`. For each of the tile's 256 × 256 pixels,
`render_tile` turns the Web Mercator position into longitude/latitude, then into a map row/column
with the same floor rule as `Grid.point_to_pixel`, and takes that pixel's colour (nearest neighbour,
so classes are never blended). Pixels off the map are transparent. The tile is encoded as a PNG with
the legend palette. No database work per tile.

**Check.** The map sends its farms as a GeoJSON FeatureCollection. The service builds farms with
`common.farms_from_features(features, grid, point_mode)`, then runs, timing each:
`stencil_approach.check(conn, grid, farms, all_touched)`, `polygon_approach.check(conn, grid, farms)`,
and, when the TIFF matches, `common.check_with_tiff(...)` in `"pixel"` mode (with the same stencil
rule) and `"exact"` mode. With
"Save results to Neon" on, it calls `common.save_results` for each method, as the CLI does. Farms that
fail to load (no geometry, unsupported type) are reported by name.

**Focus on one farm** (`farm_detail`, run when a farm is focused, so only for the farm you look at):
- *Exact shapes*: one PostGIS query intersects the farm with `class_polys`
  (`ST_Intersection(r.geom, f.geom)` for every `r` that `ST_Intersects` it; the farm is sent as EWKB,
  as `polygon_approach.check` does). The pieces are merged per colour with shapely (`unary_union`), so
  the 64 × 64 tracing seams disappear, and measured with `common.area_m2`. The default colour is not
  stored, so its part is the farm minus every stored part. That way a map whose most common colour is
  a problem colour still works (`backend/CLAUDE.md`: never hard-code land as the default).
- *Outside the map*: the farm minus `grid.bounds_box()`.
- *Pixels*: `common.farm_pixels(farm, grid, all_touched)` over the in-memory stencil array gives the
  pixels the stencils counted. They are drawn into a small transparent PNG with its bounds. No extra
  query.
- *A point*: the square of the pixel under it, and that pixel's colour.

## Phase 1: colour-coded map

- An EUDR tile layer over the basemap, with an opacity slider (default 70%) in the toolbar.
- A legend control: colour, label and share of the map for each class present.
- The dashed outline and the view lock use the extent from Neon instead of `TOP_LEFT` / `BOTTOM_RIGHT`.
- A "Loading map from Neon…" overlay until the map is ready. If Neon can't be reached, a banner says
  what to check, with a Retry button. The basemap and drawing tools keep working.
- Status bar: `Neon: connected · jambi-v1 · 6000 × 6000 px`.

## Phase 2: testing points and GeoJSONs

- **Point tool** next to the polygon tool: click to drop a point (`Point 1`, `Point 2`…). Points can be
  renamed, deleted, and dragged in Edit mode, like polygons.
- **Open ▾**
  - *GeoJSON file…*: a FeatureCollection, a Feature or a bare geometry. Point, Polygon and MultiPolygon
    are kept; `plot_id` (else `id`) becomes the name and `area_ha` is kept for circle mode. Anything
    else is skipped and listed.
  - *Farms saved in Neon*: loads `plots` and shows their stored `plot_results`.
- **Check ▾**
  - *Check all farms* (Ctrl+Enter) and *Check selected farm*.
  - *Point mode*: pixel (default) / circle. *Stencil rule*: touched (default) / centre.
  - *Compare with TIFF*: on when the TIFF matches, disabled otherwise.
  - *Save results to Neon*: off by default. Saving uses the farm's name as `plot_id`, so a later save
    with the same name replaces the earlier rows, as the CLI does.
- **On the map:** each checked farm is outlined in its verdict colour, with a halo so it stands out on
  any class, and its tooltip shows the verdict. Editing a farm marks its result as out of date (grey)
  until the next check.
- **Results tab** (right panel): one row per farm with name, kind, stencils / outlines / TIFF verdicts,
  an agreement mark, colours touched and forest ha, sorted worst first. Click a row to focus on that
  farm. Below the table, the focused farm's hectares of each colour per method, as a stacked bar.

### Focus on a farm: zoom in, verdict, highlights

After checking a single farm, or when you click a checked farm or its row in Results, the map zooms to
that farm (padded; a point goes to zoom 17) and shows:

```
┌─────────────────────────────── map, zoomed to "Polygon 2" ───────────────────────────────┐
│ ┌─ Polygon 2 ──────────────────────────────┐   ░░░ everything outside the farm dimmed ░░░ │
│ │ ✖ NOT COMPLIANT                           │         ┌──────────────────┐                 │
│ │ Deforestation after 31 Dec 2020 on        │         │   ▨▨▨  red hatch  │ deforestation  │
│ │ 1.24 ha (6.2% of the farm).               │         │        ▧ purple   │ needs review   │
│ │ ███████▒▒▒▓░  land 12.1 · forest 6.6 ·    │         └──────────────────┘                 │
│ │ deforestation 1.24 · needs review 0.10 ha │                                              │
│ │ Stencils FAIL · Outlines FAIL · TIFF ✓    │                                              │
│ │ ( Exact shapes | Pixels )   Next problem ▸ │                                              │
│ └───────────────────────────────────────────┘                                              │
└────────────────────────────────────────────────────────────────────────────────────────────┘
```

- **Verdict card** (top-left of the map), in plain words:

  | Verdict | Headline | Sentence (hectares from the outlines' exact areas) |
  |---|---|---|
  | `FAIL` | ✖ Not compliant | Deforestation after 31 Dec 2020 on X ha (Y% of the farm). |
  | `MANUAL_REVIEW` | ⚠ Needs review | X ha (Y%) may have been cleared after 2020; a person has to check. |
  | `CHECK_LOCATION` | ⚠ Check the location | Y% of the farm is water, so its coordinates are probably wrong. |
  | `CANNOT_CHECK` | ? Cannot check | Part of the farm is outside the map, or X ha has no data. |
  | `PASS` | ✔ Compliant | No deforestation after 31 Dec 2020. Forest inside: X ha (allowed under the EUDR). |

  - For a point, the sentence names the colour under it, for example "The point is on deforestation".
  - The headline uses the worse of the two approaches' verdicts, in the same "worst wins" order as
    `common.decide`. The card lists each approach's verdict and flags any disagreement.
  - A footnote says the verdict comes from this map (cut-off 31 Dec 2020).
- **Highlights:** the parts of the farm that cause the verdict, drawn over the map:
  - always when present: deforestation (red, hatched) and needs review (purple, hatched);
  - also when present: water (blue), no data (black), and any part of the farm outside the map
    (grey, hatched);
  - forest is not highlighted, because it never fails a farm, but its hectares are on the card;
  - hovering an area shows its colour and hectares. Everything outside the farm is dimmed.
- **Exact shapes | Pixels** toggle:
  - *Exact shapes* (default): the farm cut out of Neon's outlines, the same areas the outlines
    approach measures.
  - *Pixels*: what the stencils counted, meaning every pixel the farm touches under the stencil rule,
    with problem colours solid and the rest faint. This shows why the two approaches can differ at a
    farm's edge.
- **Next problem ▸** (key N) steps through the checked farms that are not compliant or need review,
  worst first.
- **Several farms:** after *Check all farms*, the map fits all of them and Results lists them worst
  first. With a single farm, it focuses on that farm straight away.
- **Esc** leaves the focus view.
- **Agreement** follows `compare.py`: stencils are compared with the TIFF pixel rule, outlines with the
  TIFF exact-area rule, each on `PlotResult.key()` (verdict, colours touched, point colour). Without the
  TIFF, the mark compares stencils with outlines on the verdict only, and says so.

## Phase 3: Statistics tab

1. **Your test results** (from the latest check): verdict counts per method (grouped bars); agreement
   tiles (stencils vs TIFF, outlines vs TIFF, stencils vs outlines); time per method (all farms at once
   and per farm); forest summary (points in forest, outlines overlapping forest).
2. **Storage** (live from Neon): size of each approach (stencils = `stencil_pages` + `grid_info`;
   outlines = `class_polys` + `square_labels` + `grid_info`), as `compare.py` counts them, next to the
   TIFF's raw pixels (width × height × 1 byte, from `grid_info`) and, when the TIFF is present, its
   file size; rows per table; ratio tiles (for example
   "stencils: 0.9× the TIFF file"); a colour-mix donut; outline corners per colour.
3. **Speed benchmark** (button): generated farms with a fixed seed, all inside the map: points spread
   evenly at random, and outlines of 0.5–20 ha (irregular polygons: a circle with 6–12 jittered
   corners). For batch sizes 1, 10, 100 and 1,000, each method checks the batch 3 times
   and the median is kept. Output: a line chart of seconds against batch size (log axes), separate for
   points and outlines, and ms per farm. It shows where the outlines start using square labels (200+
   farms). Takes about 1–2 minutes, with progress and Cancel. It is timed over Neon, and the chart says
   so (`backend/CLAUDE.md`: say where numbers come from).
4. **Fidelity vs TIFF** (button; needs the matching TIFF):
   - *Decision fidelity*: the benchmark's 1,000 generated points and 1,000 generated outlines (same
     seed, so the same farms whether or not the benchmark has run) checked by all four methods,
     giving the % of farms whose `key()` matches the TIFF, per approach, split into points and
     outlines.
   - *Pixel fidelity*: `stencil_approach.verify` and `polygon_approach.verify` rebuild all 36 million
     pixels from Neon and compare them with the TIFF. Shows pixels compared, pixels that differ, % and
     time taken. Outlines take about a minute (32 MB to download), with progress and Cancel.

Charts are native Qt widgets drawn with QPainter (`charts.py`), following the `dataviz` skill:
- palette, labels and hover tooltips;
- a "Show table" twin for each chart.

No chart library is needed.

## Phase 4: diagrams

- **How it works** (top of the Statistics tab, static SVG):
  - the data flow: app → `neon_service` → backend modules → Neon tables, and which question each
    table answers;
  - the two encodings: an 8 × 8 square becomes one 64-bit number, and 1,024 numbers per colour are
    zipped into one page row; versus a colour patch traced in 64 × 64 tiles into an outline, stored
    with a GiST index, plus 16 × 16 square labels.
- **Inspect tool** (toolbar): click the map, and the right panel's Inspect tab shows that 256 × 256 page
  three ways, side by side, from real Neon rows:
  - *stencils*: the page rebuilt from its row, with an 8 × 8 grid; hovering a square shows its 64-bit
    number per colour; the page row's zipped size in bytes;
  - *outlines*: the page's outlines drawn with QPainter, with corner dots, count, corners and bytes
    (`ST_MemSize`);
  - *labels*: the 16 × 16 square mosaic (one colour, or hatched for mixed).
- **Stored outlines layer** (Layers ▾): when the visible area is at most about 1.5 million map pixels,
  the outlines in view are fetched (`ST_AsGeoJSON`, capped at 5,000) after each pan or zoom, and drawn
  as thin class-coloured lines. When zoomed further out, a hint says to zoom in.

## Errors

| Situation | Behaviour |
|---|---|
| Backend `SystemExit` message (e.g. no map yet) | Dialog with the backend's own message; the app stays open |
| Neon asleep or connection dropped | Status "Reconnecting…", one retry, then a dialog: check `backend/.env` and that you're online |
| `.env` missing | The backend's message ("DATABASE_URL is not set…") in a dialog, plus the Retry banner |
| TIFF missing or a different map | TIFF column and fidelity buttons disabled, with a note: put the uploaded `my_map.tif` in `backend/data/tiff/` |
| Farm off the map | `CANNOT_CHECK`, note "outside the map" |
| Unsupported geometry in a file | Skipped and listed by name |
| A job while another runs | Queued; the worker runs one job at a time |
| Too many outlines in view | Layer hint "zoom in to see stored outlines" |

## Testing

There is no test suite in the repo. These come with the work:

- **Unit tests** (`frontend/tests/`, `python -m unittest discover frontend/tests`):
  - tile maths: known Web Mercator tiles land on the expected rows/columns, pixels off the map are
    transparent, and the result matches `Grid.point_to_pixel`;
  - PNG encoding and the palette;
  - colour counts from the stencil array;
  - the benchmark generator: every farm is valid and inside the map, and the same seed gives the
    same farms;
  - shaping check results for the pages (verdicts, agreement, hectares);
  - the focus view's pieces: merging parts per colour, the default colour's part (farm minus the
    stored parts) and the outside-the-map part add up to the farm's area; the verdict card gives the
    right headline and sentence for each verdict, for both points and outlines.
- **Neon smoke test** (`python frontend/smoke_neon.py`, read-only, saves nothing):
  - opens the map;
  - takes one pixel of each stored colour from the unzipped stencils and checks those points with
    both approaches; each must return that colour;
  - checks 50 generated outlines, which must give 100% decision fidelity against the TIFF (stencils
    against the pixel rule, outlines against exact areas);
  - finds a deforestation patch in the stencil array and puts a farm over it. The farm must come
    back `FAIL`, its highlighted deforestation hectares must equal the outlines' `deforested_ha`, and
    its pixel view must show deforestation pixels;
  - prints timings and says that they come from Neon.
- **Lint**: `python -m pyflakes frontend` is clean.
- **App walkthrough** after each phase, with a screenshot.
- **Backend untouched**: no files in `backend/` change, so its own checks in `backend/CLAUDE.md` still
  hold.

## Out of scope

- A web API or a browser version (approach B): possible later by wrapping `neon_service.py`.
- Editing the map or rebuilding it from the app (`build` stays CLI-only).
- Writing anything to Neon except "Save results to Neon".
- Offline use: Leaflet and basemap tiles come from the internet, as today.

## Risks

- **Qt widgets over the map view:** checked with a throwaway test on 2026-10-15. A native QFrame
  drawn over the QWebEngineView renders correctly, and `QWidget.grab()` captures both.
- **Memory:** the 6000 × 6000 array is 36 MB. Fine for this map; much bigger maps would need tiles
  rendered page by page from Neon.
- **Outline verify time over Neon:** about a minute. It's behind a button, with progress and Cancel.
- **Timing noise:** ±20% between runs (`backend/CLAUDE.md`). The benchmark reports medians of 3, and
  the charts say the times are over Neon.

---

## Scope narrowed on 2026-10-15

The user restated the goals:
1. Show the whole area, colour-coded, from Neon.
2. Draw a polygon or point and get an EUDR verdict from both approaches.
3. Compare the two approaches' results, speed and query efficiency, separately for "is the point in forest?" and "does the polygon overlap forest?".

Query efficiency is measured on both sides:
- **Client:** round trips, rows, bytes and time over Neon.
- **Server:** `EXPLAIN (ANALYZE, BUFFERS)`, run again and rolled back.

The focus view, Inspect tool, Storage and Fidelity statistics and diagrams in this spec are deferred.
