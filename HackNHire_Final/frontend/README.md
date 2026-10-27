# EUDR farm checker: frontend

A desktop app (PyQt5) that reads the EUDR map from Neon, checks farms with both of the backend's storage approaches, and compares them.

- **Stencils** (`stencil_pages`): 8×8 bitmaps, zipped per 256×256 page. The app downloads the pages a farm touches and works out the answer on this computer.
- **Outlines** (`class_polys` + `square_labels`): PostGIS polygons. PostGIS works out the answer inside Neon.

The app calls the backend's own functions (`backend/database/*.py`), so its answers are always the same as the command-line tools'.

## Setup

From the project folder (`HackNHire\`):

```
frontend\.venv\Scripts\python -m pip install -r requirements.txt
```

The app connects with `DATABASE_URL` from `backend\.env`, the same file as the backend (copy `backend\.env.example`; see the main [README](../README.md#step-3-put-the-connection-string-in-backendenv)). To point the app at a different database, copy `frontend\.env.example` to `frontend\.env` instead: it wins over `backend\.env` for the app only. A `DATABASE_URL` set in the terminal wins over both.

## Run

```
frontend\.venv\Scripts\python frontend\app.py
```

1. **The map tab** loads the map chosen in the **Map** dropdown (top left) from Neon: its `grid_info`, then every stencil page in one query. It is drawn colour-coded by class. The legend shows each class's share, and its ticks hide or show a class. If the map was ingested with protected areas (`wdpa*.geojson` files; see `backend/README.md`), they are drawn as dashed yellow outlines, with the area's name, designation and IUCN category on hover. They have their own row in the legend and in **Layers ▾**, and either one hides or shows them. Reading them costs one more query when the map loads.
2. **Drawing farms.** Draw farms with **Polygon (P)** or **Point (O)**, or add them from a file with **Open ▸ GeoJSON file…**.
3. **Checking.** Press **Check (Ctrl+Enter)**. Each farm is checked by both approaches, with these results:
   - the verdict is the worse of the two: Compliant, Not compliant, Needs review, …;
   - each approach's own verdict is shown as an icon;
   - forest and deforested hectares are listed for outlines;
   - **Protected** says whether the farm is in a protected area stored with the map ("yes" for a point, the hectares inside for an outline; the names are in the tooltip). Such a farm needs review even if the map finds no clearing, because farming may not be allowed there. Deforestation still makes it Not compliant. This costs one more query per check, which isn't counted in either approach's numbers.

   Click a row to zoom to the farm.
4. **Stencils vs outlines · last check** compares the two approaches on the farms you just checked. There is one block per question:
   - *Is the point in forest?*: the points, checked as one batch;
   - *Does the outline overlap forest?*: the outlines, checked as one batch.
5. **The Compare tab** runs a benchmark on made-up farms in batches of 1, 10, 100 and 1,000, and draws how each measure grows. The sentence under each heading is worked out from the numbers.

Options under the **Check ▾** arrow:
- how points are checked;
- the stencil rule;
- comparing with the original TIFF, used only when `backend/data/tiff/` holds exactly the map in Neon;
- **Measure server work (EXPLAIN ANALYZE)**;
- **Save results to Neon** (off by default). The results are saved with the map they were checked against.

## Maps in Neon

Neon can hold several maps, for example different areas. Each one is stored in its own schema (see `backend/README.md`, "Several maps").

- **Switching.** The **Map** dropdown, at the top left, lists them.
  - At start-up the app shows the last map you used. On the first run it shows the first map.
  - Choosing another map loads it and clears the check results, the comparison and the Compare tab. Results belong to one map.
  - Farms you drew stay with their map while the app is open: switch back and they return. Use **Export** to keep them for good.
- **Maps ▸ Upload map…** has these parts:
  - **Choosing a TIFF.** The dialog shows its size and coordinate system, or what's wrong with it.
  - **The name.** It starts as the file name, and you can change it.
  - **Uploading.** Both approaches are built in one go. The bar shows the 7 steps: stencils (read, pack, index), outlines (read, trace, index), then tidying up.
  - **Cancel.** It stops the upload, and nothing is saved to Neon; neither is anything if the upload fails.
  - **The limit.** A map can have up to 100 million pixels (e.g. 10,000 × 10,000). The app keeps the shown map in memory.
  - **Keep a copy in backend/data/tiff** (on by default) lets checks compare with the original TIFF.
- **Maps ▸ Rename map…** gives a map a new name. It takes a second: nothing is uploaded again. If it's the map on screen, it stays there with its farms and results. The new name can't be taken, or too close to another map's ("Jambi V1" and "jambi-v1" would be stored the same way).
- **Maps ▸ Delete map…** shows how much space the map uses in Neon and how many saved results it has. You must type the map's exact name before **Delete map** works. It can't be undone.

Uploading, renaming and deleting maps, and **Save results to Neon**, are the only things the app writes.

Command-line helpers:
- `--map NAME` shows this map at start-up.
- `--open FILE --check` adds a GeoJSON file's farms and checks them once the map has loaded.
- `--benchmark` opens the Compare tab and runs the benchmark.
- `--screenshot PATH --after SECONDS` saves a picture of the window, then quits.

## What the numbers mean

Every check is metered by `metering.py`. The approach is handed a connection that logs each query it sends.

| Number | Meaning |
|---|---|
| Time over Neon | Wall time for the approach's batch, measured from this computer. It includes the network. |
| waiting on Neon | The part of that time spent in queries: the network plus the database. |
| computing locally | The rest: unpacking stencil pages, rasterising outlines, adding up areas. |
| Round trips | Queries sent. This includes the "does the table exist?" check each approach makes, and `SET LOCAL jit = off`. |
| Rows / Data returned | What came back from Neon, as libpq received it. |
| Server execution | `EXPLAIN (ANALYZE, BUFFERS)` of the same data queries, run again in a transaction that is rolled back. It is the time spent inside PostgreSQL. **This is a second, warm run.** |
| 8 KB blocks touched | Shared blocks hit plus blocks read in that EXPLAIN. It measures how much of the tables and indexes the query touched. |

Benchmarks report the median of 3 runs. Every run is its own transaction and is rolled back.

## Tests

```
frontend\.venv\Scripts\python -m unittest discover -s frontend/tests -t frontend -v
frontend\.venv\Scripts\python -m pyflakes frontend
```

`frontend\smoke_neon.py` is a read-only check against the real Neon. It checks:
- that a point on each class comes back as that class from both approaches;
- that 20 made-up outlines get the same verdict from both approaches.

It also prints the query-efficiency numbers. Run it from `frontend\`: `.venv\Scripts\python smoke_neon.py --map jambi-v1`. The `--map` can be left out while Neon holds one map.

`frontend\smoke_maps.py` checks several maps end to end. It **writes**, but only to a throwaway map called `zz smoke test`, which it deletes again even if a step fails. It:
1. uploads a small made-up TIFF;
2. opens it and compares every pixel;
3. checks points and outlines with both approaches against the TIFF;
4. runs `verify` with both approaches;
5. cancels a second upload and makes sure nothing was left behind;
6. deletes the test map, and confirms your other maps are untouched.

`--keep` leaves the test map in place, e.g. for screenshots.
