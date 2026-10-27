"""
Fix a datum offset in WDPA boundaries (GeoJSON).

Some national protected-area boundaries were digitised on an old local datum
(e.g. Indian 1954 in Thailand) but stored as if they were WGS84, so they sit a
few hundred metres off on satellite imagery. This script re-labels the chosen
countries' coordinates with the datum they were really made in, then converts
them properly to WGS84 (EPSG:4326). Other countries are left untouched.

Usage:
    python fix_wdpa_datum.py input.geojson output.geojson
    python fix_wdpa_datum.py input.geojson output.geojson --iso3 THA --src-crs EPSG:4239

Common source datums to try:
    EPSG:4239  Indian 1954   (Thailand - best fit for the test areas)
    EPSG:4240  Indian 1975   (Thailand, newer)
    EPSG:4238  ID74          (Indonesia)

Requires: pip install geopandas pyproj shapely
"""

import argparse

import geopandas as gpd
import pandas as pd
import shapely


def fix_datum(gdf, iso3_codes, src_crs, iso3_field="iso3"):
    """Return a copy of gdf with the given countries transformed src_crs -> WGS84."""
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")  # GeoJSON is WGS84 by definition

    mask = gdf[iso3_field].isin(iso3_codes)
    to_fix = gdf[mask]
    keep = gdf[~mask].copy()

    # 1 ASSIGN the datum the coordinates were really made in (no change to numbers)
    # 2 REPROJECT to WGS84 (numbers change -> boundary moves to the right place)
    fixed = to_fix.set_crs(src_crs, allow_override=True).to_crs("EPSG:4326")

    fixed["datum_fix"] = f"{src_crs} -> EPSG:4326"
    keep["datum_fix"] = "none"

    out = gpd.GeoDataFrame(pd.concat([fixed, keep]), crs="EPSG:4326")
    return out.loc[gdf.index], mask  # keep original feature order


def report(before, after, mask, utm_crs="EPSG:32647"):
    """Print how far the fixed features moved, plus sanity checks."""
    a = before[mask].to_crs(utm_crs).centroid
    b = after[mask].to_crs(utm_crs).centroid
    dx, dy = b.x - a.x, b.y - a.y

    print(f"Features fixed: {mask.sum()} of {len(before)}")
    print(f"Shift east (m):  {dx.min():.0f} to {dx.max():.0f}  (negative = west)")
    print(f"Shift north (m): {dy.min():.0f} to {dy.max():.0f}  (negative = south)")

    same_vertices = (
        shapely.get_num_coordinates(after.geometry.values)
        == shapely.get_num_coordinates(before.geometry.values)
    ).all()
    print(f"All geometries valid: {after.geometry.is_valid.all()}")
    print(f"Vertex counts unchanged: {same_vertices}")

    untouched = before.index[~mask]
    changed = sum(not after.loc[i].geometry.equals(before.loc[i].geometry) for i in untouched)
    print(f"Other features changed: {changed} (should be 0)")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("input", help="WDPA GeoJSON file")
    p.add_argument("output", help="Corrected GeoJSON file to write")
    p.add_argument("--iso3", nargs="+", default=["THA"], help="Country codes to fix (default: THA)")
    p.add_argument("--src-crs", default="EPSG:4239", help="Datum the data was really made in (default: EPSG:4239 Indian 1954)")
    p.add_argument("--iso3-field", default="iso3", help="Column holding the country code (default: iso3)")
    p.add_argument("--utm-crs", default="EPSG:32647", help="Metric CRS used only for the shift report (default: UTM 47N)")
    args = p.parse_args()

    wdpa = gpd.read_file(args.input)
    fixed, mask = fix_datum(wdpa, args.iso3, args.src_crs, args.iso3_field)
    report(wdpa, fixed, mask, args.utm_crs)

    fixed.to_file(args.output, driver="GeoJSON")
    print(f"Saved: {args.output}")


if __name__ == "__main__":
    main()