# CLAUDE.md

This is a student prototype for a SystemEarth challenge, in two steps:
1. **Compaction** (`backend/compaction/eudr_layers.py`, Joe's code). About 20 satellite layers become a 32-bit evidence raster (every EUDR decision fact per pixel, 2000–2026) and an outcome map.
2. **Storage in Neon** (`backend/database/`). The compact layers are stored in Neon PostgreSQL in competing ways, and farm plots (GeoJSON points and polygons) are checked against them.

`backend/pipeline.py` runs both steps.

The user's main map in Neon is `Indonesia1` (Jambi, 6,000 × 6,000 pixels, 5 classes, uploaded from Joe's 5-colour `outcome.tif` before the merge, so it has no evidence pages).

| Folder | What it is | Read |
|---|---|---|
| `backend/` | the compaction step (Joe's `eudr_layers.py`), `pipeline.py`, the storage approaches (evidence pages, stencils, outlines), the maps (`database/maps.py`: list / upload / rename / delete / migrate), check, verify, and the evaluation (command line) | `backend/CLAUDE.md`, `backend/README.md` |
| `frontend/` | the PyQt5 desktop app: picks a map from Neon and shows it, draws and checks farms with both approaches, compares speed and query efficiency, renames and deletes maps (it doesn't upload: maps are ingested with `backend/ingest_app.py` or `pipeline.py`) | `frontend/CLAUDE.md`, `frontend/README.md` |
| `docs/superpowers/` | design specs and implementation plans: the frontend link (scope narrowed on 2026-10-15) and several maps (`2026-10-15-multi-map`) | |

Claude Code loads the `CLAUDE.md` of the folder you work in. The rules in each one apply there.

## The approaches in one paragraph each

- **Evidence pages** (`evidence_pages`). This stores Joe's `evidence.tif` itself, in 256 × 256 pages. Each page holds its distinct 32-bit values plus one index per pixel, zipped. A check fetches the pages under all the plots in one query, then runs Joe's own rule on them. So every plot gets exactly `eudr_layers.py`'s answer: the verdict, the reason, the hectares and the years.
- **Stencils** (`stencil_pages`). The map is cut into 256 × 256 pages. Each page holds one bitmap per class: 8 × 8 squares packed into 64-bit numbers, then zipped. A check fetches the pages a farm touches in one query and works out the answer on the client, counting pixels. This approach means few round trips and almost no server work, but a lot of data and local compute.
- **Outlines** (`class_polys`, GiST-indexed, plus `square_labels`). Every class except the most common one is traced into PostGIS polygons. A check asks PostGIS which outline holds a point, or how much area of each class overlaps a polygon. This gives exact areas and tiny replies, at the cost of more server work and a few more round trips.

Stencils and outlines store the outcome in 5 colours. They give the same verdict on every test farm so far: 1,111 of 1,111 in the app's benchmark. The evidence pages give the same 35 outputs as `evidence.tif` on every plot tested, and Joe's `--verify` shows `evidence.tif` matches the original layers.

**Protected areas.** GeoJSON files named `wdpa*.geojson` (WDPA polygons) given at ingest are stored with the map (`protected_areas`, `backend/database/protected_areas.py`), and the app draws them as "Protected areas". A farm in one needs review: `protected_areas.check`/`apply` turn any lesser verdict into `MANUAL_REVIEW` (deforestation stays `FAIL`), the same for stencils, outlines and the TIFF baseline. Evidence-page checks keep Joe's verdict and reason, but their outcome turns a deforestation-free plot in a protected area into needs review.

**Maps.** Each map lives in its own schema (`map_<name>`, e.g. `map_indonesia1`). The schema holds `grid_info`, each approach's tables, `plots`, `plot_results` and `evidence_results`. `public.maps` lists the maps. Every query names its tables with the schema (`grid.table(...)`). An upload whose data is already in Neon under another name (same fingerprint) copies those stencils, outlines and evidence pages inside Neon instead of uploading them again, and builds only what is missing.

## Rules for the whole project

- **The user's Neon database holds their real maps**: `Indonesia1` (schema `map_indonesia1`) and `outcome (3)` (schema `map_outcome_3`).
  - Never build, upload over, rename or delete them, or write anything else to their tables, without asking first.
  - Run `backend\venv\Scripts\python backend\database\maps.py list` before writing.
  - Test uploads go into a map of their own (e.g. `--name test`) and are deleted afterwards (`backend/tests/smoke_pipeline.py` does this).
  - The app writes only for Maps ▸ Rename map…, Delete map… (both ask first) and Check ▸ Save results to Neon. Maps are ingested with the backend's ingest window (`backend/ingest_app.py`, which runs `pipeline.py`).
- **Never commit, print or log `.env` or `DATABASE_URL`.** The file is `backend/.env`, and both halves read it through `common.connect()`.
- **Satellite layers, TIFFs and GeoJSONs are customer data.**
  - There is no `data/` folder. Inputs are given by path, and built maps go to `backend/layers/<map>/`.
  - All of these stay out of Git: `.gitignore` ignores `backend/layers/` and every `*.tif`, `*.tiff` and `*.geojson`.
  - Tests make made-up data in temporary folders.
- **The backend is the single source of truth for the EUDR rules**, and Joe's `eudr_layers.py` for the multi-source rule. Nothing re-implements them; the frontend imports the backend. A backend interface change updates the frontend in the same change (see "What the frontend relies on" in `backend/CLAUDE.md`).
- **Neon is pooled (PgBouncer, transaction mode).**
  - Keep `prepare_threshold=None` and use `SET LOCAL` only.
  - End every transaction.
  - Batch farms into a few queries, never one query per farm.
- **Speed claims must be honest.** Give medians of several runs, say "over Neon" (or local), and label EXPLAIN numbers as warm re-runs.
- **Wording:** plain English and British spelling (colour, metres). Error messages say how to fix the problem.
- **Dependencies:** one `requirements.txt`, in this folder, for the backend, the ingest window and the app. Install it into each venv; add nothing beyond it. The Docker image (`backend/Dockerfile`, built from this folder) installs it without the PyQt5 lines.
- **Commits:** this folder is a git repository (branch `main`, remote `origin` = github.com/BruhClient/HackNHire-System-Earth, private). Commit only when the user asks. `.gitignore` keeps out `.env`, the venvs and every real TIFF and GeoJSON; check `git status` before committing.

## Quick commands (from `HackNHire\`, PowerShell)

```
frontend\.venv\Scripts\python frontend\app.py                                      # the app
frontend\.venv\Scripts\python -m unittest discover -s frontend/tests -t frontend   # frontend tests (they use backend modules too)
cd backend; venv\Scripts\python -m unittest discover -s tests -t .; cd ..          # backend tests (no database)
backend\venv\Scripts\python backend\database\maps.py init                          # once, on a fresh database (tables: backend\database\schema.sql)
backend\venv\Scripts\python -m pip install -r requirements.txt                    # setup (same for frontend\.venv)
backend\venv\Scripts\python backend\ingest_app.py                                 # ingest a map: the window
backend\venv\Scripts\python backend\database\maps.py list                          # the maps stored in Neon
cd backend; venv\Scripts\python pipeline.py --name NAME --data-dir LAYERS --bbox XMIN YMIN XMAX YMAX --test-plots 1000 --verify; cd ..
cd backend; venv\Scripts\python compare\compare.py NAME --map NAME; cd ..          # the evaluation of a built map
cd backend; venv\Scripts\python tests\smoke_pipeline.py; cd ..                     # whole chain, on a throwaway map
cd frontend; .venv\Scripts\python smoke_neon.py --map Indonesia1; cd ..            # read-only Neon check
cd frontend; .venv\Scripts\python smoke_maps.py; cd ..                             # several maps, on a throwaway map
```
