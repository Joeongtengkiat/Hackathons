"""
maps.py - several maps in one Neon database, each in its own schema (a folder of tables).

    public.maps     the list of maps: name, schema, source file, fingerprint, size
    map_<name>      grid_info, stencil_pages, class_polys, square_labels, evidence_info,
                    evidence_pages, protected_areas, plots, plot_results, evidence_results

A map's schema comes from its name: 'Jambi v1' -> map_jambi_v1 (common.map_schema). Every query
names its tables with the schema (grid.table(...)), so maps never mix.

What to upload:
  * a results folder of the compaction step (layers/<map>/, made by pipeline.py): all three
    approaches - the stencils and outlines store its outcome in 5 colours, the evidence pages store
    evidence.tif (every decision fact, with the years and sources)
  * or a TIFF in the 5 colours (0 land, 1 water, 2 forest, 3 deforestation, 4 needs review): the
    stencils and outlines only - this is what the app uploads
  * with either, protected areas (protected_areas.py): the wdpa*.geojson files in a results folder,
    or the files given with --protected, cut to the map

Every table is defined in schema.sql. The shared ones are made once (init); a map's own tables are
made from the same file when it is uploaded.

Commands (run from the backend folder; give a path, or a name inside layers/)
    python database/maps.py init                                     # once, on a fresh database
    python database/maps.py list
    python database/maps.py upload layers/jambi [--name "Jambi v1"]  # a results folder: all three approaches
    python database/maps.py upload my_map.tif [--name "Jambi v1"]    # a 5-colour TIFF: stencils + outlines
    (add --protected wdpa_IDN.geojson to either to store protected areas with the map)
    (add --no-outlines to either for the lean version: stencils (+ evidence) only, ~24x less storage)
    python database/maps.py rename "Jambi v1" "Jambi 2024"           # its schema follows the name
    python database/maps.py delete "Jambi v1" --yes                  # can't be undone
    python database/maps.py migrate [--name jambi-v1]                # a map stored the old way

upload is ONE transaction: if anything fails, or you press Ctrl+C, nothing is left behind.

Data already in Neon is not uploaded again. Before writing, upload looks for a stored map with
the same pixels in the same place (public.maps.fingerprint of the 5 colours; for the evidence,
evidence_info.fingerprint of evidence.tif). It copies that map's stencils, outlines and evidence
pages into the new map inside Neon (INSERT ... SELECT: nothing crosses the network), and builds
and uploads only what is missing (e.g. the evidence pages for a map first uploaded from a TIFF).
A name already taken is still refused.
"""

import argparse
import dataclasses
import os
import sys
import time

import psycopg
import rasterio

import common
import polygon_approach
import protected_areas
import stencil_approach

MAX_PIXELS = 100_000_000   # the app keeps the whole map in memory: 1 byte per pixel
MAP_TABLES = ["grid_info", "stencil_pages", "class_polys", "square_labels", "evidence_info", "evidence_pages",
              "protected_areas", "plots", "plot_results", "evidence_results"]
UPLOAD_STEPS = 7           # a TIFF: stencils: read, pack, index; outlines: read, trace, index; tidy up


def size_problem(width: int, height: int) -> str:
    """Why a map is too big for the app ("" if it isn't)."""
    if width * height <= MAX_PIXELS:
        return ""
    return (f"This map is {width:,} x {height:,} pixels. The app keeps the whole map in memory, so it "
            f"supports up to {MAX_PIXELS // 1_000_000} million pixels (e.g. 10,000 x 10,000). "
            "Crop it into smaller areas first.")


def init(conn) -> dict:
    """Set up a fresh database once: the list of maps (public.maps) and PostGIS, from schema.sql's
    @shared and @postgis sections. Each map's own tables are made from the same file when it is
    uploaded. Safe to run again: nothing that exists is changed. PostGIS is optional (only the
    outlines need it), so a database without it is still set up."""
    with conn.cursor() as cur:
        cur.execute(common.schema_sql("shared"))
    conn.commit()
    try:
        with conn.cursor() as cur:
            cur.execute(common.schema_sql("postgis"))
            cur.execute("SELECT postgis_lib_version()")
            postgis = cur.fetchone()[0]
        conn.commit()
    except psycopg.Error as exc:
        conn.rollback()
        postgis = None
        print(f"  PostGIS could not be switched on ({str(exc).strip()}). The stencils and the evidence "
              "don't need it; upload with --no-outlines, or switch PostGIS on in Neon and run init again.")
    return {"maps_table": True, "postgis": postgis}


def list_maps(conn, with_evidence: bool = False) -> list[dict]:
    """Every stored map, by name: where it lives, its source file, its size and which approaches it
    has. with_evidence: also whether it has evidence pages ("evidence")."""
    if not common.table_exists(conn, "public.maps"):
        if common.table_exists(conn, "public.grid_info"):
            sys.exit(common.OLD_LAYOUT_MESSAGE)
        return []
    evidence = ",\n                   to_regclass(m.schema_name || '.evidence_pages') IS NOT NULL" \
        if with_evidence else ""
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT m.name, m.schema_name, m.source_file, m.width, m.height, m.added_at,
                   to_regclass(m.schema_name || '.stencil_pages') IS NOT NULL,
                   to_regclass(m.schema_name || '.class_polys') IS NOT NULL
                       AND to_regclass(m.schema_name || '.square_labels') IS NOT NULL{evidence}
            FROM public.maps m
            WHERE to_regnamespace(m.schema_name) IS NOT NULL
            ORDER BY lower(m.name)""")
        rows = cur.fetchall()
    stored = []
    for name, schema, source, width, height, added, stencils, outlines, *more in rows:
        stored.append({"name": name, "schema": schema, "source_file": source, "width": width, "height": height,
                       "added_at": added.isoformat() if added else None, "stencils": bool(stencils),
                       "outlines": bool(outlines)})
        if with_evidence:
            stored[-1]["evidence"] = bool(more[0])
    return stored


# --------------------------------------------------------------------------------------
# reuse: data Neon already holds is copied inside Neon, not uploaded again
# --------------------------------------------------------------------------------------
PARTS = {  # what can be copied: schema.sql's sections (table, then keys) and the tables
    "stencils": ("stencils", "stencils_keys", ["stencil_pages"]),
    "outlines": ("outlines", "outlines_keys", ["class_polys", "square_labels"]),
    "evidence pages": ("evidence", "evidence_keys", ["evidence_info", "evidence_pages"]),
}


def find_stored(conn, colours: str, evidence=None, outlines: bool = True):
    """The stored map that already holds the most of this data, and which parts of it can be
    reused: (grid, [parts]), or (None, []). colours: the 5-colour map's fingerprint (a map with the
    same pixels in the same place has the same one). evidence: a function giving evidence.tif's
    fingerprint (only called if some map has evidence pages), or None for a TIFF upload."""
    with conn.cursor() as cur:
        cur.execute("SELECT name FROM public.maps WHERE fingerprint = %s "
                    "AND to_regnamespace(schema_name) IS NOT NULL ORDER BY added_at", (colours,))
        names = [row[0] for row in cur.fetchall()]
    best, best_parts, wanted = None, [], None
    for name in names:
        grid = common.load_grid(conn, name)
        parts = [part for part, (_, _, tables) in PARTS.items()
                 if part != "evidence pages" and all(t in grid.tables for t in tables)
                 and (outlines or part != "outlines")]
        if evidence is not None and "evidence_info" in grid.tables:
            with conn.cursor() as cur:
                cur.execute(f"SELECT fingerprint FROM {grid.table('evidence_info')}")
                row = cur.fetchone()
            if wanted is None:
                wanted = evidence()
            if row and row[0] == wanted and "evidence_pages" in grid.tables:
                parts.append("evidence pages")
        if len(parts) > len(best_parts):
            best, best_parts = grid, parts
    return best, best_parts


def copy_stored(conn, stored: common.Grid, name: str, source_file: str, parts: list[str], report=None) -> common.Grid:
    """A new map called name, holding a copy of these parts of a stored map with the same pixels:
    copied inside Neon (INSERT ... SELECT), so nothing is sent over the network again. Leaves the
    transaction open (maps.upload commits)."""
    grid = dataclasses.replace(stored, map_version=name, schema=common.map_schema(name), tables=frozenset())
    with conn.cursor() as cur:
        cur.execute(common.CATALOGUE_SQL)
        cur.execute("SELECT name FROM public.maps WHERE schema_name = %s", (grid.schema,))
        clash = cur.fetchone()
        if clash:
            sys.exit(f"'{name}' is too close to the map '{clash[0]}': both would be stored as {grid.schema}. "
                     "Choose another name.")
        cur.execute(f"CREATE SCHEMA {grid.schema}")
        cur.execute(common.map_tables_sql(grid))
    common.save_grid(conn, grid, source_file)
    for i, part in enumerate(parts):
        if report is not None:
            report(f"copying the {part} inside Neon", i, len(parts))
        print(f"  copying the {part} from '{stored.map_version}' inside Neon...")
        make, keys, tables = PARTS[part]
        with conn.cursor() as cur:
            if part == "outlines":
                cur.execute(common.schema_sql("postgis"))
            cur.execute(common.schema_sql(make, grid))
            for table in tables:
                cur.execute(f"INSERT INTO {grid.table(table)} SELECT * FROM {stored.table(table)}")
            cur.execute(common.schema_sql(keys, grid))
    if report is not None:
        report("copying inside Neon", len(parts), len(parts))
    return grid


def upload(conn, source: str, name: str, report=None, outlines: bool = True,
           protected: list[str] | None = None) -> common.Grid:
    """A new map, in one transaction: if anything fails or is cancelled (report may raise),
    everything is rolled back and the database is as it was. Then the new tables are packed
    (VACUUM ANALYZE), which can't run inside a transaction.
    source: a results folder of the compaction step (all three approaches), or a 5-colour TIFF
    (stencils and outlines). outlines=False leaves the outlines out: the stencils (and the
    evidence) answer every check, and the outlines take ~24x the stencils' storage. The app's
    comparison of the two approaches needs them. protected: GeoJSON files of protected areas to
    store with the map (default: the wdpa*.geojson files in a results folder, none for a TIFF)."""
    name = (name or "").strip()
    common.map_schema(name)  # stops here if the name can't be used
    results, tif = None, source
    if os.path.isdir(source):
        import layers  # the compaction step's code (and its packages) is only needed for a results folder
        results = layers.results_folder(source)
        tif = layers.class_map(results)
    has_catalogue = common.table_exists(conn, "public.maps")
    if not has_catalogue and common.table_exists(conn, "public.grid_info"):
        sys.exit(common.OLD_LAYOUT_MESSAGE)
    if has_catalogue:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM public.maps WHERE name = %s", (name,))
            if cur.fetchone():
                sys.exit(f"There's already a map called '{name}'. Choose another name, "
                         "or delete that map first.")
    with rasterio.open(tif) as src:
        problem = size_problem(src.width, src.height)
        if not problem:
            shape_grid = common.Grid.from_dataset(src, name)
            colours = common.scan_map(src, shape_grid)[1] if has_catalogue else None
    if problem:
        sys.exit(problem)
    stored, reuse = None, []
    if colours is not None:   # is this data in Neon already, under another name?
        evidence = None
        if results is not None:
            import evidence_approach
            import layers
            evidence = lambda: evidence_approach.fingerprint_of(layers.evidence_file(results), shape_grid)  # noqa: E731
        stored, reuse = find_stored(conn, colours, evidence, outlines)
    if protected is None:
        protected = protected_areas.find_files(results) if results else []
    areas = protected_areas.read(protected, shape_grid) if protected else []  # read first: fail before writing
    if protected and not areas:
        print("  Protected areas: none of them overlaps the map, so none are stored")
    start = time.perf_counter()
    try:
        if stored is not None:
            print(f"Already in Neon as '{stored.map_version}': its {', '.join(reuse)} are copied inside Neon, "
                  "not uploaded again")
            grid = copy_stored(conn, stored, name, tif, reuse, report)
        copy = {"allow_copy": True} if stored is not None else {}   # build what the stored map lacks
        if "stencils" not in reuse:
            grid = stencil_approach.build(conn, tif, name, report, tidy=False, **copy)
        if outlines and "outlines" not in reuse:
            grid = polygon_approach.build(conn, tif, name, report, tidy=False, **copy)
        if results is not None and "evidence pages" not in reuse:
            import evidence_approach
            evidence_approach.build(conn, results, name, report, tidy=False)
        if areas:
            protected_areas.build(conn, grid, areas, report)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    def tell(label, done, total):
        """Progress after the commit: the map is saved now, so a late Cancel can't undo it."""
        if report is not None:
            try:
                report(label, done, total)
            except Exception:  # noqa: BLE001 - e.g. the app's Cancelled: too late, finish tidying
                pass

    built = (["stencil_pages"] + (["class_polys", "square_labels"] if outlines else [])
             + (["evidence_pages"] if results else []) + (["protected_areas"] if areas else []))
    tables = [grid.table(t) for t in built]
    for i, table in enumerate(tables):
        tell("tidying up", i, len(tables))
        common.tidy_up(conn, table)
    tell("tidying up", len(tables), len(tables))
    what = ", ".join(n for n, on in (("stencils", True), ("outlines", outlines), ("evidence pages", results),
                                     ("protected areas", areas)) if on)
    copied = f"; copied inside Neon from '{stored.map_version}': {', '.join(reuse)}" if stored is not None else ""
    print(f"Uploaded '{name}' ({what}{copied}) in {time.perf_counter() - start:.1f} s")
    common.print_storage(conn, [grid.table(t) for t in MAP_TABLES])
    return grid


def upload_source(path: str) -> str:
    """What maps.py upload was given: a results folder (its path, or its name in layers/), else a TIFF."""
    for folder in (path, os.path.join(common.ROOT_DIR, path), os.path.join(common.LAYERS_DIR, path)):
        if os.path.isdir(folder):
            return folder
    return common.find_tiff(path)


def describe(conn, name: str) -> dict:
    """What a map uses in the database, and how many saved verdicts would go with it."""
    map_name, schema = common.find_map(conn, name)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT coalesce(sum(pg_total_relation_size(c.oid)), 0)
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = %s AND c.relkind = 'r'""", (schema,))
        size = int(cur.fetchone()[0])
        saved = 0
        if common.table_exists(conn, f"{schema}.plot_results"):
            cur.execute(f"SELECT count(*) FROM {schema}.plot_results")
            saved = int(cur.fetchone()[0])
        cur.execute("SELECT source_file, width, height FROM public.maps WHERE name = %s", (map_name,))
        source_file, width, height = cur.fetchone()
    return {"name": map_name, "schema": schema, "bytes": size, "saved_results": saved,
            "source_file": source_file, "width": width, "height": height}


def delete(conn, name: str) -> dict:
    """Remove a map: its schema (every table in it) and its row in public.maps, in one transaction.
    Needs the map's exact name."""
    map_name, schema = common.find_map(conn, name)
    if map_name != name.strip():
        sys.exit(f"To delete a map, give its exact name: '{map_name}'.")
    if not common.SCHEMA_RE.match(schema):  # never drop anything that isn't a map's own schema
        sys.exit(f"Refusing to delete schema '{schema}': it isn't a map's schema (map_...).")
    with conn.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        cur.execute("DELETE FROM public.maps WHERE name = %s", (map_name,))
    conn.commit()
    return {"name": map_name, "schema": schema}


def rename(conn, name: str, new_name: str) -> dict:
    """Give a map a new name, in one transaction. Its schema follows the name (ALTER SCHEMA only
    renames: nothing is copied), and the name inside grid_info and saved results changes too."""
    old, schema = common.find_map(conn, name)
    new = (new_name or "").strip()
    new_schema = common.map_schema(new)  # stops here if the name can't be used
    if new == old:
        sys.exit(f"The map is already called '{old}'.")
    with conn.cursor() as cur:
        cur.execute("SELECT name, schema_name FROM public.maps WHERE name <> %s", (old,))
        for other, other_schema in cur.fetchall():
            if other == new:
                sys.exit(f"There's already a map called '{other}'. Choose another name.")
            if other_schema == new_schema:
                sys.exit(f"'{new}' is too close to the map '{other}': both would be stored as "
                         f"{new_schema}. Choose another name.")
        if new_schema != schema:
            cur.execute("SELECT to_regnamespace(%s) IS NOT NULL", (new_schema,))
            if cur.fetchone()[0]:
                sys.exit(f"The schema {new_schema} already exists in the database, but no map uses it. "
                         "Choose another name.")
            cur.execute(f"ALTER SCHEMA {schema} RENAME TO {new_schema}")
        cur.execute(f"UPDATE {new_schema}.grid_info SET map_version = %s", (new,))
        if common.table_exists(conn, f"{new_schema}.plot_results"):
            cur.execute(f"UPDATE {new_schema}.plot_results SET map_version = %s WHERE map_version = %s",
                        (new, old))
        cur.execute("UPDATE public.maps SET name = %s, schema_name = %s WHERE name = %s",
                    (new, new_schema, old))
    conn.commit()
    return {"old": old, "name": new, "schema": new_schema}


def migrate(conn, name: str | None = None) -> str:
    """Move a map stored the old way (its tables in the public schema) into a schema of its own,
    and list it in public.maps. One transaction; nothing is copied, so it takes about a second.
    To undo: ALTER TABLE <schema>.<table> SET SCHEMA public for each table, then delete its row."""
    if not common.table_exists(conn, "public.grid_info"):
        sys.exit("Nothing to move: no map is stored the old way (there is no public.grid_info).")
    with conn.cursor() as cur:
        cur.execute("SELECT map_version, fingerprint, source_file, width, height FROM public.grid_info")
        rows = cur.fetchall()
    if len(rows) != 1:
        sys.exit(f"public.grid_info should describe one map but has {len(rows)} rows, "
                 "so it can't be moved automatically.")
    old_name, fingerprint, source_file, width, height = rows[0]
    name = (name or old_name or "").strip()
    schema = common.map_schema(name)
    with conn.cursor() as cur:
        cur.execute(common.CATALOGUE_SQL)
        cur.execute("SELECT 1 FROM public.maps WHERE name = %s OR schema_name = %s", (name, schema))
        if cur.fetchone():
            sys.exit(f"A map called '{name}' (schema {schema}) already exists. Choose another --name.")
        cur.execute("SELECT to_regnamespace(%s) IS NOT NULL", (schema,))
        if cur.fetchone()[0]:
            sys.exit(f"The schema {schema} already exists. Choose another --name.")
        cur.execute(f"CREATE SCHEMA {schema}")
        moved = []
        for table in MAP_TABLES:
            if common.table_exists(conn, f"public.{table}"):
                cur.execute(f"ALTER TABLE public.{table} SET SCHEMA {schema}")
                moved.append(table)
        if name != old_name:
            cur.execute(f"UPDATE {schema}.grid_info SET map_version = %s", (name,))
            if "plot_results" in moved:
                cur.execute(f"UPDATE {schema}.plot_results SET map_version = %s WHERE map_version = %s",
                            (name, old_name))
        cur.execute("INSERT INTO public.maps (name, schema_name, source_file, fingerprint, width, height) "
                    "VALUES (%s,%s,%s,%s,%s,%s)", (name, schema, source_file, fingerprint, width, height))
    conn.commit()
    print(f"Moved the map '{name}' into schema {schema}: {', '.join(moved)}")
    return name


def print_list(stored: list[dict]) -> None:
    if not stored:
        print("No maps stored yet. Build and upload one with:\n"
              "  python pipeline.py --name NAME --data-dir YOUR_LAYERS --bbox XMIN YMIN XMAX YMAX")
        return
    for m in stored:
        built = " + ".join(a for a, ok in (("stencils", m["stencils"]), ("outlines", m["outlines"]),
                                           ("evidence", m.get("evidence"))) if ok)
        size = f"{m['width'] or 0:,} x {m['height'] or 0:,} px"
        print(f"  {m['name']:<24} {m['schema']:<26} {size:>18}  {m['source_file'] or '':<26} "
              f"{built or 'nothing built'}")


# --------------------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="set up a fresh database once: the list of maps and PostGIS (schema.sql)")
    sub.add_parser("list", help="list the maps stored in the database")
    u = sub.add_parser("upload", help="upload a results folder (all three approaches) or a 5-colour TIFF "
                                      "(stencils and outlines) as a new map")
    u.add_argument("source", help="a results folder (its path, or its name in layers/), or a TIFF")
    u.add_argument("--name", help="the map's name (default: the folder's or the TIFF's name, e.g. jambi)")
    u.add_argument("--no-outlines", action="store_true",
                   help="leave the outlines out: ~24x less storage; the stencils (and evidence) answer every "
                        "check, but the app can't compare the two approaches on this map")
    u.add_argument("--protected", action="append", metavar="FILE",
                   help="a GeoJSON of protected areas to store with the map (repeat for more; default for a "
                        "results folder: its wdpa*.geojson files)")
    r = sub.add_parser("rename", help="give a map a new name (nothing is copied)")
    r.add_argument("name", help="the map's name now (see: list)")
    r.add_argument("new_name", help="its new name")
    d = sub.add_parser("delete", help="delete a map and everything stored with it")
    d.add_argument("name", help="the map's exact name (see: list)")
    d.add_argument("--yes", action="store_true", help="really delete it - this can't be undone")
    m = sub.add_parser("migrate", help="move a map stored the old way into its own schema")
    m.add_argument("--name", help="the map's name (default: the name it was built with)")
    args = ap.parse_args()
    if args.cmd == "delete" and not args.yes:
        sys.exit(f"This deletes the map '{args.name}' and everything stored with it, for good. "
                 "Add --yes to go ahead.")
    source = upload_source(args.source) if args.cmd == "upload" else None

    with common.connect() as conn:
        if args.cmd == "init":
            done = init(conn)
            print("Set up: public.maps (the list of maps)"
                  + (f", PostGIS {done['postgis']}" if done["postgis"] else "")
                  + ". Each map's tables are made from schema.sql when it is uploaded.")
        elif args.cmd == "list":
            print_list(list_maps(conn, with_evidence=True))
        elif args.cmd == "upload":
            upload(conn, source, args.name or common.default_map_name(os.path.normpath(source)),
                   outlines=not args.no_outlines,
                   protected=[common.find_geojson(f) for f in args.protected] if args.protected else None)
        elif args.cmd == "rename":
            info = rename(conn, args.name, args.new_name)
            print(f"Renamed '{info['old']}' to '{info['name']}' (schema {info['schema']}).")
        elif args.cmd == "delete":
            info = delete(conn, args.name)
            print(f"Deleted the map '{info['name']}' (schema {info['schema']}).")
        else:
            migrate(conn, args.name)


if __name__ == "__main__":
    main()
