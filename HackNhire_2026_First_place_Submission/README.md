# HackNHire: fast, faithful EUDR checks for rubber plots

A student prototype for the **SystemEarth Hack & Hire** challenge

About 20 satellite layers (2000–2026) are squeezed into **one 32-bit number per pixel**, stored in Neon (cloud PostgreSQL), and used to check farm plots. Every plot gets exactly the answer the original layers give.

**Use case: rubber under the EU Deforestation Regulation (EUDR).** A plot passes if its land was not forest on 31 Dec 2020, or that forest has not been cleared since. Rubber is tricky: plantations look like forest, and replanting looks like clearing. The EUDR counts rubber as farmland (Art. 2(6)), so clearing on rubber land is flagged **needs review**, not failed. Test region: Jambi, Sumatra (36 million pixels at 10 m).

There are two windows:

| Window | What it does | Command (from the project folder) |
| --- | --- | --- |
| **Backend: the ingest window** | Turns a folder of satellite layers into a map and uploads it to Neon | `backend\venv\Scripts\python backend\ingest_app.py` |
| **Frontend: the app** | Shows a map from Neon, checks farms you draw or load, compares the approaches | `frontend\.venv\Scripts\python frontend\app.py` |

New here? Follow [Set up and run](#set-up-and-run) from top to bottom. It takes about 15 minutes.

---

## Set up and run

You need:

- **Windows** with PowerShell. Every command below is for PowerShell. On macOS or Linux, see [macOS and Linux](#macos-and-linux).
- **Python 3.10 to 3.13** ([python.org](https://www.python.org/downloads/)). Tick "Add python.exe to PATH" when you install it.
- **A free [Neon](https://console.neon.tech) account**, which is the cloud PostgreSQL database.
- **An internet connection**, for Neon and for the app's map (Leaflet loads from cdnjs).

Run every command from the **project folder**, the one that holds this README.

### Step 1. Get the code and install the packages

```powershell
git clone https://github.com/BruhClient/HackNHire-System-Earth.git
cd HackNHire-System-Earth
```

Each window gets its own Python environment. Both install the same `requirements.txt`:

```powershell
python -m venv backend\venv
backend\venv\Scripts\python -m pip install -r requirements.txt

python -m venv frontend\.venv
frontend\.venv\Scripts\python -m pip install -r requirements.txt
```

- If `python` isn't found, use `py` instead (for example `py -3.13 -m venv backend\venv`).
- You never need to "activate" a venv. The commands call its Python directly.

### Step 2. Create the Neon database

1. Sign in at [console.neon.tech](https://console.neon.tech) and click **New project**.
   - **Name:** anything, for example `hacknhire`.
   - **Postgres version:** the default (16 or newer).
   - **Region:** the one nearest to you. For Singapore, choose **AWS Asia Pacific (Singapore)**. Every check is a round trip to this region, so a nearer region makes checks faster.
2. On the project's dashboard, click **Connect**. Keep the defaults: branch `main`, database `neondb`, role `neondb_owner`. Copy the connection string. It looks like this:
   ```
   postgresql://neondb_owner:PASSWORD@ep-something-123456-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require
   ```
   The pooled string (the host has `-pooler`) and the direct string both work. The code is written for the pooled one.

### Step 3. Put the connection string in `backend\.env`

The project reads one setting:

| | |
| --- | --- |
| **Variable** | `DATABASE_URL` |
| **Value** | your Neon connection string from Step 2 |
| **File** | `backend\.env`, which sits next to `pipeline.py` and `backend\.env.example` |
| **Used by** | everything: the ingest window, the app, `pipeline.py` and every script in `backend\database` |
| **Optional** | `frontend\.env` (copy `frontend\.env.example`), only if the app should use a different database from the backend. Without it, the app uses `backend\.env`. |

Copy the example file, then open the copy:

```powershell
Copy-Item backend\.env.example backend\.env
notepad backend\.env
```

Replace the example string with your own, so the file has this one line:

```
DATABASE_URL=postgresql://neondb_owner:PASSWORD@ep-something-123456-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require
```

- Use no spaces around `=`. Quotes aren't needed. Lines starting with `#` are ignored.
- Keep `?sslmode=require` at the end.
- Until you replace it, the example string (`ep-xxxx`) is refused with a message telling you to paste your own.

**Where the code looks** (`common.connect()`, in this order):
1. A `DATABASE_URL` already set in the terminal. This wins over any file, for example `$env:DATABASE_URL = "postgresql://..."` for one PowerShell session.
2. The app only: `frontend\.env`, if you made one.
3. `.env` in the folder you run the command from.
4. `backend\.env`, the recommended place.
5. `backend\database\.env`.

`.env.txt` is accepted too, because Notepad sometimes adds `.txt`. `.env.example` is never read.

> `.env` holds your database password. Git ignores it (only `.env.example` is committed), so never commit it, paste it into chat or put it in a screenshot.

### Step 4. Set up the tables

Neon needs two things once: **PostGIS**, which the outlines approach uses, and **`public.maps`**, the list of maps. Every map's own tables are made automatically when it is uploaded. **Don't create those by hand.**

**Option A: run the setup command (recommended).**

```powershell
backend\venv\Scripts\python backend\database\test_connection.py
backend\venv\Scripts\python backend\database\maps.py init
```

The first command should end with `All good`. The second should report that the list of maps exists, plus a PostGIS version.

**Option B: run the SQL yourself.** In the Neon console, open **SQL Editor**, paste the following and click **Run**. It is the same SQL `maps.py init` runs, from `backend/database/schema.sql`:

```sql
-- PostGIS, in public so that deleting a map can never remove it (only the outlines need it)
CREATE EXTENSION IF NOT EXISTS postgis SCHEMA public;

-- The list of maps: one row per map, each stored in its own schema map_<name>
CREATE TABLE IF NOT EXISTS public.maps (
    name         TEXT PRIMARY KEY,
    schema_name  TEXT NOT NULL UNIQUE,
    source_file  TEXT,
    fingerprint  TEXT,
    width        INT,
    height       INT,
    added_at     TIMESTAMPTZ DEFAULT now()
);
```

Check it worked:

```sql
SELECT postgis_lib_version();   -- e.g. 3.5.0
SELECT * FROM public.maps;      -- empty until you upload a map
```

Both options are safe to run again: they change nothing that already exists. If you skip this step, the first upload does it anyway, but doing it now catches connection problems early.

### Step 5. Open the backend window and add a map

```powershell
backend\venv\Scripts\python backend\ingest_app.py
```

1. **Folder.** Click **Browse…** and choose either:
   - a folder of **satellite layers** (the GeoTIFFs); the right-hand panel lists the files it needs and ticks them off;
   - or a **results folder** that was built before (it holds `evidence.tif` and `outcome.tif`), which skips the compaction step.
2. **Name.** For example `jambi`. Each map is stored in its own schema, `map_jambi`.
3. **Area.** Type the west, south, east and north edges in degrees, or click **Use the whole area**.
4. **Options.** The number of made-up test plots to check after the upload (1,000 by default), and, if you like, your own plots file in GeoJSON.
5. Click **Ingest**. The window shows each step: compress, upload, check. It ends on a summary of what was stored, its size, and whether every plot matched. **Cancel** saves nothing to Neon.

Protected areas: put WDPA polygons in the layers folder as GeoJSON whose name starts with `wdpa` (e.g. `WDPA_Oct2026_Public_IDN-polygons.geojson`). They are stored with the map, and the app draws them.

**No satellite data?** Make some up and ingest that:

```powershell
backend\venv\Scripts\python backend\compaction\make_test_layers.py C:\temp\test_layers
```

Then, in the window, choose `C:\temp\test_layers`, name the map `test`, and click **Use the whole area**.

The window runs `backend/pipeline.py` and shows you the exact command. To do the same from the command line:

```powershell
cd backend
venv\Scripts\python pipeline.py --name jambi --data-dir D:\eudr\inputs --bbox 103.0 -2.3 103.5 -1.8 --test-plots 1000 --verify
cd ..
```

### Step 6. Open the frontend app and check farms

```powershell
frontend\.venv\Scripts\python frontend\app.py
```

1. Choose a map in the **Map** dropdown, top left. The map is drawn in its 5 colours. If Neon has no maps yet, the app tells you to run the ingest window, then click **Refresh**.
2. Draw farms with **Polygon (P)** or **Point (O)**, or load them with **Open ▸ GeoJSON file…**.
3. Press **Check** (Ctrl+Enter). Each farm gets a verdict from both approaches, with forest and deforested hectares and whether it lies in a protected area.
4. The **Compare** tab benchmarks the approaches on 1, 10, 100 and 1,000 made-up farms.

`--map NAME` opens a particular map at start-up. The app only reads, except for **Maps ▸ Rename map…** and **Delete map…** (both ask first) and **Check ▸ Save results to Neon** (off by default).

```mermaid
flowchart LR
    I["Ingest window<br/>backend/ingest_app.py"] -- "builds and uploads" --> N[("Neon")]
    N -- "reads" --> A["Desktop app<br/>frontend/app.py"]
    A --> M["Map and drawing"]
    A --> R["Verdicts per farm"]
    A --> K["Compare tab:<br/>speed and query cost"]
```

### Step 7 (optional). Check everything works

```powershell
cd backend; venv\Scripts\python -m unittest discover -s tests -t .; cd ..             # backend tests, no database
frontend\.venv\Scripts\python -m unittest discover -s frontend/tests -t frontend    # app tests, no database
cd backend; venv\Scripts\python tests\smoke_pipeline.py; cd ..                         # the whole chain over Neon, on a throwaway map it deletes afterwards
```

### Managing maps from the command line

```powershell
backend\venv\Scripts\python backend\database\maps.py list                       # every map: schema, size, approaches
backend\venv\Scripts\python backend\database\maps.py rename jambi "Jambi 2024"  # its schema is renamed too
backend\venv\Scripts\python backend\database\maps.py delete test --yes          # drops the map's schema: can't be undone
```

To look at the data, use Neon's **SQL Editor**:

```sql
SELECT * FROM public.maps;
SELECT pg_size_pretty(pg_total_relation_size('map_jambi.stencil_pages'));
SELECT plot_id, verdict, reason FROM map_jambi.evidence_results ORDER BY verdict;
```

### If something goes wrong

| Problem | Fix |
| --- | --- |
| `DATABASE_URL is not set` | `backend\.env` is missing, is in the wrong folder, or names the variable differently. Do Step 3 again. |
| `Note: using .env.txt` | Notepad added `.txt`. It still works. To tidy up: `Rename-Item backend\.env.txt .env` |
| `Your .env still has the example connection string` | Paste your own string from Neon's **Connect** button. |
| `Could not connect to the database` | Copy the string again from Neon, including `?sslmode=require`, and check you're online. |
| The wrong database is used | A `DATABASE_URL` set in the terminal wins over `.env`: clear it with `Remove-Item Env:DATABASE_URL`. For the app, `frontend\.env` wins over `backend\.env`: delete it, or give it the same string. |
| `PostGIS could not be switched on` | Run `CREATE EXTENSION IF NOT EXISTS postgis SCHEMA public;` in Neon's SQL Editor (Step 4, option B). Without PostGIS, the stencils and evidence pages still work. |
| `No map in the database yet` | Add one with the ingest window (Step 5). |
| `Several maps are stored (...)` | Add `--map NAME`. `maps.py list` shows the names. |
| `Your map is stored the old way` | Run `backend\venv\Scripts\python backend\database\maps.py migrate` once. |
| `python` is not found | Use `py`, or reinstall Python with "Add python.exe to PATH" ticked. |
| `pip install` fails on PyQt5 or rasterio | Use Python 3.10 to 3.13, 64-bit. Newer versions may not have ready-made packages yet. |
| The app's map is blank | The map view loads Leaflet from cdnjs, so check you're online. |
| The first command is slow | Neon pauses an idle database and takes a second or two to wake it. |

More fixes: [backend/README.md](backend/README.md#troubleshooting).

### macOS and Linux

Use `/` in paths and `bin/python` in place of `Scripts\python`, for example:

```bash
python3 -m venv backend/venv && backend/venv/bin/python -m pip install -r requirements.txt
python3 -m venv frontend/.venv && frontend/.venv/bin/python -m pip install -r requirements.txt
cp backend/.env.example backend/.env    # then edit it
backend/venv/bin/python backend/database/maps.py init
backend/venv/bin/python backend/ingest_app.py
frontend/.venv/bin/python frontend/app.py
```

---

## How it works

```mermaid
flowchart LR
    A["~20 satellite layers"] --> B["1. Compaction<br/>eudr_layers.py"]
    B --> C["evidence.tif<br/>1 number per pixel"]
    C --> D[("2. Neon<br/>evidence pages,<br/>stencils, outlines")]
    P["Farm plots"] --> E["Check: one query"]
    D --> E
    E --> F["Verdict, reason,<br/>hectares, years"]
```

1. **Compaction** (`backend/compaction/eudr_layers.py`, Joe's code) packs the EUDR facts per pixel: forest in 2020, rubber, and the year each source (TMF, Hansen, RADD radar) records a clearing. Time is stored as events, not one map per year.
2. **Storage in Neon** (`backend/database/`) keeps the result three competing ways:

|         | Evidence pages                                | Stencils                      | Outlines                        |
| ------- | --------------------------------------------- | ----------------------------- | ------------------------------- |
| Stores  | All facts (32 bits)                           | 5-colour map                  | 5-colour map, as PostGIS shapes |
| A check | Fetch pages, run Joe's rule                   | Fetch pages, count pixels     | PostGIS measures overlaps       |
| Best at | Full answer: verdict, reason, hectares, years | Tiny storage, few round trips | Exact areas, fastest            |

### The rule, per pixel

The rule lives only in `eudr_layers.py`. Nothing else re-implements it.

```mermaid
flowchart TD
    S["Pixel"] --> F{"Forest on<br/>31 Dec 2020?"}
    F -- no --> L["Land: pass"]
    F -- yes --> C{"Clearing recorded<br/>after 2020?"}
    C -- no --> OK["Forest standing: pass"]
    C -- yes --> R{"Rubber map<br/>says rubber?"}
    R -- yes --> NR["Needs review<br/>possible replanting"]
    R -- no --> T{"How many independent<br/>sources agree?"}
    T -- "TMF + Hansen or RADD" --> CF["Confirmed deforestation: fail"]
    T -- "one source, or weak pair" --> PR["Probable or needs review"]
```

A plot's verdict is decided by the worst pixel under it. Points become a circle of their declared area (4 ha if none is given), as the EU system does. Missing data is never treated as deforestation-free.

### What is in each pixel

All 17 facts fit in 32 bits, so one number replaces about 20 files.

```mermaid
flowchart LR
    subgraph IN["Original layers"]
        I1["JRC forest 2020, type"]
        I2["Rubber map"]
        I3["TMF yearly change"]
        I4["Hansen loss year"]
        I5["RADD radar alerts"]
        I6["Optical alerts, land cover,<br/>water, protected areas"]
    end
    subgraph OUT["32-bit evidence value"]
        O1["Forest, type, rubber"]
        O2["TMF class and<br/>first clearing year"]
        O3["Hansen loss year"]
        O4["RADD year<br/>and confidence"]
        O5["Optical alert, land cover,<br/>neighbour context"]
    end
    I1 --> O1
    I2 --> O1
    I3 --> O2
    I4 --> O3
    I5 --> O4
    I6 --> O5
```

- **Kept exactly:** forest in 2020, forest type, rubber, land and water, protected areas.
- **Summarised:** yearly maps and alerts become "first clearing year after 2020" plus a confidence flag.
- **Dropped:** what the decision never reads, such as alert days and yearly land-cover classes.
- **Worked out once, at build time:** facts that need neighbours, such as small gaps inside a confirmed clearing. A check then reads only the pixels under the plot.

### What happens when you press Check

```mermaid
sequenceDiagram
    participant U as You (app)
    participant B as Backend
    participant N as Neon
    U->>B: Farm plots
    B->>B: Find the 256 x 256 pages they touch
    B->>N: One query for all those pages
    N-->>B: Compressed pages
    B->>B: Unzip, run the rule on each plot's pixels
    B-->>U: Verdict, reason, hectares, years
```

Neon is reached over the internet, so every query costs a network round trip. All plots are answered in a few batched queries, never one per plot.

### The three Neon approaches, in more detail

- **Evidence pages** store `evidence.tif` itself. Each 256 × 256 page holds its distinct values (about 25) plus one small index per pixel, zipped. Joe's own rule runs on them, so the answer is identical to the original layers by construction.
- **Stencils** store the 5-colour outcome (land, water, forest, deforestation, needs review) as zipped pages. The client works out which pixels a farm covers and counts them.
- **Outlines** trace each colour patch into a PostGIS polygon with a spatial index. PostGIS measures exact overlaps inside Neon, and replies are tiny.

Each map lives in its own schema (`map_<name>`), listed in `public.maps`. Uploads are all-or-nothing: a failure leaves nothing behind. If the same map is already in Neon under another name, its tables are copied inside Neon rather than uploaded again.

---

## Results

Jambi, 1,000 test plots.

| Measure           | Result                                                                      |
| ----------------- | --------------------------------------------------------------------------- |
| Decision fidelity | **100%** (1,000 of 1,000): all 35 outputs match the original layers         |
| Pixel fidelity    | **100%** of 36 M pixels in the decision map                                 |
| Storage           | **6.7 MB** in Neon vs 245.9 MB raw (36× smaller) or 13.7 MB compressed (2×) |
| Speed over Neon   | 1,000 plots in **3.5 s**, one query (median of 3)                           |
| Speed, local disk | ~1.1 ms per plot vs 10.9 ms from the original layers                        |

The local-disk timing and per-layer figures come from Joe's own runs. The rest were measured against Neon.

---

## Repository

```
requirements.txt   every package, for both windows
backend/           compaction, Neon storage, checks, evaluation, the ingest window
  .env             YOU CREATE THIS (Step 3); never in Git
  ingest_app.py    the backend window
  pipeline.py      the same chain on the command line
  database/        schema.sql (every table), maps.py, the three approaches
  layers/<map>/    what the compaction step builds; never in Git
frontend/          the desktop app (app.py)
docs/              design specs and plans
```

Details: [backend/README.md](backend/README.md) · [frontend/README.md](frontend/README.md)

---

## Limitations

- Storage is only 2× smaller than compressed originals. The bigger win is less work per check.
- 100% fidelity is not accuracy: it proves nothing is lost, not that the rule is right.
- The evidence keeps only what this rule reads. A new rule needs a rebuild.
- This repo has `eudr_layers.py` v1.1. Newer versions split the evidence into three files.
- One region tested. Social risks (indigenous lands, legality) are not covered.

## Privacy

`.env`, satellite layers, TIFFs and GeoJSONs stay out of Git. Never commit or share `DATABASE_URL`.

## Credits

Compaction by Joe ([Joeongtengkiat/HacknHire](https://github.com/Joeongtengkiat/HacknHire)). Storage, checks and apps by the rest of team hacknhire. The stencil idea came from SystemEarth's CTO. Claude helped write and review code; no AI makes any compliance decision.
