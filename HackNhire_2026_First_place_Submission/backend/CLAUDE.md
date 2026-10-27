# CLAUDE.md

Student prototype for a SystemEarth challenge, in two steps:
1. **Compaction** (`compaction/eudr_layers.py`, Joe's code). It is copied unchanged from
   github.com/Joeongtengkiat/HacknHire, version 1.1 of 14 Oct 2026.
   - It turns about 20 satellite layers into `evidence.tif` (every EUDR decision fact per pixel,
     17 fields in 32 bits) and `outcome.tif`, in a results folder `layers/<map>/`.
   - Its `--verify` checks them against the original layers.
2. **Storage in Neon** (`database/`). The results folder becomes a map with three approaches:
   - evidence pages: `evidence.tif` itself; checks run Joe's rule;
   - stencils and outlines: the 5 colours (what the app uses).

   They compete on storage, query speed, decision fidelity and pixel fidelity.

`pipeline.py` runs both steps; `ingest_app.py` is a window around it (choose the data, name the map, follow the progress).

Also:
- The brief also asks for an EUDR layer analysis, an encoding strategy and, as a bonus, a cost
  estimate.
- SystemEarth's CTO said a local demo is fine and to containerise it (`Dockerfile`). The stencil
  idea is his.
- `README.md` is the user guide and holds the results.

## Joe's code and the EUDR rule
- **The rule lives only in `eudr_layers.py`** (`decide`, `plot_facts`, `plot_verdict`, `judge`).
  - Never re-implement or edit it here: import it (`layers.eudr_layers`).
  - To take a newer version from Joe, replace the file and run the tests. `layers.py` is the only
    glue: 5 colours, rule parameters, bit layout, grid.
  - Joe's newer versions (1.4+) write `outcome.tif` in the 5 colours already (tag
    `OUTCOME_SCHEME`). `layers.class_map` uses such a file as it is.
- **`layers.five_classes` maps Joe's classes to the 5 colours:**

  | Joe's classes | Colour |
  |---|---|
  | not forest, cleared non-forest | land |
  | forest, rubber-map forest, damaged forest | forest |
  | confirmed | deforestation |
  | probable, needs review | needs review |
  | water | water |
  | no data | 255 (read as no data) |

  This matches Joe's 1.4 legend. It was checked on Jambi: between his 1.0 and 1.4 outputs, land,
  water and the 2020-forest total agree pixel for pixel.
- **The evidence check must give every plot exactly `eudr_layers.query_plots`' 35 outputs.** It
  uses the same `plot_window`, `plot_mask` and `judge`, with row areas from `layers.joe_grid`.
  `tests/test_evidence.py` proves it; keep it that way.

## The map and the rules
Each TIFF pixel holds one value:

| Value | Colour | What it means | Effect on a farm that touches it |
|---|---|---|---|
| 0 | land | Not forest: farmland, grass, bare ground, buildings. Rubber plantations count as farmland under the EUDR, not forest | `PASS` |
| 1 | water | Rivers, lakes, sea. A farm on water probably has wrong coordinates | `CHECK_LOCATION` if the point, or more than half the outline, is water |
| 2 | forest | Forest still standing. EUDR definition: over 0.5 ha, trees over 5 m, canopy cover over 10%, not mainly farmland or town | `PASS`, reported separately (`forest_ha`) |
| 3 | deforestation | Forest turned into farmland after the EUDR cut-off date, 31 Dec 2020 | `FAIL` |
| 4 | needs review | Pixels the map flags as uncertain, so a person has to look | `MANUAL_REVIEW` |
| anything else (e.g. 255) | no data | No information for that pixel; stored as class 5 | `CANNOT_CHECK` |

- The worst colour a farm touches decides its verdict (`common.decide`): `FAIL`, then
  `MANUAL_REVIEW`, then `CANNOT_CHECK` (also for farms outside the map), then `CHECK_LOCATION`,
  then `PASS`.
- **Protected areas.** A farm in a protected area stored with the map (a point inside one, or an
  outline overlapping one by more than the exact-area rule) needs review: a legality risk under the
  EUDR, not proof of deforestation. `protected_areas.check(conn, grid, farms)` finds those farms in one
  query (none for a map without `protected_areas`), and `protected_areas.apply(results, found)` turns
  any verdict below `MANUAL_REVIEW` into `MANUAL_REVIEW`; `FAIL` stays. It sets `PlotResult.protected_areas`
  (names), `protected_ha`, `map_verdict` (the map's own verdict) and the note. It runs after each
  approach's check, the same for stencils, outlines and the TIFF baseline, so their answers stay
  comparable and the approaches' timings don't include it. `compare.py` applies it only to the verdicts
  it saves, after the fidelity figures. Evidence-page checks keep Joe's verdict and reason exactly;
  only their outcome counts protected areas (`evidence_approach.protected_plots` and `outcome`:
  deforestation-free in a protected area → `NEEDS_REVIEW`), kept apart from the rows so they still
  match `eudr_layers.py`'s outputs.
- Forest alone never fails a farm: under the EUDR only deforestation after the cut-off does.
- The two key queries: "is the point in forest?" and "does this outline overlap forest?".
- A point takes the colour of the pixel it lands on.
- Exact-area rule (outlines): a colour counts if its overlap is more than
  max(1e-4 m², 1e-6 × farm area).
- Pixel rule (stencils): `centre` (the default) = the pixel's centre is inside, rasterised with
  `rasterio.features.geometry_mask` exactly as `eudr_layers.plot_mask` (a farm smaller than a pixel
  gets every pixel it touches); `touched` = the pixel overlaps the farm by some area, not just along
  an edge. The stencils and the TIFF pixel baseline both use `common.farm_pixels`, so they always
  look at the same pixels. `--point-mode circle` makes a point a circle of its declared area (4 ha
  if none), built as `eudr_layers.read_plots` builds it; the default checks the pixel under it.
- Decision fidelity compares `PlotResult.key()` = (verdict, colours touched, point colour).
  Stencils are compared with the TIFF using the pixel rule, outlines using exact areas.

## Layout
- `pipeline.py`: compaction -> upload -> check the plots against Neon and compare with `evidence.tif`.
- `ingest_app.py`: the ingest window (PyQt5, in the root `requirements.txt`). It runs `pipeline.py` as a separate process and reads its output (`Step N of 3`, `common.Progress`'s `label: NN.N%` lines, the "Every output the same…" line), so keep those lines' wording when changing them. Its "What the folder must contain" panel (`LAYER_GROUPS`) lists the word each layer's file name must contain, copied from `eudr_layers.DISCOVERY`: change both together. The `protected` row is the exception: it is found by `protected_areas.find_files`, not by `DISCOVERY`. It is the only way maps get into Neon from a window: the frontend no longer uploads.
- `compaction/eudr_layers.py` (Joe's), `compaction/make_test_layers.py` (made-up satellite layers).
- `database/layers.py`: a results folder as the database sees it (`results_folder`, `class_map`,
  `rule_from`, `check_layout`, `joe_grid`). Imports `eudr_layers` (needs scipy and pandas).
- `database/evidence_approach.py`: evidence pages (`build` / `check` / `verify` / `read_plots`).
- `database/common.py`: shared rules, `Grid`, farm loading, results and the TIFF baseline
  (`check_with_tiff`). Imported by the others, never run.
- `database/polygon_approach.py`: outlines in PostGIS (`build` / `check` / `verify`).
- `database/protected_areas.py`: protected areas (WDPA polygons, files named `wdpa*.geojson`), cut to the
  map and stored in its `protected_areas` table (PostGIS). `maps.upload` stores those in a results folder
  (or `protected=[files]`); `pipeline.py` finds them in `--data-dir`, passes them to `eudr_layers.py`
  as `--protected` and saves the cut copy `wdpa_protected_areas.geojson` in the results folder. The app
  draws them, and the stencil and outline checks send farms in them for review (`check` / `apply`, see
  above). The evidence's own `protected` bit comes from Joe's `--protected`.
- `database/stencil_approach.py`: stencils, 8 × 8 bitmaps packed into 64-bit numbers and zipped
  per 256 × 256 page (same commands).
- `database/maps.py`: several maps, one schema each (`list` / `upload` both approaches / `rename` / `delete` /
  `migrate`). `public.maps` is the catalogue.
- `compare/compare.py`: the evaluation. It imports the database modules through `sys.path`.
- `layers/<map>/`: results folders (built, never in Git). There is no `data/` folder: inputs are paths.
- `tests/`: the backend's unit tests (fake connection, made-up layers) and `smoke_pipeline.py`.

## Commands
Run everything from the backend folder with `venv\Scripts\python`. In PowerShell, put paths with
spaces in quotes.
```
python database/test_connection.py                                  # should print "All good"
python database/maps.py list                                         # ALWAYS before writing
python pipeline.py --name NAME --data-dir LAYERS --bbox XMIN YMIN XMAX YMAX --test-plots 1000 --verify
python pipeline.py --name NAME --out-dir layers/NAME --skip-build    # upload a built folder
python database/evidence_approach.py check FARMS.geojson --map NAME
python database/evidence_approach.py verify NAME --map NAME          # results folder by name in layers/
python compare/compare.py NAME --map NAME                            # the evaluation
python -m unittest discover -s tests -t .                            # unit tests (no database)
python tests/smoke_pipeline.py                                       # live, on a throwaway map only
python -m pyflakes database compare pipeline.py tests compaction/make_test_layers.py
```
IMPORTANT: the user's Neon database holds their real maps:
- `Indonesia1` (schema `map_indonesia1`): the Jambi map in 5 colours;
- `outcome (3)` (schema `map_outcome_3`).

Both were uploaded from 5-colour TIFFs, so they have no evidence pages.
- `maps.py delete X` drops map X's schema for good. A `build --map X` replaces its tables.
- Never upload over, rename, build or delete a map you didn't create without asking.
- Test maps get their own name (`zz ...`) and are deleted afterwards. The smoke test does this.

`DATABASE_URL` is read from `backend/.env`, unless it is set in the environment.

## Checking a change
A change is done when:
1. pyflakes is clean, the backend's unit tests pass, and so do the frontend's
   (`frontend\.venv\Scripts\python -m unittest discover -s frontend/tests -t frontend`). The
   frontend's tests check `common`, `maps` and both 5-colour approaches with its own fake
   connection.
2. `tests/smoke_pipeline.py` says "Everything matches". That means:
   - decision fidelity is 100%, both for Neon vs `evidence.tif` and for `evidence.tif` vs the
     original layers;
   - 0 pixels differ for all three approaches.
3. For geometry, rule or speed changes to the 5-colour approaches, it also holds for:
   - a map in metres;
   - `--rule centre --point-mode circle`;
   - a map where forest is the most common colour.

Show the commands and their output as evidence.

## Design invariants
- **Every table is defined in `database/schema.sql`**, in sections (`-- @name`; `{schema}` and `{srid}` filled in by `common.schema_sql`). Never write `CREATE TABLE` / indexes in Python: add or change a section there. `maps.py init` makes the shared parts once (`@shared`: `public.maps`; `@postgis`); uploads stamp out a map's tables from the other sections.
- **One schema per map.**
  - `common.map_schema(name)` gives the schema: `map_` + the lowercase name, with other characters becoming `_`.
  - `public.maps` is the catalogue (name, schema, source file, fingerprint, size).
  - Every query names its tables with `grid.table("...")`, never through `search_path`. A `SET LOCAL` would cost a round trip per transaction, and it doesn't reach `VACUUM`.
  - `load_grid(conn, name)` and `find_map` choose the map.
- **Uploads are atomic.** `maps.upload` builds every approach (stencils, outlines, and for a results folder the evidence pages) in one transaction with `tidy=False`, then runs `VACUUM`. A failure or Ctrl+C leaves nothing behind.
- **Data Neon already holds is not uploaded again.** Before writing, `maps.upload` fingerprints the 5 colours (`common.scan_map`) and, if some map has evidence pages, `evidence.tif` (`evidence_approach.fingerprint_of`, the same digest as `evidence_info.fingerprint`). `find_stored` picks the stored map with the most matching parts; `copy_stored` makes the new map's tables from them inside Neon (`INSERT ... SELECT`, sections from `schema.sql`, in the same transaction), and only the missing parts are built (`build(..., allow_copy=True)` lets `start_build` accept a fingerprint stored under another name). A taken name is still refused. Pass `allow_copy` only when copying: the frontend's tests replace `build` with the documented signature.
- **PostGIS stays in `public`** (`CREATE EXTENSION ... SCHEMA public`), so dropping a map's schema can never remove it.
- **A map's schema always follows its name.** `maps.rename` renames the schema (`ALTER SCHEMA ... RENAME`) and updates `grid_info.map_version`, `plot_results.map_version` and `public.maps`, in one transaction.
- **`maps.delete` is guarded.** It needs the exact name, and drops only a schema that is listed in `public.maps` and matches `^map_[a-z0-9_]+$`.
- Lossless: only the most common colour (`grid_info.default_class`) is left out, and every other
  colour is stored. Never hard-code land as the default.
- `grid_info.fingerprint` identifies the exact map. `compare` refuses a different TIFF; `verify`
  warns.
- Outlines are traced in 64 × 64 tiles (`TRACE_TILE`, measured fastest). Square labels (16 × 16
  pixels, 255 = mixed, zipped per page) are used only for checks of at least `LABELS_FROM` (200)
  farms, and must never change an answer.
- Farm areas come from pyproj (`common.area_m2`); the default colour's area is whatever remains.
- Stencils: each page is its colour codes, one byte per pixel, raw LZMA2 (`stencil_approach.LZMA_FILTERS`: preset 9e, 64 KB dictionary), column `colours`. (The 8 × 8 64-bit bitmaps of the first version were measured 25% bigger on Jambi.)
- Outlines are optional: `maps.upload(..., outlines=False)` / `--no-outlines`. Anything that runs the outlines checks `common.has_tables` first.
- Grids from `load_grid` carry the map's tables (`Grid.tables`); `ensure_built` uses `common.has_tables`, so a check costs no extra round trip.
  Pages that are all the default colour get no row. `STORAGE MAIN` and `VACUUM ANALYZE` after
  upload.
- **Evidence pages:**
  - Each page is zlib level 9 of: the count (`<u4`), then the palette (`<u4`), then the index
    (`u1`, or `<u2` above 256 values).
  - Pages that are all `evidence_info.default_value` get no row. That value is the most common
    one; on a tie, the smallest.
  - A check is 1 round trip: the pages. The map's tables come with `load_grid` (`Grid.tables`, `common.has_tables`), and `evidence_info` is read once per map (`load_info`'s cache).
- **`evidence_info`** keeps the bit layout and rule parameters. `check_layout` refuses evidence from
  another version.
- **`evidence_results`** has no `map_version` column, so a rename doesn't need to touch it.
- **`grid_info.source_file`** is the uploaded file's path inside `layers/` (`common.stored_path`);
  for a file outside `layers/`, just its name.

## Gotchas
- Neon's pooled connection is PgBouncer in transaction mode: keep `prepare_threshold=None`, and
  use `SET LOCAL`, never plain `SET`.
- PostGIS functions declare high costs, so PostgreSQL's JIT compiler can spend ~150 ms on a big
  overlap query that then runs in ~65 ms. `common.no_jit` runs before big queries (50+ outlines or
  800+ points). Profile with `EXPLAIN (ANALYZE)` and costs on: `COSTS OFF` hides the JIT lines.
- PostGIS geometry arrays use `:` as their delimiter, so `%s::geometry[]` with a Python list
  fails. Send binary EWKB as `bytea[]` with `ST_GeomFromEWKB`, as `polygon_approach.check` does.
- Every query is a network round trip to Neon (several milliseconds). Answer all farms in a few
  batched queries (`unnest` arrays), never one query per farm.
- Timings vary by ±20%. Interleave runs, report medians, and say whether numbers come from a
  local database or Neon. Don't publish a speed claim measured any other way.

## What the frontend relies on
`../frontend` (the PyQt app; see `../frontend/CLAUDE.md`) imports these modules through
`frontend/backend.py`. If you rename or change any of the following, update the frontend in the
same change, then run its tests (`frontend\.venv\Scripts\python -m unittest discover -s
frontend/tests -t frontend`), `frontend\smoke_neon.py --map Indonesia1` and, for anything about
maps, `frontend\smoke_maps.py` (writes only a throwaway map).
- `maps`: `list_maps` (its output is unchanged unless `with_evidence=True`), `upload(conn, tif, name, report)` (a TIFF upload is unchanged: 7 steps), `describe`, `rename(conn, name, new_name)` (returns `{old, name, schema}`), `delete`, `UPLOAD_STEPS`, `MAX_PIXELS`, `size_problem`, `MAP_TABLES`.
- `common`:
  - `connect`, `load_grid(conn, name)`, `find_map`, `table_exists`, `scan_map`, `save_results`, `check_with_tiff`, `area_m2`
  - `map_schema`, `MAP_NAME_MAX`, `default_map_name`, `normalise`, `Progress(total, label, report)`
  - `farms_from_features`, `load_farms`, `farm_outside`, `point_result`, `exact_result`
  - `TIFF_DIR`, `PAGE`, `COLOURS`, `VERDICTS`, the class constants, `FOREST_2020` (forest on the cut-off date in the 5 colours: forest, deforestation, needs review)
  - `Grid`: its fields (`x0`, `y0`, `pixel_w`, `pixel_h`, `width`, `height`, `srid`, `default_class`, `fingerprint`, `map_version`, `schema`), plus `table()`, `point_to_pixel`, `pixel_centre`, `inside`, `bounds_box`, `from_dataset` and `==` (which ignores `schema`)
  - `Farm` (`plot_id`, `kind` = `point` / `outline`, `geom`)
  - `PlotResult` (`verdict`, `present`, `point_colour`, `ha()`, `water_share`, `outside`, `note`, `key()`, `outcome`, the two key questions: `in_forest_2020`, `forest_2020_ha()`, and protected areas: `protected_areas`, `protected_ha`, `map_verdict`, `in_protected`)
  - `OUTCOMES`, `OUTCOME_OF`, `outcome_of(verdict)`: every verdict (both vocabularies) → `COMPLIANT`, `NON_COMPLIANT` or `NEEDS_REVIEW`. The app's verdict styles are built from it (`legend.VERDICT_STYLE`)
- Both approaches:
  - `check(conn, grid, farms[, all_touched])` returns `(results in farm order, {"total_s": ...})`.
  - It must accept any connection-like object, because the frontend passes a `MeteredConnection` proxy to count queries.
  - It must only use `conn.cursor()` (as a context manager), `execute`, `fetchall` and `fetchone`.
  - It must also work when given only points or only outlines.
- Both approaches: `ensure_built(conn, grid)`, `build(conn, tif, map_name, report=None, tidy=True)` (returns the `Grid`), `verify(conn, grid, tif)`.
- `stencil_approach`: `page_size`, `unzip_page` (→ the page's colours, height × width uint8), `pack_page`, `fetch_pages`. The frontend rebuilds the whole map from `SELECT page_col, page_row, colours FROM {grid.table('stencil_pages')}`, and pages with no row are `default_class`.
- `maps.upload(conn, source, name, report, outlines=True, protected=None)`; `common.has_tables`, `Grid.tables` (the frontend's `need_outlines` reads it).
- `protected_areas.load(conn, grid)` → a GeoJSON FeatureCollection in lon/lat (properties `wdpa_id`, `name`, `designation`, `iucn_cat`) or None; one query, none when the map has no `protected_areas`. `protected_areas.check(conn, grid, farms)` → `{farm index: Overlap(names, m2)}` and `apply(results, found)` (`neon_service.check` runs them once and applies them to every method). `protected_areas` must never import pandas, geopandas or `eudr_layers`.
- `common.TIFF_DIR` (= `LAYERS_DIR`) plus `grid_info.source_file` is where the frontend looks for the reference TIFF.
- The frontend's environment has no scipy or pandas. So `common`, `maps`, `stencil_approach` and `polygon_approach` must never import `layers`, `evidence_approach` or `eudr_layers` at module level. `maps.upload` imports them only for a results folder.

## Style
- Plain English for a student audience, British/Singapore spelling (colour, metres). Error
  messages say how to fix the problem (`SystemExit` with the next step).
- Keep the code Python 3.10 compatible (no 3.12-only f-string syntax). Don't add dependencies
  beyond the one `requirements.txt` in the HackNHire folder (shared with the frontend; the Docker
  image installs it without the PyQt5 lines).
- When behaviour or numbers change, update `README.md` (steps, options, results, honest notes)
  and the docstring at the top of the script.

## Privacy
- Never commit or print `.env` (it holds the Neon password), or copy it into the Docker image.
- Satellite layers, `layers/`, TIFFs and GeoJSONs are customer data and stay out of Git.
  `.gitignore` ignores all `*.tif`, `*.tiff` and `*.geojson`. Tests make made-up data in temporary
  folders.
