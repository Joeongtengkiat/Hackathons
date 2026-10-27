# Several maps in Neon: design

**Date:** 2026-10-15
**Status:** approved in chat. The user chose approach A and said "stop asking me", so the remaining sections were decided without a review gate.

## Goal

Upload several EUDR TIFFs to Neon from the app, give each one a name, and switch between them with a dropdown. Uploading builds both storage approaches (stencils and outlines), so the comparison works on every map.

## What the user decided

| Question | Answer |
|---|---|
| What are the TIFFs? | **Different areas** (e.g. Jambi, Riau). They don't cover the same ground. |
| Where do uploads happen? | **In the app**, from an "Upload map…" action with a progress bar. |
| Can maps be deleted? | **Yes**, after the user types the map's name to confirm. |
| How are maps kept apart in Neon? | **A: one PostgreSQL schema per map.** |

## 1. Layout in Neon

```
public
  maps                  catalogue: one row per map (the dropdown reads this)
  (PostGIS stays installed here)
map_jambi_v1            the existing map, moved here without re-uploading
  grid_info · stencil_pages · class_polys · square_labels · plots · plot_results
map_<name>              each new upload gets the same six tables
```

**Folder names**
- A map's name becomes its schema: `map_` + the lowercase name, with every run of characters other than letters and digits replaced by `_`, trimmed of `_` at both ends, and cut to 63 characters. So "Jambi v1" and "jambi-v1" both become `map_jambi_v1`.
- A name may be 1–40 printable characters and must contain at least one letter or digit.
- Two names that would share a schema are refused with a clear message.

**`public.maps`** has these columns: `name` (primary key, as typed), `schema_name` (unique), `source_file`, `fingerprint`, `width`, `height`, `added_at`. It is written in the same transaction as the map's `grid_info`.

**Table names include their schema**, e.g. `map_jambi_v1.stencil_pages`, and are never resolved through `search_path`. Reasons:
- `SET LOCAL search_path` would add a round trip to every transaction (four per Check).
- `VACUUM` runs outside a transaction, where `SET LOCAL` has no effect.
- A query can never read the wrong map by accident.

The schema travels on the `Grid` that every backend function already receives: a new `Grid.schema` field, plus `Grid.table(name)`. The SQL text and round-trip counts are otherwise unchanged, so benchmark numbers stay comparable with earlier runs.

**Farms and saved verdicts** (`plots`, `plot_results`) live in each map's schema. Deleting a map deletes them too.

**Safety rails**
- PostGIS is created with `SCHEMA public`, so dropping a map schema can never remove the extension.
- Delete only drops a schema that is listed in `public.maps` and matches `^map_[a-z0-9_]+$`.

## 2. Backend changes (`backend/database`)

**`common.py`**
- `Grid` gains `schema: str = ""` (excluded from `==`) and `table(name)`. An empty schema means unqualified names, so code that builds a `Grid` by hand keeps working.
- `map_schema(name)` returns the schema name for a map name, and stops with a message if the name is not allowed.
- `start_build(conn, src, tif_path, map_name, other_tables, report=None)`:
  1. scans the TIFF;
  2. refuses a TIFF whose fingerprint is already stored under another name ("already stored as 'x'");
  3. refuses a name whose schema belongs to another map;
  4. creates the schema and its shared tables;
  5. if this map previously held a different TIFF, drops the other approach's tables, but only within this map's schema;
  6. calls `save_grid`.
- `save_grid` writes `<schema>.grid_info` and upserts the `public.maps` row.
- `load_grid(conn, name=None)` finds the map in `public.maps`, matching its name or its schema:
  - with no name and exactly one map stored, it uses that map;
  - with no name and several maps, it stops with "choose one with `--map`";
  - if `public.maps` is missing but `public.grid_info` exists, it stops with "run `python database/maps.py migrate`".
- `save_results`, `print_storage` and `SHARED_SCHEMA` use qualified names. `SHARED_SCHEMA` becomes `map_tables_sql(schema)`.
- `Progress(total, label, report=None)` calls `report(label, done, total)` on every step. `scan_map` takes `report=None` too.

**`stencil_approach.py`, `polygon_approach.py`**
- Every table reference goes through `grid.table(...)`.
- `ensure_built(conn, grid)`.
- `build(conn, tif, map_name, report=None, tidy=True)` returns the `Grid`. `tidy=False` skips the commit and `VACUUM`, so `maps.upload` can make both builds one transaction.
- The command line uses `--map NAME` in place of `--version`. The default name for `build` is the TIFF's file name without the extension.
- `polygon_approach` creates PostGIS with `CREATE EXTENSION IF NOT EXISTS postgis SCHEMA public`.

**New `maps.py`**
- `list_maps(conn)` returns a list of dicts: name, schema, source_file, width, height, added_at, stencils (bool), outlines (bool).
- `upload(conn, tif, name, report=None)`:
  1. checks the name and size, allowing at most 100,000,000 pixels;
  2. runs both builds with `tidy=False`, then one commit;
  3. on any error or cancel, rolls back, so nothing is left behind;
  4. after the commit, runs `VACUUM ANALYZE` on the three map tables.
- `describe(conn, name)` returns the bytes used in Neon and the number of saved results. The delete dialog uses it.
- `delete(conn, name)` drops the schema (`CASCADE`) and deletes the catalogue row in one transaction.
- `migrate(conn, name=None)` handles the existing map:
  1. moves the six old `public` tables into `map_<name>` with `ALTER TABLE … SET SCHEMA`;
  2. adds the catalogue row;
  3. does all of this in one transaction.

  The name defaults to the old `grid_info.map_version`, which is `jambi-v1`.
- Commands: `list`, `upload TIF --name NAME`, `delete NAME --yes`, `migrate [--name NAME]`.

**`compare/compare.py`** gains `--map NAME`.

## 3. Upload in the app

Maps ▸ **Upload map…** opens a dialog with:
- a file field with Browse…;
- a Name field, prefilled with the file name;
- a line describing the TIFF (pixels and EPSG code), or its problem (rotated, too big, unreadable);
- a check of the name against the maps already listed;
- a "Keep a copy in backend/data/tiff" checkbox, on by default. The copy lets checks compare against the original TIFF.

Upload runs `NeonService.upload_map` on the worker thread. The bar shows overall progress across seven steps. The text gives the current one, e.g. "Outlines · tracing outlines · 41%". Cancel sets the worker's cancel event. The next progress report raises `Cancelled`, the transaction rolls back, and the dialog says "Cancelled. Nothing was saved to Neon." An upload is never retried on reconnect: `_run(..., retry=False)`.

When the upload succeeds, the dialog closes, the map list refreshes, and the new map is selected and loaded.

## 4. The dropdown and switching

At the far left of the toolbar: **Map [name ▾]** and a **Maps ▾** button with Upload map…, Delete map… and Refresh list.

**At start-up:**
1. List the maps.
2. Pick one, in this order:
   - the `--map NAME` command-line flag;
   - the last map used (stored in `QSettings`);
   - the first map alphabetically.
3. Load it.

If Neon holds no maps, a banner says so and offers an **Upload map…** button.

**Switching from map A to map B:**
1. Cancel a running benchmark.
2. Remember A's drawn farms (in memory, while the app is open).
3. Emit `map_switching(A, B)`. In response:
   - the check results clear;
   - the results panel clears;
   - the Compare tab resets.
4. Clear the farms from the map.
5. Load B.
6. When B's `map_loaded` fires, put back any farms remembered for B.

The tile URL carries the map's schema (`?map=…`), so tiles cached for one map are never shown for another. Rapid switching only loads the last map chosen: the `open_map` jobs coalesce.

## 5. Deleting

Maps ▸ **Delete map…** opens a dialog with:
- a list of maps, with the current one preselected;
- a details line from `describe_map`: MB used in Neon, saved results, source TIFF;
- a box where the user must type the map's exact name before Delete is enabled.

After the delete:
- the list refreshes;
- if the shown map was deleted, the app loads another map, or shows the "no maps" banner;
- that map's remembered farms are dropped.

## 6. Existing map

`maps.py migrate` is run once:
1. **Before:** record the row counts and fingerprint of the public tables.
2. **After:**
   - the counts match in `map_jambi_v1`;
   - `maps.py list` shows `jambi-v1`;
   - `smoke_neon.py` passes.
3. **Undo:** move the tables back with `ALTER TABLE map_jambi_v1.<table> SET SCHEMA public`, then delete the catalogue row.

## 7. Errors (say how to fix)

| Case | Message |
|---|---|
| Name taken | "There's already a map called 'x'. Choose another name, or delete that map first." |
| Name clash | "'Jambi V1' is too close to 'jambi-v1': both would be stored as map_jambi_v1. Choose another name." |
| Same TIFF twice | "This TIFF is already stored as 'jambi-v1'." |
| Too big | "This map is W × H pixels. The app keeps the whole map in memory, so it supports up to 100 million pixels (e.g. 10,000 × 10,000). Crop it into smaller areas first." |
| Old layout | "Your map is stored the old way (one map in the public schema). Move it into the new layout first. It takes a second and nothing is re-uploaded: `python database/maps.py migrate`" |
| Neon full | the database error, plus "Neon's free plan holds 0.5 GB; delete a map you no longer need." |

## 8. Testing

- **Unit tests** (frontend suite, using fakes):
  - name → schema rules;
  - `Grid.table`;
  - `load_grid` choosing a map;
  - `upload` rolling back on failure or cancel, and tidying after commit;
  - the delete guard;
  - `NeonService` upload/delete/list;
  - upload dialog validation;
  - the delete confirmation;
  - choosing the start-up map;
  - tile URLs carrying the map key.
- **Live, read-only:** `smoke_neon.py --map jambi-v1` after the migration.
- **Live, writes only to a throwaway map:** `smoke_maps.py`:
  1. make a small synthetic TIFF elsewhere on Earth and upload it;
  2. list it;
  3. open it, and compare the class array with the TIFF;
  4. check points and polygons with both approaches against the TIFF;
  5. run `verify` with both approaches and expect 0 differing pixels;
  6. test that cancel leaves nothing behind;
  7. delete it.

  The script cleans up in `finally`.
- **Backend:** pyflakes is clean. `compare.py --map <test map> --point …` agrees with the TIFF.
- **UI:** screenshots of the toolbar, the upload dialog and a switched map.

## Out of scope

- Maps that overlap, and choosing a map automatically from a farm's location.
- Renaming a map.
- Saving the remembered farms across app restarts.
- Comparing two maps against each other.
