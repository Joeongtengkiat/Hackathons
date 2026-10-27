"""
protected_areas.py - protected areas (the World Database on Protected Areas, WDPA) stored with a map.

A map can come with one or more GeoJSON files of protected-area polygons. They are found by name:
the file name starts with 'wdpa' (any case) and ends in .geojson, e.g.
WDPA_Oct2026_Public_IDN-polygons.geojson. They are stored in the map's own schema, in
protected_areas (schema.sql: @protected), cut to the map's extent, and the app draws them over the
map as "protected areas".

  * pipeline.py finds them in --data-dir, gives them to eudr_layers.py (--protected, which sets the
    evidence's 'protected' bit), saves the cut copy in the results folder
    (wdpa_protected_areas.geojson) and uploads them with the map;
  * maps.upload stores the ones in a results folder, or the files it is given (--protected).

Points (WDPA's areas with no boundary) are skipped: there is no shape to draw. An area that is in
several files (same WDPA_PID / WDPAID) is stored once. Needs PostGIS, like the outlines.

Checks: a farm in a protected area needs review. Under the EUDR it is a legality risk (is farming
allowed there?), not proof of deforestation. check() finds those farms in one query, for any batch
of farms; apply() marks them on an approach's answers and turns any lesser verdict into
MANUAL_REVIEW. Deforestation stays FAIL. Stencils, outlines and the TIFF baseline all get the same
step, so they still agree with each other. Evidence-page checks keep Joe's verdict and reason
as he gives them; evidence_approach.outcome makes a deforestation-free plot in a protected area
NEEDS_REVIEW (protected_plots uses check() too).

Commands (run from the backend folder)
    python database/protected_areas.py add "Jambi v1" wdpa_IDN.geojson [more files]   # replaces the map's own
    python database/protected_areas.py show "Jambi v1"                                 # what is stored

Never imports pandas, geopandas or eudr_layers: the app imports this module too.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import shapely
from pyproj import Transformer
from shapely.geometry import MultiPolygon, mapping, shape
from shapely.ops import transform as reproject

import common

PREFIX = "wdpa"                              # a protected-area file's name starts with this (any case)
SUFFIX = ".geojson"
TABLE = "protected_areas"
FOLDER_COPY = "wdpa_protected_areas.geojson"  # the cut copy pipeline.py saves in the results folder
UNNAMED = "unnamed protected area"            # an area whose file gave it no name or WDPA id
JIT_OFF_FARMS = 50                            # switch PostgreSQL's JIT off for batches this big (common.no_jit)


@dataclass
class Area:
    wdpa_id: str | None
    name: str | None
    designation: str | None
    iucn_cat: str | None
    properties: dict
    geom: MultiPolygon       # in the map's coordinate system, cut to the map


def is_protected_file(path) -> bool:
    name = os.path.basename(str(path)).lower()
    return name.startswith(PREFIX) and name.endswith(SUFFIX)


def find_files(folder: str | None) -> list[str]:
    """Every protected-area file in a folder and its sub-folders (wdpa*.geojson), sorted."""
    if not folder or not os.path.isdir(folder):
        return []
    return sorted(str(p) for p in Path(folder).rglob("*") if p.is_file() and is_protected_file(p))


def _text(props: dict, *keys) -> str | None:
    for key in keys:
        value = props.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _polygons(geom):
    """The polygon parts of a shape as one MultiPolygon (None if it has none)."""
    if geom.is_empty:
        return None
    if geom.geom_type == "Polygon":
        return MultiPolygon([geom])
    if geom.geom_type == "MultiPolygon":
        return geom
    parts = [m for m in map(_polygons, getattr(geom, "geoms", [])) if m is not None]
    return MultiPolygon([p for m in parts for p in m.geoms]) if parts else None


def read(paths: list[str], grid: common.Grid) -> list[Area]:
    """The protected areas in these GeoJSON files (longitude/latitude, as GeoJSON always is) that
    overlap the map, each cut to the map's extent and put in the map's coordinate system."""
    to_map = None
    if grid.srid != 4326:
        to_map = Transformer.from_crs(4326, grid.srid, always_xy=True).transform
    extent = grid.bounds_box()
    shapely.prepare(extent)
    areas, seen, skipped = [], set(), 0
    for path in paths:
        for feat in common.read_features(path):
            if not feat.get("geometry"):
                skipped += 1
                continue
            geom = shape(feat["geometry"])
            if geom.geom_type not in ("Polygon", "MultiPolygon"):
                skipped += 1           # WDPA's points: an area with no boundary
                continue
            if not geom.is_valid:
                geom = shapely.make_valid(geom)
            if to_map:
                geom = reproject(to_map, geom)
            if not extent.intersects(geom):
                continue
            geom = _polygons(geom if extent.contains(geom) else geom.intersection(extent))
            if geom is None:
                continue
            props = feat.get("properties") or {}
            wdpa_id = _text(props, "WDPA_PID", "WDPAID", "wdpa_pid", "wdpaid", "id")
            key = wdpa_id or shapely.to_wkb(geom)
            if key in seen:
                continue
            seen.add(key)
            areas.append(Area(wdpa_id, _text(props, "NAME", "ORIG_NAME", "name"),
                              _text(props, "DESIG_ENG", "DESIG", "designation"),
                              _text(props, "IUCN_CAT", "iucn_cat"), props, geom))
    if skipped:
        print(f"  Protected areas: skipped {skipped} feature{'' if skipped == 1 else 's'} with no outline "
              "(points or empty shapes)")
    return areas


def write_geojson(areas: list[Area], grid: common.Grid, path: str) -> None:
    """The areas as a GeoJSON file (longitude/latitude), e.g. the cut copy in a results folder."""
    to_lonlat = None
    if grid.srid != 4326:
        to_lonlat = Transformer.from_crs(grid.srid, 4326, always_xy=True).transform
    features = [{"type": "Feature", "properties": a.properties,
                 "geometry": mapping(reproject(to_lonlat, a.geom) if to_lonlat else a.geom)} for a in areas]
    common.make_parent(path)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"type": "FeatureCollection", "features": features}, fh)


def merge(paths: list[str], path: str) -> None:
    """Several protected-area files as one (for eudr_layers.py's --protected, which takes one file)."""
    features = [f for p in paths for f in common.read_features(p)]
    common.make_parent(path)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"type": "FeatureCollection", "features": features}, fh)


def build(conn, grid: common.Grid, areas: list[Area], report=None) -> None:
    """Store the areas as the map's protected_areas (replacing any it had). Leaves the
    transaction open: the caller commits (maps.upload does, with the rest of the map)."""
    table = grid.table(TABLE)
    if report is not None:
        report("storing the protected areas", 0, 1)
    with conn.cursor() as cur:
        cur.execute(common.schema_sql("postgis"))   # PostGIS lives in public (see maps.init)
        cur.execute(f"DROP TABLE IF EXISTS {table}")
        cur.execute(common.schema_sql("protected", grid))
    rows = [(a.wdpa_id, a.name, a.designation, a.iucn_cat, json.dumps(a.properties, default=str),
             shapely.to_wkb(shapely.set_srid(a.geom, grid.srid), hex=True, include_srid=True)) for a in areas]
    common.copy_rows(conn, table, ["wdpa_id", "name", "designation", "iucn_cat", "properties", "geom"], rows)
    with conn.cursor() as cur:
        cur.execute(common.schema_sql("protected_keys", grid))
    if report is not None:
        report("storing the protected areas", 1, 1)
    print(f"  Protected areas: {len(areas)} stored")


def load(conn, grid: common.Grid) -> dict | None:
    """The map's protected areas as a GeoJSON FeatureCollection in longitude/latitude, simplified
    to half a pixel (what the app draws), in one query. None if the map has none stored."""
    if not common.has_tables(conn, grid, TABLE):
        return None
    tolerance = min(grid.pixel_w, grid.pixel_h) / 2
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT wdpa_id, name, designation, iucn_cat,
                   ST_AsGeoJSON(ST_Transform(ST_SimplifyPreserveTopology(geom, %s), 4326), 6)
            FROM {grid.table(TABLE)} ORDER BY name NULLS LAST, wdpa_id""", (tolerance,))
        rows = cur.fetchall()
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": json.loads(geojson),
         "properties": {"wdpa_id": wdpa_id, "name": name, "designation": designation, "iucn_cat": iucn}}
        for wdpa_id, name, designation, iucn, geojson in rows]}


# --------------------------------------------------------------------------------------
# checks: which farms lie in a protected area
# --------------------------------------------------------------------------------------
@dataclass
class Overlap:
    names: tuple             # the protected areas the farm is in, sorted
    m2: float                # how much of the farm is inside them (0 for a point)


def check(conn, grid: common.Grid, farms: list[common.Farm]) -> dict[int, Overlap]:
    """Which farms lie in a protected area: {index in farms: Overlap}, in one query.
    A point counts if it is inside an area (or on its edge). An outline counts if it overlaps the
    areas by more than the outlines' exact-area rule (common.MIN_OVERLAP_M2 / MIN_OVERLAP_SHARE),
    so a farm that only touches an area along an edge doesn't. Overlapping areas are counted once.
    A map with no protected areas sends nothing (load_grid's list of tables says so). Uses only
    conn.cursor(), execute and fetchall, like the approaches' checks."""
    idx = [i for i, farm in enumerate(farms) if not farm.geom.is_empty]
    if TABLE not in grid.tables or not idx:
        return {}
    geoms = shapely.set_srid(np.asarray([farms[i].geom for i in idx], dtype=object), grid.srid)
    with conn.cursor() as cur:
        if len(idx) >= JIT_OFF_FARMS:
            common.no_jit(cur)
        cur.execute(f"""
            SELECT f.idx,
                   array_agg(DISTINCT COALESCE(NULLIF(p.name, ''), 'WDPA ' || p.wdpa_id, '{UNNAMED}')),
                   ST_AsBinary(ST_Union(CASE WHEN ST_Dimension(f.geom) = 2
                                             THEN ST_Intersection(p.geom, f.geom) END))
            FROM (SELECT q.idx, ST_GeomFromEWKB(q.g) AS geom
                  FROM unnest(%s::int[], %s::bytea[]) AS q(idx, g)) AS f
            JOIN {grid.table(TABLE)} p ON ST_Intersects(p.geom, f.geom)
            GROUP BY f.idx""", (idx, list(shapely.to_wkb(geoms, include_srid=True))))
        rows = cur.fetchall()
    found = {}
    for i, names, inside in rows:
        farm = farms[i]
        if farm.geom.geom_type in ("Polygon", "MultiPolygon"):
            m2 = common.area_m2(shapely.from_wkb(bytes(inside)), grid) if inside else 0.0
            if m2 <= max(common.MIN_OVERLAP_M2, common.MIN_OVERLAP_SHARE * common.area_m2(farm.geom, grid)):
                continue                       # touches an area along its edge only
        else:
            m2 = 0.0
        found[i] = Overlap(tuple(sorted(names)), m2)
    return found


def apply(results: list, found: dict[int, Overlap]) -> None:
    """Mark the farms check() found on one approach's answers (in farm order), and send them for
    review: every verdict less serious than MANUAL_REVIEW becomes MANUAL_REVIEW, and FAIL stays.
    The verdict the map alone gave is kept as map_verdict. Give every approach the same found, so
    their answers stay comparable."""
    review = common.VERDICTS.index(common.MANUAL_REVIEW)
    for i, overlap in found.items():
        res = results[i]
        if res is None:
            continue
        res.protected_areas, res.protected_ha = overlap.names, overlap.m2 / 10_000
        res.map_verdict = res.verdict
        if common.VERDICTS.index(res.verdict) > review:
            res.verdict = common.MANUAL_REVIEW
        res.note = "; ".join(filter(None, [res.note, "in a protected area: " + ", ".join(overlap.names)]))


# --------------------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", help="store protected areas with a map (replaces the ones it has)")
    a.add_argument("map", help="the map's name (see: python database/maps.py list)")
    a.add_argument("files", nargs="+", help="GeoJSON files of protected areas (e.g. wdpa_IDN.geojson)")
    s = sub.add_parser("show", help="list the protected areas stored with a map")
    s.add_argument("map", help="the map's name")
    args = ap.parse_args()
    with common.connect() as conn:
        grid = common.load_grid(conn, args.map)
        if args.cmd == "add":
            files = [common.find_geojson(f) for f in args.files]
            areas = read(files, grid)
            if not areas:
                sys.exit("None of these protected areas overlaps the map, so nothing was stored. "
                         "Check that the files cover the map's area.")
            build(conn, grid, areas)
            conn.commit()
            common.tidy_up(conn, grid.table(TABLE))
            print(f"Stored {len(areas)} protected areas with '{grid.map_version}'.")
        else:
            found = load(conn, grid)
            conn.rollback()
            if found is None:
                print(f"'{grid.map_version}' has no protected areas stored. Add them with:\n"
                      f"  python database/protected_areas.py add \"{grid.map_version}\" wdpa_FILE.geojson")
                return
            for f in found["features"]:
                p = f["properties"]
                print(f"  {p['wdpa_id'] or '':<12} {p['name'] or '(no name)':<40} {p['designation'] or ''}")
            print(f"{len(found['features'])} protected areas")


if __name__ == "__main__":
    main()
