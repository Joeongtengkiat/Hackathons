# EUDR layers: from satellite data to a queryable map in Neon

One backend, two steps:

1. **Compaction** (`compaction/eudr_layers.py`, Joe's code). About 20 satellite layers become **one
   32-bit number per pixel**, `evidence.tif`. The layers include JRC forest 2020, Hansen tree loss,
   JRC TMF yearly change 2020–2025, RADD radar alerts, optical alerts and the rubber map.
   - **What each number holds:** every fact the EUDR decision needs. That means forest on 31 Dec
     2020, forest type, the year each source records a clearing, alerts, rubber and water.
   - **Time:** kept as events (the first clearing year per source), not one map per year.
   - **The rule:** the EUDR rule turns the evidence into `outcome.tif`.
   - **The proof:** Joe's `--verify` shows the compact layers give every test plot exactly the
     answers the original layers give.
2. **Storage and queries in Neon** (`database/`). The compact layers go into a Neon PostgreSQL
   database, three ways:
   - **Evidence pages** (`database/evidence_approach.py`). This stores `evidence.tif` itself, in
     zipped 256 × 256 pages. A check runs Joe's own rule on the pages under each plot. So every plot
     gets exactly what `eudr_layers.py` gives it:
     - the verdict and the reason;
     - the hectares of 2020 forest and of each kind of clearing;
     - the years.
   - **Stencils** (`database/stencil_approach.py`, Matthew's 8 × 8 bitmap idea) and **outlines**
     (`database/polygon_approach.py`, PostGIS shapes). These store the outcome in 5 colours: land,
     water, forest, deforestation and needs review. They answer the fast "is it in forest?"
     questions, and the app uses them.

`pipeline.py` runs everything in one command. **Nothing is lost:** each approach's `verify`
rebuilds the whole map from Neon and matches the file bit for bit.

---

## Folder structure

```
backend/                         <- run every command from here
├── .env                         <- YOU CREATE THIS: your Neon connection string (never in Git)
├── pipeline.py                  <- the whole chain: compaction -> upload -> check
├── ingest_app.py                <- the same chain in a window: choose the data, name the map, watch it run
├── Dockerfile                   <- (the packages are in ../requirements.txt, one file for the whole project)
├── compaction/                  <- step 1
│   ├── eudr_layers.py           <- Joe's code: satellite layers -> evidence.tif, outcome.tif, ...
│   └── make_test_layers.py      <- made-up satellite layers, to try everything without real data
├── database/                    <- step 2: everything that talks to Neon
│   ├── test_connection.py       <- check Neon is reachable
│   ├── maps.py                  <- several maps: list / upload / rename / delete / migrate
│   ├── layers.py                <- reads a results folder for the database (5 colours, the rule)
│   ├── evidence_approach.py     <- evidence pages: build / check / verify
│   ├── stencil_approach.py      <- stencils: build / check / verify
│   ├── polygon_approach.py      <- outlines: build / check / verify
│   ├── protected_areas.py       <- protected areas (wdpa*.geojson) stored with a map: add / show
│   ├── common.py                <- shared rules (used by the others, don't run it)
│   └── results/                 <- verdict CSVs (created automatically)
├── compare/
│   ├── compare.py               <- the evaluation: Neon vs the files vs the original layers
│   └── results/                 <- comparison CSVs (created automatically)
├── tests/                       <- unit tests, and smoke_pipeline.py (a live run on a throwaway map)
└── layers/<map>/                <- what step 1 builds, one folder per map (created; never in Git)
```

There is no `data/` folder any more:
- Your satellite layers stay wherever they are; you give their folder with `--data-dir`.
- The compact layers go to `layers/<map>/`.
- A file can be given by its full path or its path inside `layers/`, e.g. `jambi/test_plots.geojson`.

---

## How each approach stores the map

| | Evidence pages | Stencils | Outlines |
|---|---|---|---|
| Stores | `evidence.tif`: all 17 facts per pixel, 32 bits | the 5 colours | the 5 colours |
| Idea | Each 256 × 256 page holds its distinct values (about 25), plus one byte per pixel pointing to one; zipped | Per colour, each 8 × 8 square of yes/no pixels becomes one 64-bit number; each page is zipped | Each patch of colour is traced into a shape that follows the pixel edges (64 × 64 tiles), plus square labels |
| Left out | Pages that are all the most common value | The most common colour | The most common colour |
| Tables | `evidence_pages`, `evidence_info` (bit layout, rule settings) | `stencil_pages` | `class_polys` (spatial index), `square_labels` |
| A check | One query fetches the pages under all the plots; Joe's rule runs on them | One query fetches the pages; pixels are counted | PostGIS measures exact overlaps |
| Answers | Joe's verdict and reason, hectares of each kind of clearing, the years | Colours touched, hectares, verdict | Colours touched, exact hectares, verdict |
| Plot rule | Pixels whose centre is inside (Joe's rule) | Pixels the plot overlaps (or `--rule centre`) | Exact areas |

**Why not outlines for the evidence?**
- A 32-bit value has thousands of possible combinations, so it would trace into far more shapes
  than 5 colours do.
- A page of evidence holds only about 25 distinct values, so the palette pages are small. On a map
  made from Jambi's real outcome, they were half the size of `evidence.tif`.
- They also decode in about 0.5 ms a page.

---

## Step 0: One-time setup

1. Install **Python 3.10 or newer**.
2. Open a terminal in the `backend` folder.
3. Create a virtual environment and activate it:
   - Windows: `python -m venv venv`, then `venv\Scripts\activate`
   - macOS / Linux: `python3 -m venv venv`, then `source venv/bin/activate`
4. Install the packages from the project's one list, in the HackNHire folder: `pip install -r ..\requirements.txt`
   (macOS / Linux: `../requirements.txt`)

## Step 1: Connect to Neon (`.env`)

1. Go to [console.neon.tech](https://console.neon.tech) and create a project in the **AWS Asia
   Pacific (Singapore)** region.
2. Click **Connect** and copy the connection string.
3. Create `.env` in the **backend folder**, with this one line and no quotes (use your own string):
   ```
   DATABASE_URL=postgresql://neondb_owner:abc123@ep-cool-name-123456.ap-southeast-1.aws.neon.tech/neondb?sslmode=require
   ```
4. Test it: `python database/test_connection.py` should say `All good`.

## Step 2: Build, upload and check a map (`pipeline.py`)

```
python pipeline.py --name jambi --data-dir D:\eudr\inputs --bbox 103.0 -2.3 103.5 -1.8 --test-plots 1000 --verify
```

1. **Compaction.** `eudr_layers.py` reads the satellite layers in `--data-dir` for the box. It
   finds them by their file names (see the top of `compaction/eudr_layers.py`). It writes
   `layers/jambi/`:
   - `evidence.tif`, `outcome.tif` and `outcome_simple.tif`;
   - 1,000 test plots;
   - with `--verify`, the check against the original layers (`fidelity_plots.csv`, `summary.json`).
2. **Upload.** The folder goes to Neon as the map `jambi`, with the evidence pages, the stencils
   and the outlines, in one transaction. If anything fails, or you press Ctrl+C, nothing is left
   behind.
   - **Already in Neon?** If a stored map has the same pixels in the same place (same
     fingerprint), its stencils, outlines and evidence pages are copied inside Neon instead of
     being uploaded again; only what it lacks is built and uploaded (e.g. the evidence pages of a
     map first uploaded from a TIFF). On the Thai test map, a second upload of the same folder took
     0.7 s instead of 5.2 s (one run each, over Neon). A name that is already taken is still refused.
   - **Protected areas.** Put the WDPA polygons in `--data-dir` as GeoJSON whose file name starts
     with `wdpa` (any case), e.g. `WDPA_Oct2026_Public_IDN-polygons.geojson`. Several files are
     fine. The pipeline finds them by itself and:
     - gives them to `eudr_layers.py` as `--protected`, so the evidence's `protected` fact is set;
     - saves a copy cut to the map as `layers/jambi/wdpa_protected_areas.geojson`;
     - stores them with the map in Neon (`protected_areas`, PostGIS), in the same transaction.
       The app draws them over the map as "Protected areas", and every check sends a farm in
       one for review (see **Verdict** below).

     WDPA points (areas with no boundary) are skipped. To add protected areas to a map already in
     Neon: `python database/protected_areas.py add "MAP" wdpa_FILE.geojson` (this replaces the
     map's own protected areas). `maps.py upload` takes `--protected FILE` too, and uploads the
     `wdpa*.geojson` files of a results folder by itself.
3. **Check.** The test plots (and `--plots FILE`) are checked against Neon, and the verdicts are
   saved there. Every answer is compared with `eudr_layers.py`'s own answer from `evidence.tif`.

Options:
- Every option the script doesn't know goes to `eudr_layers.py`: `--plots`, `--grid 30m`,
  `--protected FILE`, and so on (see `python compaction/eudr_layers.py --help`).
- `--skip-build` uploads a folder that is already built.
- `--no-upload` stops after step 1.
- To run step 1 without a database:
  `python compaction/eudr_layers.py --data-dir ... --bbox ... --out-dir layers/jambi`

**Try it without real data.** Make up some satellite layers, then run the pipeline on them:

```
python compaction/make_test_layers.py C:\temp\test_layers        # prints the --bbox to use
python pipeline.py --name test --data-dir C:\temp\test_layers --bbox <the printed box> --test-plots 300 --verify
python compare/compare.py test --map test
python database/maps.py delete test --yes
```

## Step 3: Check more plots (`check`)

```
python database/evidence_approach.py check farms.geojson --map jambi    # Joe's verdict, reason, hectares, years
python database/stencil_approach.py check farms.geojson --map jambi     # the 5 colours
python database/polygon_approach.py check farms.geojson --map jambi
```

- **Plot formats.** Plots can be in the EU Information System format (`ProductionPlace`, `Area`),
  or have `plot_id` and `area_ha`.
- **Points.** The evidence check treats a point as a circle of its declared area (4 ha if none is
  given), as the EU system does. The stencils and outlines check the pixel under it.
- **Where results go:**
  - the plots go to the map's `plots` table;
  - evidence verdicts go to `evidence_results` (verdict, reason, every figure as JSON);
  - 5-colour verdicts go to `plot_results`;
  - CSV copies go to `database/results/`.

## Step 4: Prove nothing was lost, and compare (`verify`, `compare`)

```
python database/evidence_approach.py verify jambi --map jambi                # all 32 bits, every pixel
python database/stencil_approach.py verify jambi/outcome_5class.tif --map jambi
python database/polygon_approach.py verify jambi/outcome_5class.tif --map jambi
python compare/compare.py jambi --map jambi
```

`compare.py` with a results folder reports:
- **Part 1, the 5 colours.** Stencils and outlines vs the TIFF, with decision fidelity and speed.
  "Is the point in forest?" and "does the outline overlap forest?" are reported separately.
- **Part 2, the evidence:**
  - decision fidelity of Neon vs `evidence.tif` (every output of every plot);
  - decision fidelity of `evidence.tif` vs the original layers (from `--verify`);
  - speed of Neon, of `evidence.tif` on disk and of the original layers;
  - pixel fidelity of the evidence pages, and what `evidence.tif` keeps of each original layer.
- **Storage.** Sizes from the original layers, through `evidence.tif`, to each approach in Neon.

Other uses:
- `--point LON LAT` tests single points against the 5 colours.
- For a map uploaded from a 5-colour TIFF (e.g. by the app), run
  `python compare/compare.py my_map.tif farms.geojson --map NAME`.

## Several maps

Each map lives in its own **schema** named after it (`jambi` -> `map_jambi`), listed in
`public.maps`:

```
python database/maps.py list                            # every map: schema, size, source, approaches
python database/maps.py upload layers/riau --name riau  # a results folder: all three approaches
python database/maps.py upload riau.tif --name riau     # a 5-colour TIFF: stencils and outlines only
python database/maps.py rename riau "Riau 2024"         # its schema is renamed too
python database/maps.py delete riau --yes               # drops the map's schema: can't be undone
```

- With more than one map stored, give `--map NAME` to `check`, `verify` and `compare`.
- The app (`../frontend`) uploads 5-colour TIFFs, and switches, renames and deletes maps.
- A database set up before several maps were supported is moved once with `maps.py migrate`.

## Look at the data in Neon

```sql
SELECT * FROM public.maps;
SELECT bit_layout, rule_params, default_value FROM map_jambi.evidence_info;
SELECT plot_id, verdict, reason, facts->>'confirmed_ha', facts->>'first_deforestation_year'
FROM map_jambi.evidence_results ORDER BY verdict;
SELECT pg_size_pretty(pg_total_relation_size('map_jambi.evidence_pages'));
```

---

## The live test (`tests/smoke_pipeline.py`)

This test runs the whole chain on a made-up 600 × 600 map with 300 test plots, over Neon, on
16 Oct 2026:

- **Decision fidelity:**
  - Neon vs `evidence.tif`: 300 of 300 plots were identical in all 35 outputs.
  - `evidence.tif` vs the original layers: 300 of 300 were identical.
- **Pixel fidelity:** the evidence pages, the stencils and the outlines each rebuilt 360,000 pixels
  with 0 differences.
- **Speed** (one run, not a median):

  | | 300 plots at once | One plot alone |
  |---|---|---|
  | Neon evidence pages | 0.47 s | 24 ms |
  | `evidence.tif` on disk | 0.38 s | 10 ms |
  | Original layers (14 files) | – | 5.3 ms a plot, read locally |

On so small a map, Neon's table sizes are mostly PostgreSQL's minimum page sizes, so they say
nothing about storage. For storage, use `compare.py` on a real results folder.

---

## How a farm is checked (stencils and outlines)

1. **Outside the map?** → `CANNOT_CHECK`.
2. **Detailed check:** all farms are answered together, not one query per farm.
   - *Stencils:* one query fetches the pages under the farms by their position. They are
     unzipped and compared bit by bit.
   - *Outlines:* one query for the points and one for the outlines. PostGIS finds the shapes
     near each farm (spatial index) and measures the overlap. In a batch of 200+ farms, the
     square labels are read first, and farms sitting entirely in one colour skip the geometry.
3. **Verdict** (the worst colour wins):

| Verdict | When |
|---|---|
| `FAIL` | touches deforestation |
| `MANUAL_REVIEW` | touches needs review |
| `CANNOT_CHECK` | outside the map, or touches no-data pixels |
| `CHECK_LOCATION` | point on water, or outline more than half water: coordinates are probably wrong |
| `PASS` | only land and/or forest |

A farm in a protected area stored with the map (a point inside one, or an outline overlapping
one by more than a sliver) becomes `MANUAL_REVIEW` if the map alone would give it a lesser
verdict; `FAIL` stays `FAIL`. Under the EUDR such a farm is a legality risk (is farming allowed
there?), not proof of deforestation. This is one extra query per check, the same for both
approaches and not counted in their timings. The CSV and the saved note give the areas' names and
the hectares inside them (`in_protected_area`, `protected_ha`, `protected_areas`). Evidence-page
checks keep Joe's verdict and reason, but their outcome counts protected areas too: deforestation
→ non-compliant; deforestation-free in a protected area → needs review; deforestation-free and in
none → compliant (saved in `evidence_results.facts` with `protected_areas`, and in the CSV).

Forest doesn't fail a farm under the EUDR, since only *deforestation* after 31 Dec 2020 does.
It is reported separately: `touches_forest`, `forest_ha` and `point_colour`.

## Options

| Option | Command | Meaning |
|---|---|---|
| `--name NAME` | `pipeline.py`, `maps.py upload` | The map's name in Neon (`maps.py`'s default: the folder's or TIFF's name). `pipeline.py` also uses it for the folder `layers/<name>` |
| `--skip-build` / `--no-upload` | `pipeline.py` | Upload a folder that is already built / stop after building it |
| `--min-plot-ha HA` | evidence `check`, `compare`, `pipeline.py` | Ignore clearing of at most this many hectares per plot, as `eudr_layers.py` does (default 0: the EUDR has no tolerance) |
| `--csv-evidence FILE` | `compare` | Where to write part 2's per-plot table |
| `--map NAME` | `build`, `check`, `verify`, `compare` | Which map: the one to build, or the one to check against when several are stored |
| `--rule touched` / `centre` | stencil `check`, `compare` | Which pixels count as inside an outline: every pixel it overlaps (cautious, default) or only pixels whose centre is inside |
| `--point-mode pixel` / `circle` | `check`, `compare` | A point is checked against the pixel under it (default), or turned into a circle with its declared area |
| `--point LON LAT` | `compare` | Test one point, longitude first. Repeat it for more points |
| `--repeat N` | `compare` | Timing runs per method; the median is reported (default 3) |
| `--single N` | `compare` | How many farms per question are also timed one at a time (default 20) |
| `--skip-verify` | `compare` | Skip the pixel-by-pixel rebuild, which is slow on a big map |
| `--csv FILE` | `check`, `compare` | Where to write the CSV (default: the `results/` folder next to the script) |

Every script explains itself with `--help`, e.g. `python database/stencil_approach.py check --help`.

## Troubleshooting

| Message | Fix |
|---|---|
| `DATABASE_URL is not set` | `.env` is missing or not in the backend folder. It must sit next to `README.md`. |
| `Note: using .env.txt` | Windows Notepad added `.txt`. It still works, but turn on "File name extensions" in File Explorer and rename it to `.env`. |
| `Your .env still has the example connection string` | Paste your own string from Neon's **Connect** button. |
| `Could not connect to the database` | Copy the string again from Neon. Keep `?sslmode=require` and check you're online. |
| `Can't find the TIFF file` / `GeoJSON file` | Give its full path, or its path inside `layers/` (e.g. `jambi/test_plots.geojson`). Check `.tif` vs `.tiff`. |
| `PostGIS could not be switched on` | The outlines approach needs PostGIS. The stencils approach still works. |
| `No map in the database yet` | Build and upload one first (Step 2). |
| `... isn't a results folder of the compaction step` | The folder has no `evidence.tif` and `outcome.tif`. Build it with `pipeline.py` (or `eudr_layers.py --out-dir`). |
| `... has no evidence pages` | The map was uploaded from a 5-colour TIFF. Upload its results folder as a map to use the evidence. |
| `... written by another version of eudr_layers.py (bit layout ...)` | The results folder was built by a different version of Joe's code. Rebuild it with `compaction/eudr_layers.py`, or put that version there. |
| `Missing required input(s): gfc2020` / `No JRC GFC2020 file covers the box` | `--data-dir` needs the satellite layers for the box, named as `eudr_layers.py` expects (see the top of that file). |
| `Your map is stored the old way` | Run `python database/maps.py migrate` once. It moves the map into its own schema without re-uploading. |
| `Several maps are stored (...)` | Add `--map NAME`. `python database/maps.py list` shows the names. |
| `There's no map called '...'` | Check the spelling against `python database/maps.py list`. |
| `There's already a map called '...'` | Choose another name, or delete that map first. |
| `... is too close to the map '...'` | The two names would share a schema (`Jambi V1` and `jambi-v1`). Choose another name. |
| `This TIFF is already stored as '...'` | That map is uploaded already. Use it with `--map`. |
| `This map is ... pixels` | The app keeps a whole map in memory, up to 100 million pixels. Crop the TIFF into smaller areas. |
| `The map '...' is incomplete in the database` | An upload was stopped part-way. Delete it with `maps.py delete` and upload again. |
| `Removed '...' from '...': it was built from a different TIFF` | You rebuilt a map from a new TIFF with one approach. Run the other approach's `build --map` too. |
| `This TIFF is not the map '...' in the database` | `verify` and `compare` need the TIFF that map was uploaded from. Check `--map`. |
| Almost every farm is `CANNOT_CHECK` | The farms are outside your map or crop. GeoJSON coordinates must be longitude first, then latitude. |
| `--point ... is not a longitude/latitude pair` | Give the longitude first (e.g. `103.5`), then the latitude (e.g. `-1.6`). |
| A test point says `outside the map` | `compare` prints the area your map covers. Pick a point inside it. |
| `python` is not found (Windows) | Use `py` instead, e.g. `py database/test_connection.py`. |
| The first command is slow | Neon pauses idle databases and takes a second or two to wake up. |

## Running in Docker (optional)

The database stays on Neon; the container just runs the scripts. From the **HackNHire** folder (the
image reads the project's one `requirements.txt` there, without the PyQt5 windows):

```
docker build -f backend/Dockerfile -t eudr-storage .
docker run --rm -v "$(pwd)/backend:/project" eudr-storage database/test_connection.py
docker run --rm -v "$(pwd)/backend:/project" -v "D:/eudr/inputs:/inputs" eudr-storage pipeline.py --name jambi --data-dir /inputs --bbox 103.0 -2.3 103.5 -1.8
docker run --rm -v "$(pwd)/backend:/project" eudr-storage compare/compare.py jambi --map jambi
```

The first `-v` shares the backend folder, so the container reads your `.env` and writes `layers/` and the
`results/` folders back to your computer; the second shares your satellite layers. Your `.env` is never
copied into the image. On Windows PowerShell, replace `$(pwd)` with `${PWD}`.

## Keeping things private

`.gitignore` keeps `.env` (your database password), `layers/`, every TIFF and GeoJSON (customer data) and
the results out of Git. Tests make their own made-up data in temporary folders.

---

## Earlier results: the 5 colours on a local database

Measured before the merge with Joe's code, with `compare.py` on made-up 5-colour Jambi maps (land 61%, forest 32%, water 3%,
deforestation 2%, needs review 2%), using a local PostgreSQL 16 + PostGIS 3.4 database.
Times vary by about ±20% between runs.

**Fidelity and storage**

| | 2048 × 2048 map, 303 farms | 6144 × 6144 map, 2,003 farms |
|---|---|---|
| Decision fidelity, stencils / outlines | 100% / 100% | 100% / 100% |
| – Question 1, "is the point in forest?" | 100% / 100% (113 points) | 100% / 100% (694 points) |
| – Question 2, "does the outline overlap forest?" | 100% / 100% (190 outlines) | 100% / 100% (1,309 outlines) |
| Pixel fidelity (all 5 colours), stencils / outlines | 100% / 100% | 100% / 100% |
| TIFF file (compressed) | 0.10 MB | 0.86 MB |
| TIFF uncompressed | 4.19 MB | 37.75 MB |
| **Stencils database** | **0.19 MB** | **0.99 MB** |
| **Outlines database** | **2.56 MB** | **21.46 MB** |

**Speed: checking all the farms of one kind at once**

| | 113 points | 190 outlines | 694 points | 1,309 outlines |
|---|---|---|---|---|
| TIFF directly, pixel rule | 0.007 s | 0.059 s | 0.069 s | 0.52 s |
| TIFF directly, exact area | same as above | 0.19 s | same as above | 1.40 s |
| **Stencils** | **0.005 s** | **0.042 s** | **0.039 s** | **0.33 s** |
| **Outlines** | **0.003 s** | **0.020 s** | **0.030 s** | **0.15 s** |

For a point, both TIFF rules are the same thing: the colour of the pixel under it.

**Speed over a network.** Neon is reached over the internet, so every query waits for a round
trip. To estimate this, the tests were repeated through a link that adds about 7 ms per round
trip, similar to reaching Neon's Singapore region:

- **One farm on its own:** about 7 ms for both stencils and outlines, because each needs a single
  round trip. Reading a TIFF on your own computer takes about 1 ms, since there is no network.
- **Batches:** 694 points took 0.044 s (stencils) and 0.041 s (outlines). 1,309 outlines took
  0.40 s and 0.15 s.

The same 100% results held for a map in metres (UTM 48S), for the centre rule with circle
points, and for a map where forest is the most common colour (so forest was left out instead).

## Honest notes for your evaluation

- **Stencils are about the size of the compressed TIFF** (1.15× on the large map), roughly
  38× smaller than the raw pixels, and queryable in SQL. Without zipping (one row per 8 × 8
  square) they would take an estimated 19 MB on the large map.
- **Outlines are about 25× bigger than the compressed TIFF.** Pixel edges turn every curve into
  a staircase, and each corner costs 16 bytes. Their strengths are exact areas and the fastest
  checks. Tracing in 256-pixel tiles instead of 64 saves about 30% of the storage but makes
  outline checks about 50% slower. 128 is a middle ground: 25% smaller, 15% slower. To change
  it, edit `TRACE_TILE` in `polygon_approach.py`.
- **Speed:** compared with reading the TIFF using the same rule:
  - stencils were 1.4–1.8× faster than the pixel rule
  - outlines were about 10× faster than exact areas, and about 2× faster for points

  Most of the stencils' time goes into working out which pixels each outline covers (in
  Python), not into the database.
- **Storing land and forest doesn't slow the queries down.** Measuring the forest inside each
  outline costs about 0.03 ms per outline, and the speed-ups below more than make up for it:
  - **JIT switched off.** PostGIS marks its shape functions as expensive, so for a big batch
    PostgreSQL's JIT compiler spent about 150 ms compiling a query that then ran in about
    65 ms. Both the old and the new version were losing this time. The outlines check now
    switches JIT off before a big query (`SET LOCAL jit = off`, for that one transaction
    only). PostgreSQL 19 switches JIT off by default for reasons like this.
  - **Square labels** answer farms that sit entirely in one colour without any geometry.
  - **64-pixel tiles** instead of 256 give shapes with fewer corners.

  On the large map, checking all 2,002 farms that are on the map:

  | | Local database | With a 7 ms round trip |
  |---|---|---|
  | Old outlines (land and forest not stored) | 0.32 s | 0.37 s |
  | Old outlines, with JIT also switched off | 0.17 s | – |
  | **New outlines (every colour stored)** | **0.14 s** | **0.19 s** |

  Points alone took about the same time in both versions.
- **Rules matter:** the pixel rule and the exact-area rule can disagree on a farm or two, and
  pixel-rule hectares are larger for small farms. That's a policy choice to agree with
  SystemEarth, not a storage error.
