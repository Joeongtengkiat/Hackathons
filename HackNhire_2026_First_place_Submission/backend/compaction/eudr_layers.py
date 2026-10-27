#!/usr/bin/env python3
"""
eudr_layers.py - EUDR deforestation evidence for one area, packed for fast plot queries.
Version 1.1 (14 Oct 2026). SystemEarth Hack & Hire, team hacknhire (NTU).

What it does, in the order it runs:
  1. Reads only the box you ask for from each input raster (whole tiles or clips) and puts
     every layer on one grid (the JRC 10 m grid by default) by nearest neighbour.
  2. Turns ~20 input rasters (2000-2026) into the per-pixel facts the EUDR decision needs and
     packs them into ONE 32-bit number per pixel: evidence.tif (see BIT LAYOUT). Time is kept
     as events (first clearing year per source), not one snapshot per year.
  3. Applies the outcome rule (see OUTCOME RULE) to every pixel: outcome.tif (full detail, a
     colour per outcome and year) and outcome_simple.tif (one colour per kind of result).
  4. Judges plots (--plots, EU Information System GeoJSON) by reading only the window of
     evidence.tif under each plot: facts, verdict and the due diligence columns. The rule runs
     from evidence.tif alone, so --query-only can judge new plots later without the inputs.
  5. Measures itself against the original layers (--verify): every plot is judged again
     straight from the input rasters and every output compared (decision fidelity), the facts
     are compared pixel by pixel (pixel fidelity), and both ways are timed.

Outputs (in --out-dir):
  evidence.tif            Every decision fact for every pixel, packed into one 32-bit value
                          (see BIT LAYOUT). Lossless for the fields it stores. COG.
  outcome.tif / .qml      One outcome class per pixel, with built-in colours and a
                          QGIS legend (loads automatically). COG. One colour per kind of
                          result; the year is in the pixel value (21-27 = confirmed 2021-2027,
                          31-37 probable, 41-47 needs review) and shows when you click a pixel.
  outcome_simple.tif / .qml   Compact version for viewing: one colour per kind of result
                          (no shade per year), and "not forest" split by what the land is
                          (rubber, other plantations/tree cover, open land). COG, plus
                          outcome_simple_preview.png (quick look, no GIS needed).
                          --simple-only rebuilds just this map in an existing results
                          folder in seconds (make_simple_maps.bat does every folder).
  layers.gpkg             Polygons: deforestation (patches by year and outcome),
                          forest_2020 (by forest type), rubber_2020.
  deforestation_by_year.csv   Hectares per outcome and year.
  hansen_vs_multisource.csv   What a Hansen-only rule would flag, against this rule.
  plots_result.csv / .gpkg    Per-plot facts and verdict (with --plots).
  test_plots.geojson, test_plots_result.csv   Generated test plots and their verdicts
                          (with --test-plots N).
  fidelity_plots.csv      Per plot: verdict from evidence.tif, from outcome.tif and straight
                          from the original rasters, which outputs differ, time both ways
                          (with --verify).
  pixel_fidelity.csv      Per input layer: what evidence.tif keeps and the share of its
                          original pixel values that can be read back exactly.
  summary.json            Grid, data profile, sizes, timings, fidelity and checks.
  run_log.txt             Everything the run printed, including any error.

Both .tif files explain themselves: outcome.tif carries its colour table, class names
(LEGEND) and hectares; evidence.tif carries its BIT_LAYOUT and RULE_PARAMS; both carry the
rule, the source files and the date in their metadata. outcome.tif.aux.xml adds an attribute
table so QGIS/ArcGIS show each class's name when you click a pixel.

Inputs (found automatically in --data-dir by file name, or set with --layer):
  required  JRC GFC2020 (forest 2020), plus at least one clearing source:
            Hansen GFC v1.13 lossyear and/or JRC TMF (both are needed for 'confirmed')
  optional  Hansen datamask + treecover2000, JRC GFT2020, TMF AnnualChange_2020..2025,
            DeforestationYear / DegradationYear / TransitionMap_MainClasses,
            rubber map of Southeast Asia c.2020, protected areas (vector, --protected),
            RADD radar alerts (GFW wur_radd_alerts, date_conf) + the UMD primary forest 2001
            map (where RADD watches), Sentinel-2 yearly land cover: Dynamic World (exported
            with dynamic_world_export.js) or Impact Observatory / Esri maps (get_landcover.py),
            near-real-time optical alerts: GFW integrated deforestation alerts (GLAD-L + RADD)
            and DIST-ALERT (Landsat + Sentinel-2), both date_conf (alerts\\get_alerts.bat)
  Files that don't cover the box (e.g. a tile for another region) are skipped.

OUTCOME RULE (per pixel, clearing years 2021-2027). Sources: TMF and Hansen (optical, Landsat)
and, where available, RADD (radar, Sentinel-1; high-confidence alerts count).
  not forest in 2020 (GFC2020)                          -> not deforestation
  forest, not rubber, TMF AND (Hansen OR RADD) record clearing -> confirmed
  forest, not rubber, only TMF records clearing         -> probable
  forest, not rubber, Hansen AND RADD, not TMF          -> probable
  forest, not rubber, only Hansen records clearing      -> needs review (TMF mostly sees canopy
                                                           damage there), except -> damaged forest,
                                                           not deforestation, when RADD watched the
                                                           pixel and saw nothing, or when TMF has
                                                           had 3+ years to judge and still sees
                                                           forest. --hansen-only review / probable
                                                           give the earlier rules.
  forest, not rubber, only RADD records clearing        -> needs review (radar only)
  still needs review (Hansen only or RADD only) and Sentinel-2 land cover shows trees every
  year since -> damaged forest; shows crops, bare ground, built-up or grass now -> probable
  forest, rubber map says rubber, clearing recorded     -> needs review (possible replanting)
  Year = earliest year any source records the clearing.
  After the latest yearly data (TMF / Hansen, now 2025) only near-real-time alerts can see a
  clearing. Optical alerts (GLAD-L, DIST-ALERT) then count on land the yearly data shows as
  standing forest: optical and RADD radar agree (any confidence) -> probable; a high-confidence
  optical alert alone -> needs review (low-confidence ones alone are ignored, as for RADD);
  RADD-only clearing an optical alert confirms -> probable; rubber-map land -> needs review;
  land not forest in 2020 -> cleared, not forest. Alerts from years the yearly data covers are
  ignored: once a year is released, the yearly maps decide it.
  Gaps in a confirmed clearing: TMF's and Hansen's 30 m grids don't line up, so inside a clearing
  both record, a few pixels end up seen by only one of them. A probable or needs-review pixel in a
  gap of up to ~60 m inside a confirmed clearing of the same year (+-1) -> confirmed; a
  needs-review pixel within 30 m of one -> probable. Rubber-map land, 2026 alerts and forest the
  yearly data calls damaged but standing keep their class.

BIT LAYOUT of evidence.tif (32-bit; bit 0 = least significant; all 32 bits used)
  bits  0     gfc_forest    JRC GFC2020 says forest
  bits  1-2   gft_type      0 none, 1 naturally regenerating, 2 primary, 3 planted
  bit   3     rubber        rubber map says rubber in 2020
  bits  4-6   tmf_2020      TMF AnnualChange class in 2020 (0 no data, 1-6)
  bits  7-9   tmf_clear     first TMF clearing after 2020: 0 none, 1-7 = 2021-2027
  bit   10    tmf_degraded  TMF degradation after 2020
  bits 11-13  hansen_loss   Hansen loss year after 2020: 0 none, 1-7 = 2021-2027
  bit   14    hansen_prior  Hansen loss in 2001-2020 (Hansen can't see a later clearing)
  bit   15    protected     inside a protected area (0 if no --protected file)
  bits 16-18  radd_alert    first RADD alert after 2020: 0 none, 1-7 = 2021-2027
  bit   19    radd_high     that RADD alert is high confidence
  bit   20    radd_watch    RADD monitors this pixel (primary forest 2001, no loss 2001-2019)
  bits 21-22  dw_after      Sentinel-2 land cover after the first clearing: 0 unknown, 1 trees
                            every year, 2 now crops/bare/built/grass, 3 mixed
  bits 23-24  land          0 no data, 1 land, 2 water (Hansen data mask), so the rule can run
                            from this file alone
  bits 25-27  alert_year    most recent optical near-real-time alert after 2020 (GLAD-L or
                            DIST-ALERT): 0 none, 1-7 = 2021-2027
  bits 28-29  alert_conf    its confidence: 0 none, 1 low, 2 high
  bits 30-31  context       next to a confirmed clearing of the pixel's own clearing year (+-1):
                            0 no, 1 within 30 m, 2 in a gap of up to ~60 m inside it (worked out
                            once at build time over the whole box, so queries stay per pixel)
  Decode: field = (pixel >> first bit) & (2**bits - 1).

Examples (Windows; on macOS/Linux end lines with \\ instead of ^):
  Build a box, generate 1,000 test plots and check fidelity against the original layers:
    python eudr_layers.py --data-dir "C:\\data\\eudr" --bbox 103.0 -2.3 103.5 -1.8 ^
        --out-dir "C:\\data\\eudr_out" --test-plots 1000 --verify
  Judge a supplier's plots (the box defaults to the plots' extent plus ~1 km):
    python eudr_layers.py --data-dir "C:\\data\\eudr" --plots plots.geojson --out-dir out_plots --verify
  Judge new plots later from evidence.tif alone (no input rasters needed):
    python eudr_layers.py --query-only --out-dir "C:\\data\\eudr_out" --plots new_plots.geojson

Plots: --plots plots.geojson (EU Information System GeoJSON) judges each plot from the compact
layer on disk (only the window under each plot is read). Writes plots_result.csv / .gpkg:
per-plot facts (hectares of 2020 forest, confirmed / probable / review clearing by source and
year), a verdict, and the due diligence columns c1/c2/d1. --plots-csv adds the supplier's own
record for comparison.

Requires: pip install rasterio geopandas pyogrio scipy
"""

import argparse
import json
import math
import os
import re
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy import ndimage

# Geospatial libraries are imported inside the functions that use them, so the
# pure-numpy logic can be tested without them.

# --------------------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------------------

SCRIPT_VERSION = "1.1 (2026-10-14)"
CUTOFF_YEAR = 2020
LAST_CODABLE_YEAR = 2027          # 3-bit year fields: 1..7 = 2021..2027


TMF_SETTLE_YEARS = 3              # TMF calls a clearing only after ~2.5 years without regrowth
# Largest error (in source pixels) allowed when warping a layer in another CRS (the Sentinel-2
# land cover maps, in UTM). GDAL's default (0.125 px) lets the whole box and a plot's window pick
# different source pixels; rasterio refuses 0, and this small an error makes the pick exact in practice.
EXACT_WARP_TOLERANCE = 1e-9


def rule_text(hansen_alone="settle", settled_until=None, radd=False, dynamic_world=False, alerts_after=None):
    """The outcome rule in words, stored inside the output files."""
    if dynamic_world and hansen_alone == "settle":
        tail = ("Still undecided clearings (Hansen only or RADD only): Sentinel-2 land cover (Dynamic "
                f"World or Impact Observatory) shows trees every year since -> damaged forest; now "
                "crops, bare, built or grass -> probable. ")
    else:
        tail = ""
    if hansen_alone == "probable":
        middle = "Forest, not rubber, only one of them records clearing -> probable. "
    else:
        middle = ("Forest, not rubber, only TMF records clearing -> probable. Forest, not rubber, only "
                  "Hansen records clearing -> needs review (TMF mostly sees canopy damage there). ")
        if hansen_alone == "settle" and settled_until:
            middle += (f"Exception: Hansen-only clearing in {CUTOFF_YEAR + 1}-{settled_until}, where TMF "
                       f"still sees forest {TMF_SETTLE_YEARS}+ years later -> damaged forest, not "
                       "deforestation. ")
    if radd:
        middle += ("RADD radar (high-confidence alerts) is a third, independent source: TMF and RADD -> "
                   "confirmed; Hansen and RADD -> probable; RADD only -> needs review"
                   + ("; Hansen only where RADD watched and saw nothing -> damaged forest, not deforestation"
                      if hansen_alone == "settle" else "") + ". ")
    middle += tail
    if alerts_after:
        middle += (f"After the yearly data (from {alerts_after + 1}), optical near-real-time alerts (GLAD-L, "
                   "DIST-ALERT) count on land the yearly data shows as standing forest: optical and RADD radar "
                   "agree -> probable; a high-confidence optical alert alone -> needs review; RADD-only clearing an optical alert "
                   f"confirms -> probable. Alerts up to {alerts_after} are ignored (the yearly maps decide "
                   "those years). ")
    middle += ("Gaps of up to ~60 m inside a confirmed clearing of the same year (+-1), flagged by one "
               "source -> confirmed; needs-review pixels within 30 m of one -> probable (TMF's and Hansen's "
               "30 m grids don't line up); rubber-map land, alerts and damaged-but-standing forest excluded. ")
    return ("Per pixel, clearing years 2021-2027. Not forest on 31 Dec 2020 (JRC GFC2020) -> not "
            "deforestation. Forest, not rubber, TMF and Hansen both record clearing -> confirmed. "
            + middle + "Forest, rubber map says rubber, clearing recorded -> needs review (possible "
            "replanting). Year = earliest year any source records the clearing.")


RULE_TEXT = rule_text()

FIELDS = [                        # (name, bits), packed from bit 0 upward
    ("gfc_forest", 1),
    ("gft_type", 2),
    ("rubber", 1),
    ("tmf_2020", 3),
    ("tmf_clear", 3),
    ("tmf_degraded", 1),
    ("hansen_loss", 3),
    ("hansen_prior", 1),
    ("protected", 1),
    ("radd_alert", 3),
    ("radd_high", 1),
    ("radd_watch", 1),
    ("dw_after", 2),
    ("land", 2),
    ("alert_year", 3),
    ("alert_conf", 2),
    ("context", 2),
]
PACKED_BITS = sum(b for _, b in FIELDS)
assert PACKED_BITS <= 32
PACKED_DTYPE = np.uint16 if PACKED_BITS <= 16 else np.uint32
RADD_EPOCH = np.datetime64("2014-12-31")      # RADD date = days after this, confidence in front
DW_FIRST_YEAR = 2020                           # dynamic_world_export.js writes trees_/label_ per year
DW_NO_DATA = 255
DW_TREES_STAYED = 50                           # median tree probability (%) in every year after
DW_TREES_GONE = 30                             # latest year below this, and one of these labels:
DW_REPLACED_LABELS = (2, 4, 6, 7)              # grass, crops, built, bare
DW_UNKNOWN, DW_STAYED, DW_REPLACED, DW_MIXED = 0, 1, 2, 3
LAND_NODATA, LAND, LAND_WATER = 0, 1, 2       # the 'land' field (Hansen data mask on the grid)
ALERT_LOW, ALERT_HIGH = 1, 2                    # the 'alert_conf' field (0 = no alert)
CTX_NEAR, CTX_GAP = 1, 2                        # the 'context' field (0 = not next to a confirmed clearing)
CONTEXT_RADIUS_M = 30                           # one satellite (Landsat) pixel
# Impact Observatory / Esri annual land cover (io_lulc_<tile>_<year>_*.tif from get_landcover.py):
# class -> Dynamic World label. Rangeland (grass/shrub) maps to shrub, so it never counts as
# "replaced" (young regrowth looks the same).
IO_TO_DW = {1: 0, 2: 1, 4: 3, 5: 4, 7: 6, 8: 7, 9: 8, 11: 5}
IO_TREES = 2
IO_YEAR_RE = re.compile(r"io_lulc_[0-9a-z]+_(\d{4})")

GFT_CODES = {1: 1, 10: 2, 20: 3}  # GFT2020 value -> 2-bit code
GFT_NAMES = {1: "naturally regenerating", 2: "primary", 3: "planted/plantation", 4: "type unknown"}

TMF_UNDISTURBED, TMF_DEGRADED, TMF_DEFORESTED, TMF_REGROWTH, TMF_WATER, TMF_OTHER = 1, 2, 3, 4, 5, 6
TMF_FOREST_LIKE = (TMF_UNDISTURBED, TMF_DEGRADED, TMF_REGROWTH)
TMF_TO_PLANTATION = 41            # TransitionMap_MainClasses: deforested -> tree plantation

# Outcome classes (uint8)
NODATA, WATER, NOT_FOREST, FOREST, FOREST_RUBBER, CLEARED_NON_FOREST, DEGRADED = 0, 1, 2, 3, 4, 5, 6
CONFIRMED, PROBABLE, REVIEW = 20, 30, 40      # + (year - 2020)
GROUP_NAMES = {CONFIRMED: "confirmed", PROBABLE: "probable", REVIEW: "needs review"}

BASE_LABELS = {
    NODATA: ("No data", (0, 0, 0, 0)),
    WATER: ("Water", (166, 206, 227, 255)),
    NOT_FOREST: ("Not forest in 2020", (225, 225, 225, 255)),
    FOREST: ("Forest in 2020, standing", (27, 120, 55, 255)),
    FOREST_RUBBER: ("Forest in 2020 per JRC, rubber per rubber map", (166, 217, 106, 255)),
    CLEARED_NON_FOREST: ("Cleared after 2020, not forest in 2020", (240, 200, 160, 255)),
    DEGRADED: ("Forest in 2020, damaged after 2020 but standing", (140, 150, 60, 255)),
}
GROUP_COLOURS = {   # one colour per kind of result, whatever the year (same as outcome_simple.tif);
    CONFIRMED: (227, 26, 28),   # the year stays in the pixel value (e.g. 23 = confirmed 2023) and
    PROBABLE: (255, 127, 0),    # in the attribute table, so clicking a pixel still shows it
    REVIEW: (106, 61, 154),
}

# Compact display version (outcome_simple.tif): one colour per kind of result instead of a
# shade per year, and "not forest" split by what the land is. Full detail stays in outcome.tif.
(S_NODATA, S_CONFIRMED, S_PROBABLE, S_REVIEW, S_FOREST, S_RUBBER, S_TREES, S_OPEN,
 S_CLEARED, S_WATER) = range(10)
SIMPLE_CLASSES = {  # code: (label, RGBA, counts as deforestation, meaning)
    S_NODATA: ("No data", (0, 0, 0, 0), "no data", "Outside the data."),
    S_CONFIRMED: ("Deforestation after 2020: confirmed", (227, 26, 28, 255), "yes",
                  "Forest on 31 Dec 2020 (JRC GFC2020) cleared in 2021 or later; two independent sources "
                  "record it (TMF and Hansen, or TMF and RADD radar), on the pixel or around it (small gaps "
                  "inside a clearing both record). The year is in outcome.tif."),
    S_PROBABLE: ("Deforestation after 2020: probable", (255, 127, 0, 255), "probably",
                 "Forest on 31 Dec 2020 cleared in 2021 or later according to TMF only, or to Hansen or "
                 "RADD radar backed by a second source (RADD, or Sentinel-2 land cover now showing "
                 "farmland or bare ground), or, after the latest yearly data, by two near-real-time alert "
                 "systems that agree (RADD radar and an optical one: GLAD-L or DIST-ALERT)."),
    S_REVIEW: ("Needs review", (106, 61, 154, 255), "check",
               "Forest in 2020 per JRC, cleared after 2020 on weaker evidence: seen by Hansen only "
               "(TMF mostly sees canopy damage there), by RADD radar only, or, after the latest yearly "
               "data, by one near-real-time alert system only (GLAD-L or DIST-ALERT), or on land the rubber map calls "
               "rubber (possible replanting)."),
    S_FOREST: ("Forest in 2020, still standing", (27, 120, 55, 255), "no",
               "Forest on 31 Dec 2020 (JRC GFC2020), not cleared since. Includes forest damaged after "
               "2020 (TMF degradation, or tree loss seen by Hansen only where TMF, RADD radar or "
               "Sentinel-2 land cover show the forest still there) and forest the rubber map calls "
               "rubber (both shown separately in outcome.tif)."),
    S_RUBBER: ("Not forest: rubber plantation", (154, 205, 90, 255), "no",
               "Not forest in 2020; rubber according to the rubber map of Southeast Asia c.2020."),
    S_TREES: ("Not forest: other plantations and tree cover", (216, 207, 150, 255), "no",
              "Not forest in 2020 per JRC, but tree cover per JRC TMF (or cleared earlier and planted "
              "with trees): mostly plantations such as oil palm, timber, or rubber the rubber map missed."),
    S_OPEN: ("Not forest: farms and other open land", (235, 235, 235, 255), "no",
             "Not forest in 2020 and no tree cover per TMF: farms, young plantations, settlements, bare land."),
    S_CLEARED: ("Not forest, cleared after 2020: harvest or replanting", (184, 121, 42, 255), "no",
                "Not forest on 31 Dec 2020 and cleared since: plantation harvest, replanting or new "
                "farms. Not deforestation under the EUDR."),
    S_WATER: ("Water", (107, 174, 214, 255), "no", "Water per Hansen's data mask or TMF."),
}

# File-name patterns used by --data-dir discovery (lower-case file names).
DISCOVERY = {
    "lossyear": lambda n: "lossyear" in n,
    "datamask": lambda n: "datamask" in n,
    "treecover2000": lambda n: "treecover2000" in n,
    "gfc2020": lambda n: "gfc2020" in n and "subtype" not in n and "gft" not in n,
    "gft2020": lambda n: "gft2020" in n or ("gfc2020" in n and "subtype" in n),
    "tmf_deforestation_year": lambda n: "deforestationyear" in n,
    "tmf_degradation_year": lambda n: "degradationyear" in n,
    "tmf_transition": lambda n: "transitionmap" in n and "main" in n,
    "rubber": lambda n: "rubber" in n and "1km" not in n,
    "radd": lambda n: "radd" in n,
    "dynamic_world": lambda n: "dynamic_world" in n or "dynamicworld" in n,
    "io_lulc": lambda n: "io_lulc" in n,
    "alerts": lambda n: "integrated_alerts" in n,
    "dist_alerts": lambda n: "dist_alert" in n,
    "primary_forest": lambda n: "primary_forest" in n or "primary-forest" in n,
}
# Matches AnnualChange_2020 and JRC's own names such as JRC_TMF_AnnualChange_v1_2020_ASI_...
ANNUAL_RE = re.compile(r"annualchange(?:[_\-]v\d+)?[_\-]?(\d{4})")
REQUIRED = ("gfc2020",)           # plus at least one clearing source: Hansen lossyear or TMF


# --------------------------------------------------------------------------------------
# Pure logic (numpy / scipy only)
# --------------------------------------------------------------------------------------

@dataclass
class Grid:
    """A north-up grid: top-left corner (x0, y0), pixel size (dx, dy<0), size, CRS."""
    x0: float
    y0: float
    dx: float
    dy: float
    width: int
    height: int
    crs: object = None

    @property
    def transform(self):
        from affine import Affine
        return Affine(self.dx, 0.0, self.x0, 0.0, self.dy, self.y0)

    def centres_x(self):
        return self.x0 + (np.arange(self.width) + 0.5) * self.dx

    def centres_y(self):
        return self.y0 + (np.arange(self.height) + 0.5) * self.dy

    @property
    def n_pixels(self):
        return self.width * self.height


def snap_grid(origin_x, origin_y, dx, dy, bbox, crs=None):
    """Grid on the lattice (origin, dx, dy) covering bbox, snapped outward to whole pixels.
    Tiles of one dataset share a lattice, so this works across tile edges too."""
    xmin, ymin, xmax, ymax = bbox
    eps = 1e-6
    c0 = math.floor((xmin - origin_x) / dx + eps)
    c1 = math.ceil((xmax - origin_x) / dx - eps)
    r0 = math.floor((ymax - origin_y) / dy + eps)       # dy < 0: top edge first
    r1 = math.ceil((ymin - origin_y) / dy - eps)
    if c1 <= c0 or r1 <= r0:
        raise ValueError("Empty bbox")
    return Grid(origin_x + c0 * dx, origin_y + r0 * dy, dx, dy, c1 - c0, r1 - r0, crs)


def nearest_indices(centres, origin, step, size):
    """Index of the source pixel that contains each target centre (nearest-neighbour
    resampling for grids in the same CRS), and whether it falls inside the source."""
    idx = np.floor((centres - origin) / step).astype(np.int64)
    return idx, (idx >= 0) & (idx < size)


def row_areas_m2(grid):
    """Exact area of one pixel in each row on the WGS84 ellipsoid (m2), for a lat/lon grid.
    Uses the authalic-latitude formula for a cell bounded by two meridians and two parallels."""
    a = 6378137.0
    f = 1 / 298.257223563
    e2 = f * (2 - f)
    e = math.sqrt(e2)
    edges = np.radians(grid.y0 + np.arange(grid.height + 1) * grid.dy)
    s = np.sin(edges)
    q = s / (1 - e2 * s * s) + np.log((1 + e * s) / (1 - e * s)) / (2 * e)
    dlon = math.radians(abs(grid.dx))
    return 0.5 * a * a * (1 - e2) * dlon * np.abs(np.diff(q))


def year_code(year_array, lo=CUTOFF_YEAR + 1, hi=LAST_CODABLE_YEAR):
    """Calendar years -> 3-bit code (0 = none, 1..7 = 2021..2027)."""
    y = year_array.astype(np.int32)
    return np.where((y >= lo) & (y <= hi), y - CUTOFF_YEAR, 0).astype(np.uint8)


def tmf_clearing(annual, deforestation_year=None):
    """First year after 2020 in which TMF shows clearing: the pixel goes from a forest
    class (undisturbed, degraded, regrowth) to 'deforested' between consecutive years.
    Regrowth that is cleared again shows up here even though Hansen can't record it.
    DeforestationYear > 2020 is also used; the earliest year wins.
    Returns the 3-bit code (0 none, 1..7 = 2021..2027)."""
    shape = next(iter(annual.values())).shape if annual else deforestation_year.shape
    out = np.zeros(shape, np.uint8)
    if annual and CUTOFF_YEAR in annual:
        prev = annual[CUTOFF_YEAR]
        for y in sorted(k for k in annual if k > CUTOFF_YEAR):
            cur = annual[y]
            hit = (out == 0) & np.isin(prev, TMF_FOREST_LIKE) & (cur == TMF_DEFORESTED)
            if y <= LAST_CODABLE_YEAR:
                out[hit] = y - CUTOFF_YEAR
            prev = cur
    if deforestation_year is not None:
        code = year_code(deforestation_year)
        take = (code > 0) & ((out == 0) | (code < out))
        out[take] = code[take]
    return out


def tmf_degradation(annual, degradation_year=None):
    """TMF records degradation after 2020 (undisturbed -> degraded, or DegradationYear > 2020)."""
    shape = next(iter(annual.values())).shape if annual else degradation_year.shape
    out = np.zeros(shape, bool)
    if annual and CUTOFF_YEAR in annual:
        prev = annual[CUTOFF_YEAR]
        for y in sorted(k for k in annual if k > CUTOFF_YEAR):
            cur = annual[y]
            out |= (prev == TMF_UNDISTURBED) & (cur == TMF_DEGRADED)
            prev = cur
    if degradation_year is not None:
        out |= year_code(degradation_year) > 0
    return out


def radd_fields(radd, primary_forest=None, lossyear=None):
    """RADD date_conf values (leading digit 2 = low, 3 = high confidence; then days after
    31 Dec 2014; 0 = no alert) -> (alert year code after 2020, high confidence, watched)."""
    v = radd.astype(np.int32)
    conf, days = v // 10000, v % 10000
    years = (RADD_EPOCH + np.arange(10000).astype("timedelta64[D]")).astype("datetime64[Y]").astype(np.int64) + 1970
    code_of_day = year_code(years)                  # small lookup table: day count -> year code
    code = np.where((v > 0) & np.isin(conf, (2, 3)), code_of_day[days], 0).astype(np.uint8)
    high = ((code > 0) & (conf == 3)).astype(np.uint8)
    watch = v > 0                                   # an alert means RADD was watching
    if primary_forest is not None:
        prior_loss = ((lossyear >= 1) & (lossyear <= 19)) if lossyear is not None else False
        watch |= (primary_forest == 1) & ~prior_loss
    return code, high, watch.astype(np.uint8)


def date_conf_decode(values):
    """GFW date_conf values -> (confidence digit, year code after 2020). The first digit is the
    confidence, the other four the days after 31 Dec 2014; 0 = no alert."""
    v = values.astype(np.int32)
    conf, days = v // 10000, v % 10000
    years = (RADD_EPOCH + np.arange(10000).astype("timedelta64[D]")).astype("datetime64[Y]").astype(np.int64) + 1970
    return conf, np.where(v > 0, year_code(years)[days], 0).astype(np.uint8)


def optical_alerts(integrated=None, dist=None, radd=None):
    """Near-real-time OPTICAL alerts after 2020 -> (year code of the most recent, confidence:
    1 low, 2 high). Sources: GLAD-L (Landsat), taken from GFW's integrated alerts where RADD has no
    alert at the pixel or the alert is 'highest' (more than one system saw it; digit 4), and
    DIST-ALERT (Landsat + Sentinel-2; digit 2 low, 3 high). RADD radar keeps its own fields, so
    agreement between radar and optical can be told apart from one system alone."""
    year = conf = None
    for values, glad_l in ((integrated, True), (dist, False)):
        if values is None:
            continue
        c, code = date_conf_decode(values)
        ok = (code > 0) & np.isin(c, (2, 3, 4) if glad_l else (2, 3))
        if glad_l and radd is not None:
            ok &= (radd == 0) | (c == 4)        # otherwise the integrated alert is RADD's own
        code = np.where(ok, code, 0).astype(np.uint8)
        lvl = np.where(ok, np.where(c == 2, ALERT_LOW, ALERT_HIGH), 0).astype(np.uint8)
        if year is None:
            year, conf = code, lvl
        else:                                   # most recent year wins; same year: higher confidence
            newer = code > year
            same = (code > 0) & (code == year)
            conf = np.where(newer, lvl, np.where(same, np.maximum(conf, lvl), conf)).astype(np.uint8)
            year = np.where(newer, code, year).astype(np.uint8)
    return year, conf


def build_fields(gfc, lossyear, gft=None, rubber=None, tmf_annual=None,
                 tmf_deforestation_year=None, tmf_degradation_year=None,
                 protected=None, gfc_forest_values=(1,), radd=None, primary_forest=None, alerts=None,
                 dist_alerts=None):
    """Per-pixel facts (each a small uint8 array) from the aligned input layers."""
    shape = gfc.shape
    zeros = lambda: np.zeros(shape, np.uint8)
    f = {}
    f["gfc_forest"] = np.isin(gfc, gfc_forest_values).astype(np.uint8)
    if gft is not None:
        g = zeros()
        for value, code in GFT_CODES.items():
            g[gft == value] = code
        f["gft_type"] = g
    else:
        f["gft_type"] = zeros()
    f["rubber"] = (rubber == 1).astype(np.uint8) if rubber is not None else zeros()
    annual = tmf_annual or {}
    if CUTOFF_YEAR in annual:
        t = annual[CUTOFF_YEAR].astype(np.int32)
        f["tmf_2020"] = np.where((t >= 0) & (t <= 7), t, 0).astype(np.uint8)
    else:
        f["tmf_2020"] = zeros()
    if annual or tmf_deforestation_year is not None:
        f["tmf_clear"] = tmf_clearing(annual, tmf_deforestation_year)
    else:
        f["tmf_clear"] = zeros()
    if annual or tmf_degradation_year is not None:
        f["tmf_degraded"] = tmf_degradation(annual, tmf_degradation_year).astype(np.uint8)
    else:
        f["tmf_degraded"] = zeros()
    loss = lossyear.astype(np.int32)
    f["hansen_loss"] = np.where((loss > 20) & (loss <= 27), loss - 20, 0).astype(np.uint8)
    f["hansen_prior"] = ((loss >= 1) & (loss <= 20)).astype(np.uint8)
    f["protected"] = (protected > 0).astype(np.uint8) if protected is not None else zeros()
    if radd is not None:
        f["radd_alert"], f["radd_high"], f["radd_watch"] = radd_fields(radd, primary_forest, loss)
    else:
        f["radd_alert"], f["radd_high"], f["radd_watch"] = zeros(), zeros(), zeros()
    f["dw_after"] = zeros()                     # filled by dw_after_state() when land cover is given
    f["land"] = np.full(shape, LAND, np.uint8)  # set from the data mask by read_inputs()
    if alerts is not None or dist_alerts is not None:
        f["alert_year"], f["alert_conf"] = optical_alerts(alerts, dist_alerts, radd)
    else:
        f["alert_year"], f["alert_conf"] = zeros(), zeros()
    f["context"] = zeros()                      # set by clearing_context() in read_inputs()
    return f


def io_as_dw(classes):
    """Impact Observatory classes -> (tree %, Dynamic World label), so dw_after_state() reads
    both sources the same way (trees = 100 %, other classes 0 %, no data / clouds = 255)."""
    lut_t = np.full(256, DW_NO_DATA, np.uint8)
    lut_l = np.full(256, DW_NO_DATA, np.uint8)
    for io, dw in IO_TO_DW.items():
        lut_t[io] = 100 if io == IO_TREES else 0
        lut_l[io] = dw
    return lut_t[classes], lut_l[classes]


def dw_bands(path):
    """{year: (trees band, label band)} of a Dynamic World export (band names if the file has
    them, otherwise the order dynamic_world_export.js writes: trees_2020, label_2020, ...)."""
    import rasterio
    with rasterio.open(path) as src:
        names, count = list(src.descriptions or []), src.count
    if names and all(n and re.fullmatch(r"(trees|label)_\d{4}", n) for n in names):
        index = {n: i + 1 for i, n in enumerate(names)}
        years = sorted({int(n[-4:]) for n in names})
        return {y: (index[f"trees_{y}"], index[f"label_{y}"]) for y in years
                if f"trees_{y}" in index and f"label_{y}" in index}
    return {DW_FIRST_YEAR + i: (2 * i + 1, 2 * i + 2) for i in range(count // 2)}


def first_event(fields):
    """Year code of the first clearing any source records (TMF, Hansen, high-confidence RADD)."""
    r = np.where(fields["radd_high"] == 1, fields["radd_alert"], 0).astype(np.uint8)
    return earliest_year(earliest_year(fields["tmf_clear"], fields["hansen_loss"]), r)


def dw_after_state(get_year, years, event_code):
    """What Sentinel-2 land cover (Dynamic World) shows in the years AFTER each pixel's first
    clearing. get_year(y) -> (trees % 0-100 or 255, label 0-8 or 255) on the grid.
    Returns DW_UNKNOWN (no clearing, or no clear image after it), DW_STAYED (trees >= 50% every
    year), DW_REPLACED (latest year < 30% trees and crops/bare/built/grass) or DW_MIXED."""
    event_year = np.where(event_code > 0, CUTOFF_YEAR + event_code.astype(np.int32), 9999)
    min_after = last_trees = last_label = None
    seen = None
    for y in sorted(years):
        trees, label = get_year(y)
        if min_after is None:
            min_after = np.full(trees.shape, DW_NO_DATA, np.uint8)
            last_trees = np.full(trees.shape, DW_NO_DATA, np.uint8)
            last_label = np.full(trees.shape, DW_NO_DATA, np.uint8)
            seen = np.zeros(trees.shape, bool)
        after = (trees != DW_NO_DATA) & (y > event_year)
        min_after[after] = np.minimum(min_after[after], trees[after])
        last_trees[after], last_label[after] = trees[after], label[after]
        seen |= after
    state = np.zeros(event_code.shape, np.uint8)
    if seen is None:
        return state
    state[seen & (min_after >= DW_TREES_STAYED)] = DW_STAYED
    gone = seen & (state == 0) & (last_trees < DW_TREES_GONE) & np.isin(last_label, DW_REPLACED_LABELS)
    state[gone] = DW_REPLACED
    state[seen & (state == 0)] = DW_MIXED
    return state


def pack(fields):
    """Pack the per-pixel facts into one unsigned integer per pixel (BIT LAYOUT)."""
    out = None
    shift = 0
    for name, bits in FIELDS:
        v = fields[name].astype(PACKED_DTYPE)
        if v.max(initial=0) >= (1 << bits):
            raise ValueError(f"{name} does not fit in {bits} bits")
        out = (v << shift) if out is None else (out | (v << shift))
        shift += bits
    return out.astype(PACKED_DTYPE)


def unpack(packed):
    """Inverse of pack()."""
    fields, shift = {}, 0
    for name, bits in FIELDS:
        fields[name] = ((packed >> shift) & ((1 << bits) - 1)).astype(np.uint8)
        shift += bits
    return fields


def earliest_year(a, b):
    """Element-wise earliest non-zero year code of two code arrays (0 = none)."""
    a = a.astype(np.uint8)
    b = b.astype(np.uint8)
    both = (a > 0) & (b > 0)
    return np.where(both, np.minimum(a, b), np.maximum(a, b)).astype(np.uint8)


def outcome_classes(fields, valid, water, hansen_alone="review", settled_until=None, alerts_after=None):
    """Apply the outcome rule to every pixel; returns the uint8 outcome grid.
    hansen_alone: where only Hansen records the clearing, "review" or "probable" ("settle" =
    review, plus the exception below). settled_until: last Hansen year that TMF has had time to
    judge; Hansen-only clearing up to then, on land TMF called forest in 2020 and never saw
    cleared, becomes DEGRADED (damaged forest, not deforestation). alerts_after: last year of the
    yearly data (TMF / Hansen); near-real-time alerts count only for later years."""
    forest = fields["gfc_forest"] == 1
    rubber = fields["rubber"] == 1
    t, h = fields["tmf_clear"], fields["hansen_loss"]
    zero = np.zeros(forest.shape, np.uint8)
    r = np.where(fields.get("radd_high", zero) == 1, fields.get("radd_alert", zero), 0).astype(np.uint8)
    tv, hv, rv = t > 0, h > 0, r > 0           # TMF, Hansen, RADD (high confidence) record clearing
    cleared = tv | hv | rv
    year = earliest_year(earliest_year(t, h), r)
    out = np.full(forest.shape, NOT_FOREST, np.uint8)
    out[~forest & cleared] = CLEARED_NON_FOREST
    out[forest & ~cleared] = FOREST
    out[forest & ~cleared & (fields["tmf_degraded"] == 1) & ~rubber] = DEGRADED
    out[forest & ~cleared & rubber] = FOREST_RUBBER
    sel = forest & cleared & rubber
    out[sel] = REVIEW + year[sel]
    keep = forest & ~rubber
    sel = keep & tv & (hv | rv)                 # TMF and a second source
    out[sel] = CONFIRMED + year[sel]
    sel = keep & tv & ~hv & ~rv                 # TMF only
    out[sel] = PROBABLE + year[sel]
    han = keep & ~tv & hv                       # Hansen without TMF
    if hansen_alone == "probable":
        out[han] = PROBABLE + year[han]
    else:
        sel = han & rv                          # Hansen and RADD radar agree
        out[sel] = PROBABLE + year[sel]
        sel = han & ~rv
        out[sel] = REVIEW + year[sel]
        if hansen_alone == "settle":
            # RADD watched the pixel and saw nothing at all: canopy damage, not a clearing
            quiet = fields.get("radd_watch", zero) == 1
            out[han & ~rv & quiet & (fields.get("radd_alert", zero) == 0)] = DEGRADED
            if settled_until:                   # TMF has had time to judge and still sees forest
                settled = han & ~rv & (h <= settled_until - CUTOFF_YEAR) & np.isin(fields["tmf_2020"], TMF_FOREST_LIKE)                out[settled] = DEGRADED
    sel = keep & ~tv & ~hv & rv                 # RADD radar only
    out[sel] = REVIEW + year[sel]
    if hansen_alone == "settle":
        # Still undecided (Hansen only or RADD only): what Sentinel-2 land cover shows afterwards
        dwa = fields.get("dw_after", zero)
        weak = keep & ~tv & (hv | rv) & ((out // 10) * 10 == REVIEW)
        out[weak & (dwa == DW_STAYED)] = DEGRADED
        sel = weak & (dwa == DW_REPLACED)
        out[sel] = PROBABLE + year[sel]
    ctx = fields.get("context", zero)
    if ctx.any():
        # Gaps and edges of a confirmed clearing (see clearing_context): the pixel's own clearing
        # year from the yearly sources and RADD; damaged-but-standing forest, rubber-map land and
        # alert-only pixels (own year 0) keep their class.
        own = earliest_year(earliest_year(t, h), r)
        grp = np.where(out >= CONFIRMED, (out // 10) * 10, 0)
        flagged = keep & (own > 0)
        if alerts_after:                        # after the yearly data only alerts speak: provisional
            flagged &= own <= (alerts_after - CUTOFF_YEAR)
        sel = flagged & (ctx == CTX_GAP) & ((grp == PROBABLE) | (grp == REVIEW))
        out[sel] = CONFIRMED + own[sel]
        sel = flagged & (ctx == CTX_NEAR) & (grp == REVIEW)
        out[sel] = PROBABLE + own[sel]
    if alerts_after:
        # After the yearly data, only near-real-time alerts can see a clearing.
        a = fields.get("alert_year", zero)
        recent = a > (alerts_after - CUTOFF_YEAR)                            # optical alert
        agree = recent & (fields.get("radd_alert", zero) > (alerts_after - CUTOFF_YEAR))  # + radar
        counted = agree | (recent & (fields.get("alert_conf", zero) == ALERT_HIGH))
        standing = keep & np.isin(out, (FOREST, DEGRADED))                  # per the yearly data
        sel = standing & counted
        out[sel] = np.where(agree[sel], PROBABLE, REVIEW) + a[sel]
        sel = forest & rubber & (out == FOREST_RUBBER) & counted            # possible replanting
        out[sel] = REVIEW + a[sel]
        sel = keep & ~tv & ~hv & rv & agree & ((out // 10) * 10 == REVIEW)  # RADD + optical agree
        out[sel] = PROBABLE + year[sel]
        out[~forest & (out == NOT_FOREST) & counted] = CLEARED_NON_FOREST
    out[water] = WATER
    out[~valid] = NODATA
    return out


def land_field(valid, datamask):
    """The 2-bit 'land' fact: 0 no data, 1 land, 2 water (Hansen data mask)."""
    return np.where(valid, np.where(datamask == 2, LAND_WATER, LAND), LAND_NODATA).astype(np.uint8)


def context_radius(grid):
    """CONTEXT_RADIUS_M in grid pixels (3 on the 10 m grid, 1 on the 30 m grid); from the pixel
    width alone, so the whole box and any window of it get the same value."""
    return max(1, int(round(CONTEXT_RADIUS_M / (abs(grid.dx) * 111320.0))))


def disk(radius):
    yy, xx = np.mgrid[-radius:radius + 1, -radius:radius + 1]
    return (xx * xx + yy * yy) <= radius * radius


def clearing_context(fields, radius):
    """The 'context' fact: for each forest pixel (not rubber-map land) that some yearly source or
    RADD records cleared in year y but that is not itself confirmed, whether a confirmed clearing
    of year y-1..y+1 is around it: CTX_GAP if the pixel lies in a gap of that clearing narrower than
    about two satellite pixels (morphological closing with a disk of `radius` pixels), CTX_NEAR if
    it lies within `radius` pixels of it. Confirmed pixels are the same under every rule variant,
    so this fact is too. Uses only pixels within 2 x radius, so a plot's window read with that
    margin reproduces it exactly (see read_window_fields)."""
    from scipy import ndimage
    base = dict(fields)
    base["context"] = np.zeros(fields["gfc_forest"].shape, np.uint8)
    oc = decide(base)
    red = (oc >= CONFIRMED) & ((oc // 10) * 10 == CONFIRMED)
    red_year = np.where(red, oc % 10, 0)
    own = first_event(fields)
    cand = (fields["gfc_forest"] == 1) & (fields["rubber"] == 0) & ~red & (own > 0)
    ctx = np.zeros(oc.shape, np.uint8)
    if not (red.any() and cand.any()):
        return ctx
    st = disk(radius)
    for y in np.unique(own[cand]):
        core = red & (np.abs(red_year.astype(np.int16) - int(y)) <= 1)
        if not core.any():
            continue
        mine = cand & (own == y)
        near = ndimage.binary_dilation(core, st) & mine
        gap = ndimage.binary_closing(core, st) & mine
        ctx[near] = np.maximum(ctx[near], CTX_NEAR)
        ctx[gap] = CTX_GAP
    return ctx


def decide(fields, hansen_alone="settle", settled_until=None, alerts_after=None):
    """The outcome rule run from the packed facts alone (evidence.tif): where there is data and
    where there is water come from the 'land' field and TMF's 2020 class."""
    land = fields["land"]
    water = (land == LAND_WATER) | (fields["tmf_2020"] == TMF_WATER)
    return outcome_classes(fields, land != LAND_NODATA, water, hansen_alone, settled_until, alerts_after)


def settled_year(hansen_alone, tmf_last_year):
    """Last Hansen year TMF has had time to judge (TMF_SETTLE_YEARS before its latest yearly
    map), used by the 'settle' rule; None when it doesn't apply."""
    if hansen_alone != "settle" or not tmf_last_year:
        return None
    y = int(tmf_last_year) - TMF_SETTLE_YEARS
    return y if y > CUTOFF_YEAR else None


TEST_STRATA = (   # where --test-plots centres half of its plots: one share per kind of outcome
    ("confirmed", lambda o: (o // 10) * 10 == CONFIRMED),
    ("probable", lambda o: (o // 10) * 10 == PROBABLE),
    ("needs review", lambda o: (o // 10) * 10 == REVIEW),
    ("damaged forest", lambda o: o == DEGRADED),
    ("forest standing", lambda o: o == FOREST),
    ("forest per JRC, rubber per map", lambda o: o == FOREST_RUBBER),
    ("cleared, not forest in 2020", lambda o: o == CLEARED_NON_FOREST),
    ("flagged 2026 or later (alerts)", lambda o: (o >= CONFIRMED) & (o % 10 >= 6)),
)


def make_test_plots(n, grid, outcome, seed=0, country=""):
    """n synthetic smallholder plots inside the box, as EU Information System GeoJSON (a dict).
    Half sit at random places; the other half are centred on pixels of each kind of outcome
    (confirmed, probable, needs review, damaged forest, standing forest, rubber, cleared
    non-forest), so a fidelity check exercises every branch of the rule. Shapes: rotated
    rectangles of 0.3-10 ha (median 2 ha, like smallholder rubber farms); about 1 in 20 is a
    point with an Area (the EU format allows points for plots up to 4 ha)."""
    rng = np.random.default_rng(seed)
    h, w = outcome.shape
    lat_mid = grid.y0 + grid.dy * h / 2
    m_x = 111320 * math.cos(math.radians(lat_mid))          # metres per degree
    m_y = 110574.0
    margin = int(math.ceil(300 / min(abs(grid.dx) * m_x, abs(grid.dy) * m_y)))
    margin = min(margin, max(0, (min(h, w) - 2) // 2))
    lo_r, hi_r, lo_c, hi_c = margin, h - margin, margin, w - margin

    def sample(pred, k):
        """k pixel positions (row, col) inside the margins where pred(outcome) holds."""
        got_r, got_c = [], []
        for _ in range(40):
            r = rng.integers(lo_r, hi_r, 200_000)
            c = rng.integers(lo_c, hi_c, 200_000)
            ok = pred(outcome[r, c]) if pred is not None else np.ones(r.size, bool)
            got_r.extend(r[ok][:k - len(got_r)])
            got_c.extend(c[ok][:k - len(got_c)])
            if len(got_r) >= k:
                break
        if len(got_r) < k and pred is not None:              # rare outcome: list every pixel of it
            rr, cc = np.nonzero(pred(outcome[lo_r:hi_r, lo_c:hi_c]))
            if rr.size:
                pick = rng.integers(0, rr.size, k - len(got_r))
                got_r.extend(rr[pick] + lo_r)
                got_c.extend(cc[pick] + lo_c)
        return list(zip(got_r, got_c))

    centres = []
    present = [(name, pred) for name, pred in TEST_STRATA
               if pred(outcome[lo_r:hi_r:7, lo_c:hi_c:7]).any() or pred(outcome[lo_r:hi_r, lo_c:hi_c]).any()]
    targeted = n // 2 if present else 0
    for i, (name, pred) in enumerate(present):
        k = targeted // len(present) + (1 if i < targeted % len(present) else 0)
        centres += [(r, c, name) for r, c in sample(pred, k)]
    centres += [(r, c, "random") for r, c in sample(None, n - len(centres))]

    features = []
    for i, (r, c, stratum) in enumerate(centres):
        lon = grid.x0 + (c + rng.uniform(0.2, 0.8)) * grid.dx
        lat = grid.y0 + (r + rng.uniform(0.2, 0.8)) * grid.dy
        area_ha = float(np.clip(np.exp(rng.normal(math.log(2.0), 0.7)), 0.3, 10.0))
        if rng.random() < 0.05:
            area_ha = min(area_ha, 4.0)
            geometry = {"type": "Point", "coordinates": [round(lon, 7), round(lat, 7)]}
        else:
            aspect, theta = rng.uniform(1.0, 2.5), rng.uniform(0.0, math.pi)
            width = math.sqrt(area_ha * 1e4 / aspect)
            length = aspect * width
            cos_t, sin_t = math.cos(theta), math.sin(theta)
            mx = 111320 * math.cos(math.radians(lat))
            ring = []
            for x, y in ((-length / 2, -width / 2), (length / 2, -width / 2),
                         (length / 2, width / 2), (-length / 2, width / 2)):
                xr, yr = x * cos_t - y * sin_t, x * sin_t + y * cos_t
                ring.append([round(lon + xr / mx, 7), round(lat + yr / m_y, 7)])
            ring.append(ring[0])
            geometry = {"type": "Polygon", "coordinates": [ring]}
        features.append({"type": "Feature", "geometry": geometry, "properties": {
            "ProducerName": "Test producer", "ProducerCountry": country,
            "ProductionPlace": f"TEST-{i + 1:05d}", "Area": round(area_ha, 4), "test_stratum": stratum}})
    return {"type": "FeatureCollection", "features": features}


def compare_outputs(a, b):
    """Names of the plot outputs (facts, verdict, reason) that differ between two results."""
    def same(x, y):
        if isinstance(x, float) or isinstance(y, float):
            try:
                return abs(float(x) - float(y)) <= 1e-9
            except (TypeError, ValueError):
                return False
        return x == y
    keys = list(a) + [k for k in b if k not in a]
    return [k for k in keys if k not in a or k not in b or not same(a[k], b[k])]


def pixel_fidelity(layers, fields, forest_values=(1,)):
    """How much of each original layer evidence.tif keeps, on the common grid: what it is stored
    as, the share of pixels whose original value can be read back exactly, and what is lost.
    layers: aligned original arrays from read_inputs() (None where absent); fields: the facts
    decoded from evidence.tif."""
    rows = []

    def add(layer, stored, exact, lost_if_any):
        pct = None if exact is None else round(100 * float(exact), 4)
        rows.append({"layer": layer, "stored_as": stored, "exact_values_recovered_pct": pct,
                     "what_is_lost": "nothing" if pct == 100 else lost_if_any})

    g = layers.get("gfc")
    if g is not None:
        dec = np.where(fields["gfc_forest"] == 1, np.int16(forest_values[0]), np.int16(0))
        add("JRC GFC2020 (forest on 31 Dec 2020)", "gfc_forest (1 bit)", np.mean(g == dec),
            "values other than forest / not forest")
    if layers.get("gft") is not None:
        dec = np.array([0, 1, 10, 20], np.uint8)[fields["gft_type"]]
        add("JRC GFT2020 (forest type)", "gft_type (2 bits)", np.mean(layers["gft"] == dec),
            "values other than 0, 1, 10, 20")
    if layers.get("rubber") is not None:
        add("Rubber map of Southeast Asia c.2020", "rubber (1 bit)",
            np.mean(layers["rubber"] == fields["rubber"]),
            "'other tree cover' (0) and no data (-99) are both stored as 0")
    ly = layers.get("lossyear")
    if ly is not None and layers.get("lossyear_found", True):
        prior = fields["hansen_prior"] == 1
        dec = np.where(fields["hansen_loss"] > 0, fields["hansen_loss"].astype(np.int16) + np.int16(20),
                       np.where(prior, np.int16(-1), np.int16(0)))
        add("Hansen GFC lossyear", "hansen_loss (3 bits) + hansen_prior (1 bit)", np.mean(ly == dec),
            "the year of losses in 2001-2020 (kept as one 'lost before 2021' bit); "
            "losses after 2020 keep their year")
    dm = layers.get("datamask")
    if dm is not None and layers.get("datamask_found", True):
        add("Hansen datamask", "land (2 bits)", np.mean(dm == fields["land"]),
            "pixels outside the loss layer's coverage are stored as no data")
    annual = layers.get("tmf_annual") or {}
    if CUTOFF_YEAR in annual:
        add(f"JRC TMF AnnualChange {CUTOFF_YEAR}", "tmf_2020 (3 bits)",
            np.mean(annual[CUTOFF_YEAR] == fields["tmf_2020"]), "class values above 7")
    later = sorted(y for y in annual if y > CUTOFF_YEAR)
    if later or layers.get("defo") is not None:
        add(f"JRC TMF AnnualChange {later[0]}-{later[-1]}" if later else "JRC TMF DeforestationYear",
            "tmf_clear (3 bits) + tmf_degraded (1 bit)", None,
            "the class in each year (kept as the first clearing year after 2020 and whether the "
            "forest was degraded after 2020); DeforestationYear/DegradationYear before 2021")
    if layers.get("radd") is not None:
        add("RADD radar alerts (date_conf)", "radd_alert (3 bits) + radd_high (1 bit)",
            np.mean(layers["radd"] == 0),
            "the day of each alert (kept: year of the first alert after 2020 and its confidence); "
            "alerts before 2021")
    if layers.get("alerts") is not None:
        add("GFW integrated alerts (GLAD-L + RADD, date_conf)", "alert_year (3 bits) + alert_conf (2 bits)",
            np.mean(layers["alerts"] == 0),
            "the day of each alert and alerts before 2021; RADD's part is kept in the RADD fields, GLAD-L's "
            "as the year of the most recent optical alert and its confidence")
    if layers.get("dist_alerts") is not None:
        add("DIST-ALERT (Landsat + Sentinel-2, date_conf)", "alert_year (3 bits) + alert_conf (2 bits)",
            np.mean(layers["dist_alerts"] == 0),
            "the day of each alert (kept: year of the most recent optical alert and its confidence, "
            "shared with GLAD-L)")
    if layers.get("primary") is not None:
        add("UMD primary forest 2001", "part of radd_watch (1 bit)", None,
            "kept only as 'RADD watches here' (primary forest 2001 and no Hansen loss 2001-2019)")
    if layers.get("lc_name"):
        add(f"Sentinel-2 land cover ({layers['lc_name']})", "dw_after (2 bits)", None,
            "the class in each year (kept as what the land became after the first clearing)")
    if layers.get("protected") is not None:
        add("Protected areas (polygons)", "protected (1 bit)",
            np.mean((layers["protected"] > 0) == (fields["protected"] == 1)), "nothing beyond 10 m detail")
    if layers.get("treecover_found"):
        add("Hansen treecover2000", "not stored", 0.0,
            "everything: only the Hansen-only comparison rule (canopy > 10%) uses it, not the EUDR decision")
    if layers.get("transition_found"):
        add("JRC TMF TransitionMap_MainClasses", "not stored", 0.0,
            "everything: only the viewing map (outcome_simple.tif) uses it")
    return rows


def fidelity_summary(rows, px):
    """Decision and pixel fidelity totals from verify_plots() rows and pixel counts."""
    n = len(rows)
    if not n:
        return {"plots_compared": 0}
    pct = lambda k: round(100.0 * k / n, 2)
    ok = sum(bool(r["all_outputs_match"]) for r in rows)
    same_v = sum(r["verdict_from_evidence"] == r["verdict_from_original_layers"] for r in rows)
    map_ok = sum(bool(r["outcome_map_matches"]) for r in rows)
    te = np.array([r["evidence_ms"] for r in rows], float)
    to = np.array([r["original_layers_ms"] for r in rows], float)
    verdicts = {}
    for r in rows:
        verdicts[r["verdict_from_original_layers"]] = verdicts.get(r["verdict_from_original_layers"], 0) + 1
    sets = {}
    for r in rows:
        sets[r["set"]] = sets.get(r["set"], 0) + 1
    npx = max(1, px["pixels_compared"])
    return {
        "plots_compared": n, "plots_by_set": sets,
        "decision_fidelity_pct": pct(ok), "plots_with_every_output_identical": ok,
        "outputs_compared_per_plot": px.get("outputs_per_plot"),
        "same_verdict_pct": pct(same_v), "outcome_tif_gives_same_outputs_pct": pct(map_ok),
        "verdicts_from_original_layers": verdicts,
        "pixels_compared": px["pixels_compared"],
        "pixel_fidelity_pct_by_field": {k: round(100.0 * (1 - v / npx), 6)
                                        for k, v in px["field_mismatches"].items()},
        "outcome_pixels_identical_pct": round(100.0 * (1 - px["outcome_mismatches"] / npx), 6),
        "query_ms_median": {"evidence_tif": round(float(np.median(te)), 3),
                            "original_layers": round(float(np.median(to)), 3)},
        "query_ms_mean": {"evidence_tif": round(float(te.mean()), 3),
                          "original_layers": round(float(to.mean()), 3)},
        "query_seconds_total": {"evidence_tif": round(float(te.sum()) / 1000, 3),
                                "original_layers": round(float(to.sum()) / 1000, 3)},
        "speedup_median": round(float(np.median(to) / max(np.median(te), 1e-9)), 1),
        "files_read_per_plot": {"evidence_tif": 1, "original_layers": px.get("original_files")},
    }


def hansen_only(treecover, lossyear, datamask, threshold=10):
    """The naive rule most tools start with: tree cover > threshold in 2000, not lost by
    2020, lost after 2020. Returns (flagged, year_code)."""
    loss = lossyear.astype(np.int32)
    forest_2020 = (treecover > threshold) & (datamask == 1) & ~((loss >= 1) & (loss <= 20))
    flagged = forest_2020 & (loss > 20) & (loss <= 27)
    return flagged, np.where(flagged, loss - 20, 0).astype(np.uint8)


def class_areas_ha(values, row_area, n_classes=256):
    """Hectares per class value for a uint8 grid with per-row pixel areas."""
    totals = np.zeros(n_classes, np.float64)
    for r in range(values.shape[0]):
        totals += np.bincount(values[r], minlength=n_classes)[:n_classes] * row_area[r]
    return totals / 1e4


def outcome_label(code):
    if code in BASE_LABELS:
        return BASE_LABELS[code][0]
    group = (code // 10) * 10
    if group in GROUP_NAMES:
        return f"Deforestation {GROUP_NAMES[group]}, {CUTOFF_YEAR + code - group}"
    return f"class {code}"


def outcome_colormap():
    cmap = {k: v[1] for k, v in BASE_LABELS.items()}
    for group, rgb in GROUP_COLOURS.items():
        for i in range(LAST_CODABLE_YEAR - CUTOFF_YEAR):
            cmap[group + i + 1] = (*rgb, 255)
    return cmap


def outcome_status(code):
    """Does this outcome class count as deforestation under the EUDR?"""
    if code == NODATA:
        return "no data"
    if code == FOREST_RUBBER:
        return "no (disputed land: forest per JRC, rubber per rubber map)"
    group = (code // 10) * 10
    return {CONFIRMED: "yes", PROBABLE: "probably",
            REVIEW: "check (seen by Hansen only, or rubber per the rubber map)"}.get(group, "no")


def outcome_legend():
    """{code: {label, colour, deforestation}} for every class the outcome layer can hold."""
    return {code: {"label": outcome_label(code), "colour": "#{:02X}{:02X}{:02X}".format(*rgba[:3]),
                   "deforestation": outcome_status(code)}
            for code, rgba in sorted(outcome_colormap().items())}


def simple_outcome(outcome, fields, transition=None):
    """Compact classes for viewing (SIMPLE_CLASSES): one class per kind of result, without
    years, and "not forest" split by what the land is. Built from the outcome grid, the
    evidence fields and, when given, TMF's transition map (41 = cleared and planted with trees)."""
    group = np.where(outcome >= CONFIRMED, (outcome // 10) * 10, 0)
    s = np.full(outcome.shape, S_NODATA, np.uint8)
    s[group == CONFIRMED] = S_CONFIRMED
    s[group == PROBABLE] = S_PROBABLE
    s[group == REVIEW] = S_REVIEW
    s[np.isin(outcome, (FOREST, DEGRADED, FOREST_RUBBER))] = S_FOREST
    not_forest = outcome == NOT_FOREST
    rubber = fields["rubber"] == 1
    trees = np.isin(fields["tmf_2020"], TMF_FOREST_LIKE)
    if transition is not None:
        trees |= transition == TMF_TO_PLANTATION
    s[not_forest & rubber] = S_RUBBER
    s[not_forest & ~rubber & trees] = S_TREES
    s[not_forest & ~rubber & ~trees] = S_OPEN
    s[outcome == CLEARED_NON_FOREST] = S_CLEARED
    s[outcome == WATER] = S_WATER
    return s


def simple_colormap():
    return {code: v[1] for code, v in SIMPLE_CLASSES.items()}


def simple_legend(has_rubber=True):
    """{code: {label, colour, deforestation, meaning}} for outcome_simple.tif."""
    out = {}
    for code, (label, rgba, status, meaning) in SIMPLE_CLASSES.items():
        if code == S_TREES and not has_rubber:
            label = "Not forest: plantations and other tree cover"
            meaning += " This run had no rubber map, so rubber plantations are in this class or in open land."
        out[code] = {"label": label, "colour": "#{:02X}{:02X}{:02X}".format(*rgba[:3]),
                     "deforestation": status, "meaning": meaning}
    return out


def legend_entries(legend=None, colormap=None):
    """[(code, label, RGBA, deforestation status)] in code order; default: the full outcome."""
    if legend is None:
        legend, colormap = outcome_legend(), outcome_colormap()
    return [(c, legend[c]["label"], colormap[c], legend[c]["deforestation"]) for c in sorted(colormap)]


def mode_downsample(a, step):
    """Most common class in each step x step block (ties go to the lower code, so the
    deforestation classes win ties in the compact map). For previews of class maps."""
    if step <= 1:
        return a
    h, w = (a.shape[0] // step) * step, (a.shape[1] // step) * step
    blocks = a[:h, :w].reshape(h // step, step, w // step, step)
    best = np.zeros((h // step, w // step), a.dtype)
    best_n = np.full(best.shape, -1, np.int32)
    for v in np.flatnonzero(np.bincount(a[:h, :w].ravel())):
        n = (blocks == v).sum(axis=(1, 3), dtype=np.int32)
        take = n > best_n
        best[take], best_n[take] = v, n[take]
    return best


def write_png(path, arr, colormap):
    """Write a uint8 class grid as an indexed-colour PNG with transparency (no extra libraries)."""
    import struct
    import zlib
    h, w = arr.shape
    n = max(int(arr.max()) if arr.size else 0, max(colormap)) + 1
    palette = b"".join(bytes(colormap.get(i, (0, 0, 0, 0))[:3]) for i in range(n))
    alpha = bytes(colormap.get(i, (0, 0, 0, 0))[3] for i in range(n))
    raw = np.zeros((h, w + 1), np.uint8)       # each row starts with filter type 0
    raw[:, 1:] = arr

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    Path(path).write_bytes(b"\x89PNG\r\n\x1a\n"
                           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 3, 0, 0, 0))
                           + chunk(b"PLTE", palette) + chunk(b"tRNS", alpha)
                           + chunk(b"IDAT", zlib.compress(raw.tobytes(), 6)) + chunk(b"IEND", b""))


FIELD_MEANINGS = {
    "gfc_forest": "1 = forest on 31 Dec 2020 (JRC GFC2020)",
    "gft_type": "0 none, 1 naturally regenerating, 2 primary, 3 planted/plantation (JRC GFT2020)",
    "rubber": "1 = rubber in 2020 (rubber map of Southeast Asia c.2020)",
    "tmf_2020": "JRC TMF class in 2020: 0 no data, 1 undisturbed, 2 degraded, 3 deforested, "
                "4 regrowth, 5 water, 6 other",
    "tmf_clear": "first TMF clearing after 2020: 0 none, 1-7 = 2021-2027",
    "tmf_degraded": "1 = TMF degradation after 2020",
    "hansen_loss": "Hansen loss year after 2020: 0 none, 1-7 = 2021-2027",
    "hansen_prior": "1 = Hansen loss in 2001-2020 (Hansen can't record a later clearing)",
    "protected": "1 = inside a protected area (0 when no protected-area file was given)",
    "radd_alert": "first RADD radar alert after 2020 (any confidence): 0 none, 1-7 = 2021-2027",
    "radd_high": "1 = that RADD alert is high confidence (confirmed by later radar images)",
    "radd_watch": "1 = RADD monitors this pixel (UMD primary forest 2001, no Hansen loss 2001-2019)",
    "dw_after": "Sentinel-2 land cover (Dynamic World or Impact Observatory) in the years after the first recorded clearing: "
                "0 unknown / no clearing, 1 trees every year, 2 now crops, bare, built or grass, 3 mixed",
    "land": "0 no data (outside the inputs), 1 land, 2 water (Hansen data mask); with tmf_2020 it lets "
            "the rule run from this file alone",
    "alert_year": "most recent optical near-real-time alert after 2020 (GLAD-L from GFW's integrated alerts, "
                  "or DIST-ALERT): 0 none, 1-7 = 2021-2027",
    "alert_conf": "confidence of that alert: 0 none, 1 low, 2 high",
    "context": "next to a confirmed clearing of the pixel's own clearing year (+-1): 0 no, 1 within 30 m, "
               "2 in a gap of up to ~60 m inside it (TMF's and Hansen's 30 m grids don't line up, so a "
               "few pixels inside a clearing end up seen by only one of them)",
}


def field_layout():
    """Bit layout of evidence.tif: [{field, bits, meaning}], bit 0 = least significant."""
    out, offset = [], 0
    for name, bits in FIELDS:
        span = str(offset) if bits == 1 else f"{offset}-{offset + bits - 1}"
        out.append({"field": name, "bits": span, "meaning": FIELD_MEANINGS[name]})
        offset += bits
    return out


def plot_facts(evidence_w, outcome_w, mask, row_area_w, alerts_after=None):
    """Facts for one plot from a window of the compact layers: the evidence (packed values, or
    the decoded fields), the outcome classes, the plot's pixel mask and each window row's pixel
    area (m2). Hectares throughout. alerts_after: last year of the yearly data (see the rule)."""
    f = evidence_w if isinstance(evidence_w, dict) else unpack(evidence_w)
    px_ha = np.broadcast_to((np.asarray(row_area_w, np.float64) / 1e4)[:, None], mask.shape)

    def ha(m):
        return round(float(px_ha[m & mask].sum()), 4)

    grp = (outcome_w // 10) * 10
    conf, prob, rev = grp == CONFIRMED, grp == PROBABLE, grp == REVIEW
    defo = conf | prob | rev
    years = sorted({CUTOFF_YEAR + int(c) % 10 for c in np.unique(outcome_w[defo & mask])})
    tmf, han = f["tmf_clear"] > 0, f["hansen_loss"] > 0
    radd = (f["radd_high"] == 1) & (f["radd_alert"] > 0)
    if alerts_after:
        recent = f["alert_year"] > (alerts_after - CUTOFF_YEAR)
        radar = f["radd_alert"] > (alerts_after - CUTOFF_YEAR)
        recent &= (f["alert_conf"] == ALERT_HIGH) | radar      # the alerts the rule counts
    else:
        recent = radar = np.zeros(mask.shape, bool)
    # needs review because of a recent optical alert: its year is the alert's year
    alert_rev = rev & recent & ((outcome_w % 10) == f["alert_year"]) & ~tmf & (f["radd_alert"] == 0)
    return {
        "pixels": int(mask.sum()), "area_ha": ha(np.ones(mask.shape, bool)),
        "forest_2020_ha": ha(f["gfc_forest"] == 1),
        "planted_forest_2020_ha": ha(f["gft_type"] == 3),
        "forest_standing_ha": ha(outcome_w == FOREST),
        "forest_degraded_ha": ha(outcome_w == DEGRADED),
        "hansen_only_damage_ha": ha((outcome_w == DEGRADED) & han & ~tmf),
        "radd_watched_ha": ha(f["radd_watch"] == 1),
        "radd_alert_on_2020_forest_ha": ha(radd & (f["gfc_forest"] == 1)),
        "review_radd_only_ha": ha(rev & radd & ~han & ~tmf & (f["rubber"] == 0)),
        "dw_trees_stayed_ha": ha((f["dw_after"] == DW_STAYED) & (f["gfc_forest"] == 1)),
        "dw_replaced_ha": ha((f["dw_after"] == DW_REPLACED) & (f["gfc_forest"] == 1)),
        "forest_but_rubber_ha": ha(outcome_w == FOREST_RUBBER),
        "not_forest_2020_ha": ha(outcome_w == NOT_FOREST),
        "cleared_not_forest_2020_ha": ha(outcome_w == CLEARED_NON_FOREST),
        "water_ha": ha(outcome_w == WATER),
        "confirmed_ha": ha(conf), "probable_ha": ha(prob), "review_ha": ha(rev),
        "confirmed_in_gap_ha": ha(conf & (f["context"] == CTX_GAP) & ~(tmf & (han | radd))),
        "probable_next_to_confirmed_ha": ha(prob & (f["context"] == CTX_NEAR) & ~tmf),
        "probable_tmf_only_ha": ha(prob & tmf & ~han), "probable_hansen_only_ha": ha(prob & han & ~tmf),
        "review_hansen_only_ha": ha(rev & han & ~tmf & ~radd & (f["rubber"] == 0) & ~alert_rev),
        "review_rubber_ha": ha(rev & (f["rubber"] == 1) & ~alert_rev),
        "recent_alert_on_2020_forest_ha": ha(recent & (f["gfc_forest"] == 1)),
        "review_recent_alert_ha": ha(alert_rev),
        "probable_alerts_agree_ha": ha(prob & recent & radar & ~tmf),
        "deforestation_years": " ".join(map(str, years)),
        "first_deforestation_year": years[0] if years else "",
        "hansen_loss_2021_2023_ha": ha((f["hansen_loss"] >= 1) & (f["hansen_loss"] <= 3)),
        "hansen_loss_2021_2025_ha": ha(han),
        "protected_ha": ha(f["protected"] == 1),
    }


def plot_verdict(facts, min_ha=0.0):
    """(verdict, reason) for one plot from its facts. min_ha ignores slivers at plot edges."""
    if facts["pixels"] == 0:
        return "not assessed", "Plot lies outside the processed area"
    if facts["confirmed_ha"] > min_ha:
        gap = facts.get("confirmed_in_gap_ha", 0) > 0
        return "non-compliant", ("Forest in 2020 cleared after 2020; two independent sources record it"
                                 + (" (some pixels lie in small gaps inside a clearing both record)" if gap else ""))
    if facts["probable_ha"] > min_ha:
        return "probable deforestation", ("Forest in 2020 cleared after 2020 per TMF, or per Hansen or RADD "
                                          "radar backed by a second source (RADD, or Sentinel-2 land cover "
                                          "now showing farmland or bare ground)"
                                          + ("; or one source on the edge of a confirmed clearing"
                                             if facts.get("probable_next_to_confirmed_ha", 0) > 0 else "")
                                          + ("; or, after the latest yearly data, two near-real-time alert "
                                             "systems agree (RADD radar and an optical one: GLAD-L or DIST-ALERT)"
                                             if facts.get("probable_alerts_agree_ha", 0) > 0 else "")
                                          + "; needs evidence")
    if facts["review_ha"] > min_ha:
        why = []
        if facts.get("review_hansen_only_ha", 0) > 0:
            why.append("seen by Hansen only (TMF mostly sees canopy damage there)")
        if facts.get("review_radd_only_ha", 0) > 0:
            why.append("seen by RADD radar only")
        if facts.get("review_rubber_ha", 0) > 0:
            why.append("on land the rubber map calls rubber (possible replanting)")
        if facts.get("review_recent_alert_ha", 0) > 0:
            why.append("flagged by an optical near-real-time alert (GLAD-L or DIST-ALERT) after the latest "
                       "yearly data, on forest the yearly maps still show standing; the next yearly release "
                       "will confirm or clear it")
        return "needs review", ("Clearing after 2020 on 2020 forest " + " and ".join(why)
                                if why else "Clearing after 2020 on weak evidence; check imagery")
    damage = facts.get("hansen_only_damage_ha", 0)
    return "deforestation-free", ("No clearing after 2020 on land that was forest in 2020"
                                  + (f" (Hansen saw tree loss on {damage:g} ha, but TMF, RADD radar or "
                                     "Sentinel-2 land cover show the forest still there: damaged, not "
                                     "cleared)" if damage > 0 else ""))


C2_NO, C2_YES = "a. No Indication of deforestation", "b. Indication of deforestation"


def rat_aux_xml(hectares, entries=None):
    """GDAL raster attribute table for outcome.tif (or, with entries, another class map), saved
    next to it as <name>.tif.aux.xml: QGIS and ArcGIS then show each class's name, colour and
    hectares when you click a pixel. (GeoTIFF has no slot for an attribute table inside the
    file; GDAL keeps it in this sidecar.)"""
    from xml.sax.saxutils import escape
    fields = [("Value", 0, 5), ("Class", 2, 2), ("Red", 0, 6), ("Green", 0, 7), ("Blue", 0, 8),
              ("Alpha", 0, 9), ("Hectares", 1, 0), ("Deforestation", 2, 0)]
    lines = ['<PAMDataset>', '  <PAMRasterBand band="1">',
             '    <GDALRasterAttributeTable tableType="thematic">']
    for i, (name, ftype, usage) in enumerate(fields):
        lines.append(f'      <FieldDefn index="{i}"><Name>{name}</Name><Type>{ftype}</Type>'
                     f'<Usage>{usage}</Usage></FieldDefn>')
    for row, (code, label, (r, g, b, a), status) in enumerate(entries or legend_entries()):
        cells = [code, escape(label), r, g, b, a, f"{float(hectares[code]):.4f}", escape(status)]
        lines.append(f'      <Row index="{row}">' + "".join(f"<F>{c}</F>" for c in cells) + "</Row>")
    lines += ['    </GDALRasterAttributeTable>', '  </PAMRasterBand>', '</PAMDataset>', '']
    return "\n".join(lines)


def deforestation_table(areas_ha):
    """Rows: outcome group; columns: years 2021..2027 + total (hectares)."""
    import pandas as pd
    years = list(range(CUTOFF_YEAR + 1, LAST_CODABLE_YEAR + 1))
    rows = []
    for group, name in GROUP_NAMES.items():
        vals = [areas_ha[group + y - CUTOFF_YEAR] for y in years]
        rows.append({"outcome": name, **{str(y): round(v, 2) for y, v in zip(years, vals)},
                     "total": round(sum(vals), 2)})
    other = [{"outcome": BASE_LABELS[c][0], "total": round(areas_ha[c], 2)}
             for c in (NOT_FOREST, FOREST, FOREST_RUBBER, CLEARED_NON_FOREST, DEGRADED, WATER)]
    return pd.DataFrame(rows), pd.DataFrame(other)


def patch_labels(outcome, codes):
    """Label 4-connected patches separately for each outcome code (unique ids overall)."""
    labels = np.zeros(outcome.shape, np.int32)
    code_of = [0]
    next_id = 1
    structure = ndimage.generate_binary_structure(2, 1)
    for code in codes:
        lab, n = ndimage.label(outcome == code, structure=structure)
        if n:
            m = lab > 0
            labels[m] = lab[m] + (next_id - 1)
            code_of.extend([code] * n)
            next_id += n
    return labels, np.array(code_of, np.int32)


def patch_stats(labels, code_of, row_area, extras):
    """Per-patch pixel count and area, plus counts of pixels where each extra mask is set."""
    rr, cc = np.nonzero(labels)
    lab = labels[rr, cc]
    n = len(code_of)
    stats = {
        "pixels": np.bincount(lab, minlength=n),
        "area_ha": np.bincount(lab, weights=row_area[rr], minlength=n) / 1e4,
    }
    for name, mask in extras.items():
        if mask is not None:
            stats[name] = np.bincount(lab, weights=mask[rr, cc].astype(np.float64), minlength=n).astype(np.int64)
    return stats


def qml_for_outcome(path, entries=None):
    """QGIS style with labelled colours; QGIS loads it automatically when it sits next to the
    .tif with the same name. outcome.qml (no entries): one legend line per kind of result, with
    every year of a kind in the same colour (a 'discrete' colour ramp: each value takes the first
    item at or above it, so 21-27 all take the 'confirmed' item at 27). outcome_simple.qml (the
    compact entries): one palette entry per class."""
    from xml.sax.saxutils import quoteattr
    if entries is None:
        cmap, items = outcome_colormap(), []
        for code, (label, _) in sorted(BASE_LABELS.items()):
            items.append((code, label, cmap[code]))
        for group in (CONFIRMED, PROBABLE, REVIEW):
            last = group + LAST_CODABLE_YEAR - CUTOFF_YEAR
            items.append((last, f"Deforestation after 2020: {GROUP_NAMES[group]} (any year; the year is "
                                f"in the pixel value, e.g. {group + 3} = {CUTOFF_YEAR + 3})", cmap[last]))
        lines = [f'          <item value="{c}" color="#{r:02x}{g:02x}{b:02x}" alpha="{a}" label={quoteattr(l)}/>'
                 for c, l, (r, g, b, a) in items]
        top = items[-1][0]
        xml = ("<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>\n"
               '<qgis version="3.28" styleCategories="AllStyleCategories">\n  <pipe>\n'
               f'    <rasterrenderer type="singlebandpseudocolor" band="1" opacity="1" alphaBand="-1" '
               f'classificationMin="0" classificationMax="{top}" nodataColor="">\n'
               '      <rastershader>\n'
               f'        <colorrampshader colorRampType="DISCRETE" classificationMode="1" clip="0" '
               f'minimumValue="0" maximumValue="{top}" labelPrecision="0">\n'
               + "\n".join(lines) + "\n        </colorrampshader>\n      </rastershader>\n"
               "    </rasterrenderer>\n  </pipe>\n</qgis>\n")
        Path(path).write_text(xml, encoding="utf-8")
        return
    lines = []
    for code, label, (r, g, b, a), _ in (entries or legend_entries()):
        if code == NODATA:
            continue
        lines.append(f'        <paletteEntry value="{code}" color="#{r:02x}{g:02x}{b:02x}" '
                     f'alpha="{a}" label={quoteattr(label)}/>')
    entries = lines
    xml = ("<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>\n"
           '<qgis version="3.28" styleCategories="AllStyleCategories">\n  <pipe>\n'
           '    <rasterrenderer type="paletted" band="1" opacity="1" alphaBand="-1" nodataColor="">\n'
           '      <colorPalette>\n' + "\n".join(entries) + "\n      </colorPalette>\n"
           "    </rasterrenderer>\n  </pipe>\n</qgis>\n")
    Path(path).write_text(xml, encoding="utf-8")


def discover(data_dir):
    """Find input files by name. Returns {layer: [paths]} and {year: [paths]} for TMF annual.
    The rubber map's 10 m tiles are named only by their corner, so any GeoTIFF inside a folder
    whose name contains "rubber" also counts as a rubber tile (except the 1 km overview)."""
    found, annual = {}, {}
    root = Path(data_dir)
    for p in sorted(root.rglob("*")):
        if p.suffix.lower() not in (".tif", ".tiff") or not p.is_file():
            continue
        n = p.name.lower()
        m = ANNUAL_RE.search(n)
        if m:
            annual.setdefault(int(m.group(1)), []).append(p)
            continue
        for layer, test in DISCOVERY.items():
            if test(n):
                found.setdefault(layer, []).append(p)
                break
        else:
            folders = [part.lower() for part in p.relative_to(root).parts[:-1]]
            if any("rubber" in f for f in folders) and "1km" not in n:
                found.setdefault("rubber", []).append(p)
    return found, annual


def human(n_bytes):
    for unit in ("B", "KB", "MB", "GB"):
        if n_bytes < 1024 or unit == "GB":
            return f"{n_bytes:.1f} {unit}" if unit != "B" else f"{n_bytes} B"
        n_bytes /= 1024


# --------------------------------------------------------------------------------------
# Raster and vector I/O (rasterio / geopandas)
# --------------------------------------------------------------------------------------

def tile_names(bbox):
    """10-degree tiles covering a box, named the JRC way (N10_E90) and the Hansen way (10N_090E).
    Both datasets name a tile by its top-left corner."""
    xmin, ymin, xmax, ymax = bbox
    jrc, hansen = [], []
    for top in range(math.ceil(ymax / 10) * 10, math.ceil(ymin / 10) * 10 - 1, -10):
        for left in range(math.floor(xmin / 10) * 10, math.floor(xmax / 10) * 10 + 1, 10):
            if top - 10 >= ymax or left >= xmax:
                continue
            jrc.append(f"{'N' if top >= 0 else 'S'}{abs(top)}_{'E' if left >= 0 else 'W'}{abs(left)}")
            hansen.append(f"{abs(top):02d}{'N' if top >= 0 else 'S'}_{abs(left):03d}{'E' if left >= 0 else 'W'}")
    return jrc, hansen


def same_crs(a, b):
    if a is None or b is None:
        return False
    try:
        return a == b or (a.to_epsg() is not None and a.to_epsg() == b.to_epsg())
    except Exception:
        return a == b


def bboxes_overlap(a, b):
    """(xmin, ymin, xmax, ymax) boxes overlap with positive area."""
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def keep_overlapping(found, annual, bbox):
    """Drop input files that don't cover the box (e.g. a Hansen tile for another region).
    Returns the filtered dicts and a list of (layer, dropped file names)."""
    import rasterio
    from rasterio.warp import transform_bounds

    def covers(p):
        with rasterio.open(p) as src:
            b = tuple(src.bounds)
            if src.crs is not None and src.crs.to_epsg() not in (None, 4326):
                b = transform_bounds(src.crs, "EPSG:4326", *b)
        return bboxes_overlap(b, bbox)

    dropped = []
    out_found, out_annual = {}, {}
    for name, paths in found.items():
        keep = [p for p in paths if covers(p)]
        if len(keep) < len(paths):
            dropped.append((name, [p.name for p in paths if p not in keep]))
        if keep:
            out_found[name] = keep
    for year, paths in annual.items():
        keep = [p for p in paths if covers(p)]
        if len(keep) < len(paths):
            dropped.append((f"tmf_annual_{year}", [p.name for p in paths if p not in keep]))
        if keep:
            out_annual[year] = keep
    return out_found, out_annual, dropped


def grid_from_file(path, bbox):
    import rasterio
    with rasterio.open(path) as src:
        t = src.transform
        if t.b != 0 or t.d != 0:
            sys.exit(f"{path} is rotated; not supported")
        return snap_grid(t.c, t.f, t.a, t.e, bbox, src.crs)


def read_on_grid(paths, grid, fill=0, band=1, handles=None):
    """Read one layer (one or more tiles; band `band` of each) onto the grid by nearest
    neighbour. Returns (array, has_data). Same-CRS files use exact index arithmetic and read
    only the window they need; other CRSs go through a warped view of the source with a
    practically exact coordinate transform, so a small window and the whole box pick the same
    source pixels (see EXACT_WARP_TOLERANCE). handles: optional {path: open dataset} kept open
    between calls."""
    import rasterio
    from rasterio.vrt import WarpedVRT
    from rasterio.windows import Window
    from rasterio.enums import Resampling

    out, has = None, np.zeros((grid.height, grid.width), bool)
    xs, ys = grid.centres_x(), grid.centres_y()
    for p in paths:
        src = handles.get(p) if handles is not None else None
        if src is None:
            src = rasterio.open(p)
            if handles is not None:
                handles[p] = src
        try:
            if out is None:
                out = np.full((grid.height, grid.width), fill, np.dtype(src.dtypes[band - 1]))
            nodata = src.nodata
            t = src.transform
            if same_crs(src.crs, grid.crs) and t.b == 0 and t.d == 0:
                cols, cin = nearest_indices(xs, t.c, t.a, src.width)
                rows, rin = nearest_indices(ys, t.f, t.e, src.height)
                if not cin.any() or not rin.any():
                    continue
                c0, c1 = cols[cin].min(), cols[cin].max()
                r0, r1 = rows[rin].min(), rows[rin].max()
                data = src.read(band, window=Window(int(c0), int(r0), int(c1 - c0 + 1), int(r1 - r0 + 1)))
                sub = data[np.ix_(rows[rin] - r0, cols[cin] - c0)]
                region = np.ix_(np.flatnonzero(rin), np.flatnonzero(cin))
            else:
                opts = dict(crs=grid.crs, transform=grid.transform, width=grid.width, height=grid.height,
                            resampling=Resampling.nearest, src_nodata=nodata, nodata=nodata)
                try:
                    vrt = WarpedVRT(src, tolerance=EXACT_WARP_TOLERANCE, **opts)
                except Exception:                   # GDAL's default (approximate) transform
                    print(f"  Note: exact warp unavailable for {Path(p).name}; using GDAL's approximate one")
                    vrt = WarpedVRT(src, **opts)
                with vrt:
                    sub = vrt.read(band)
                region = (slice(None), slice(None))
            ok = np.ones(sub.shape, bool) if nodata is None else (sub != nodata)
            if np.issubdtype(sub.dtype, np.floating):
                ok &= ~np.isnan(sub)
            put = ok & ~has[region]
            o = out[region]
            o[put] = sub[put].astype(out.dtype, copy=False)
            out[region] = o
            hh = has[region]
            hh |= put
            has[region] = hh
        finally:
            if handles is None:
                src.close()
    return out, has


def landcover_plan(found, no_landcover=False):
    """Which Sentinel-2 land cover files to use: ("Dynamic World", {year: (paths, trees band,
    label band)}) from a Dynamic World export, else ("Impact Observatory", {year: paths}) from
    io_lulc_* files (get_landcover.py), else (None, {})."""
    if no_landcover:
        return None, {}
    if "dynamic_world" in found:
        paths = found["dynamic_world"]
        return "Dynamic World", {y: (paths, tb, lb) for y, (tb, lb) in dw_bands(paths[0]).items()}
    if "io_lulc" in found:
        by_year = {}
        for p in found["io_lulc"]:
            m = IO_YEAR_RE.search(p.name.lower())
            if m:
                by_year.setdefault(int(m.group(1)), []).append(p)
        if by_year:
            return "Impact Observatory", by_year
    return None, {}


def read_inputs(found, annual, grid, args, reader=None, decision_only=False, lc=None):
    """Read every input onto `grid` and turn the layers into the per-pixel facts (ready to pack).
    The build uses it for the whole box; --verify uses it for the window under one plot,
    straight from the original rasters, so both go through exactly the same steps.
    reader(name, paths, grid, fill, band) -> (array, has_data) (default: read_on_grid);
    decision_only skips layers the decision doesn't use (treecover2000, TMF transition map);
    lc: landcover_plan() result, to avoid looking it up again for every plot.
    Returns a dict: "fields" (the facts) plus the aligned layers that run() reports on."""
    if reader is None:
        reader = lambda name, paths, g, fill=0, band=1: read_on_grid(paths, g, fill, band)

    def get(name, fill=0):
        return reader(name, found[name], grid, fill)[0] if name in found else None

    gfc, has_gfc = reader("gfc2020", found["gfc2020"], grid, 0)
    if "lossyear" in found:
        lossyear, has_loss = reader("lossyear", found["lossyear"], grid, 0)
    else:                                    # TMF-only run: no Hansen loss recorded anywhere
        lossyear, has_loss = np.zeros(gfc.shape, np.uint8), has_gfc
    datamask = get("datamask")
    if datamask is None:                     # treat the box as land; TMF still marks water
        datamask = np.ones(gfc.shape, np.uint8)
    treecover = None if decision_only else get("treecover2000")
    gft, rubber = get("gft2020"), get("rubber")
    tmf_annual = {y: reader(f"tmf_annual_{y}", annual[y], grid, 0)[0] for y in sorted(annual)}
    defo, degr = get("tmf_deforestation_year"), get("tmf_degradation_year")
    transition = None if decision_only else get("tmf_transition")
    use_radd = "radd" in found and not args.no_radd
    radd = get("radd") if use_radd else None
    primary = get("primary_forest") if use_radd else None
    use_alerts = ("alerts" in found or "dist_alerts" in found) and not getattr(args, "no_alerts", False)
    alerts = get("alerts") if use_alerts else None
    dist_alerts = get("dist_alerts") if use_alerts else None
    if radd is None and alerts is not None and "radd" in found:
        radd = get("radd")                  # only to tell GLAD-L from RADD in the integrated alerts
        radd_for_glad_only = True
    else:
        radd_for_glad_only = False
    protected = rasterize_protected(args.protected, grid) if args.protected else None
    fields = build_fields(gfc, lossyear, gft, rubber, tmf_annual, defo, degr, protected,
                          tuple(args.gfc_forest_values), None if radd_for_glad_only else radd, primary,
                          alerts, dist_alerts)
    if radd_for_glad_only:
        fields["alert_year"], fields["alert_conf"] = optical_alerts(alerts, dist_alerts, radd)
    # Sentinel-2 land cover after each clearing: Dynamic World export, else Impact Observatory maps
    t_lc = time.perf_counter()
    kind, years = lc if lc is not None else landcover_plan(found, args.no_landcover)
    lc_name = None
    if years:
        if kind == "Dynamic World":
            def lc_year(y):
                paths, tb, lb = years[y]
                return (reader(None, paths, grid, DW_NO_DATA, tb)[0],
                        reader(None, paths, grid, DW_NO_DATA, lb)[0])
        else:
            def lc_year(y):
                return io_as_dw(reader(None, years[y], grid, 0)[0])
        fields["dw_after"] = dw_after_state(lc_year, sorted(years), first_event(fields))
        lc_name = f"{kind} {min(years)}-{max(years)}"
    # Hansen's datamask decides what is land. GFC2020 gaps count as non-forest, because some
    # JRC files mark non-forest as no-data.
    fields["land"] = land_field(has_loss & (datamask != 0), datamask)
    fields["context"] = clearing_context(fields, context_radius(grid))
    return {"fields": fields, "gfc": gfc, "has_gfc": has_gfc, "lossyear": lossyear,
            "has_loss": has_loss, "datamask": datamask, "treecover": treecover, "gft": gft,
            "rubber": rubber, "tmf_annual": tmf_annual, "defo": defo, "degr": degr,
            "transition": transition, "radd": radd, "primary": primary, "protected": protected,
            "use_radd": use_radd, "alerts": alerts, "dist_alerts": dist_alerts, "use_alerts": use_alerts,
            "lc_name": lc_name, "lc_seconds": time.perf_counter() - t_lc,
            "treecover_found": "treecover2000" in found, "transition_found": "tmf_transition" in found,
            "lossyear_found": "lossyear" in found, "datamask_found": "datamask" in found}


def native_window_bytes(paths, bbox):
    """Uncompressed bytes the bbox occupies in each input at its own resolution."""
    import rasterio
    total = 0
    for p in paths:
        with rasterio.open(p) as src:
            t = src.transform
            try:
                g = snap_grid(t.c, t.f, t.a, t.e, bbox)
            except ValueError:
                continue
            c0 = max(0, round((g.x0 - t.c) / t.a))
            r0 = max(0, round((g.y0 - t.f) / t.e))
            w = min(src.width, c0 + g.width) - c0
            h = min(src.height, r0 + g.height) - r0
            if w > 0 and h > 0:
                total += w * h * np.dtype(src.dtypes[0]).itemsize * src.count
    return total


def compressed_baseline_bytes(paths, bbox, tmpdir):
    """Size of the same bbox clipped from each input and saved the standard way
    (tiled GeoTIFF, DEFLATE with the horizontal predictor). A fair 'original layers' baseline."""
    import rasterio
    from rasterio.windows import Window
    total = 0
    for i, p in enumerate(paths):
        with rasterio.open(p) as src:
            t = src.transform
            try:
                g = snap_grid(t.c, t.f, t.a, t.e, bbox)
            except ValueError:
                continue
            c0 = max(0, round((g.x0 - t.c) / t.a))
            r0 = max(0, round((g.y0 - t.f) / t.e))
            w = min(src.width, c0 + g.width) - c0
            h = min(src.height, r0 + g.height) - r0
            if w <= 0 or h <= 0:
                continue
            win = Window(c0, r0, w, h)
            data = src.read(window=win)
            prof = src.profile.copy()
            prof.update(driver="GTiff", width=w, height=h, transform=src.window_transform(win),
                        tiled=True, blockxsize=512, blockysize=512, compress="DEFLATE", predictor=2)
            prof.pop("photometric", None)
            out = Path(tmpdir) / f"baseline_{i}.tif"
            with rasterio.open(out, "w", **prof) as dst:
                dst.write(data)
            total += out.stat().st_size
            out.unlink()
    return total


def write_cog(path, arr, grid, nodata=None, colormap=None, compress="ZSTD", predictor=None,
              level=None, overview_resampling="nearest", tags=None, band_tags=None,
              band_description=None):
    """Write a Cloud-Optimised GeoTIFF (512-px tiles, overviews). Falls back to DEFLATE
    if this GDAL build lacks the requested codec. Returns the codec actually used.
    tags / band_tags / band_description are stored inside the file (GDAL_METADATA tag),
    alongside the colour table, so the file explains itself in any GDAL-based tool."""
    import rasterio
    import rasterio.shutil
    from rasterio.io import MemoryFile

    profile = dict(driver="GTiff", width=grid.width, height=grid.height, count=1,
                   dtype=arr.dtype, crs=grid.crs, transform=grid.transform, nodata=nodata,
                   tiled=True, blockxsize=512, blockysize=512, compress="DEFLATE")
    opts = dict(driver="COG", compress=compress, blocksize=512,
                overview_resampling=overview_resampling)
    if predictor:
        opts["predictor"] = predictor
    if level:
        opts["level"] = level
    aux = Path(str(path) + ".aux.xml")          # stale sidecar from an earlier run would mix in
    if aux.exists():
        aux.unlink()
    with MemoryFile() as mem:
        with mem.open(**profile) as tmp:
            tmp.write(arr, 1)
            if colormap:
                tmp.write_colormap(1, colormap)
            if tags:
                tmp.update_tags(**{k: str(v) for k, v in tags.items()})
            if band_tags:
                tmp.update_tags(1, **{k: str(v) for k, v in band_tags.items()})
            if band_description:
                tmp.set_band_description(1, band_description)
        with mem.open() as tmp:
            try:
                rasterio.shutil.copy(tmp, path, **opts)
                return compress
            except Exception as exc:
                if compress == "DEFLATE":
                    raise
                print(f"  {compress} not available in this GDAL build ({exc}); using DEFLATE")
                opts["compress"] = "DEFLATE"
                opts.pop("level", None)
                rasterio.shutil.copy(tmp, path, **opts)
                return "DEFLATE"


def rasterio_open(path):
    import rasterio
    return rasterio.open(path)


def read_back_equal(path, arr):
    import rasterio
    with rasterio.open(path) as src:
        return bool(np.array_equal(src.read(1), arr))


def tags_inside(path, keys):
    """True if the file itself carries these dataset tags (checked before any sidecar exists)."""
    import rasterio
    with rasterio.open(path) as src:
        found = src.tags()
    return all(k in found for k in keys)


def compression_test(arr, grid, tmpdir, nodata=None):
    """Write the same layer with several lossless codecs; size, write time, full read and
    random 256-px window reads, and a lossless check."""
    import rasterio
    from rasterio.windows import Window
    variants = [("NONE", None), ("LZW", None), ("DEFLATE", None), ("DEFLATE", "STANDARD"),
                ("ZSTD", None), ("ZSTD", "STANDARD"), ("LERC_ZSTD", None)]
    rng = np.random.default_rng(0)
    wins = [(int(rng.integers(0, max(1, grid.width - 256))), int(rng.integers(0, max(1, grid.height - 256))))
            for _ in range(50)]
    rows = []
    for codec, pred in variants:
        path = Path(tmpdir) / f"test_{codec}_{pred}.tif"
        t0 = time.perf_counter()
        used = write_cog(path, arr, grid, nodata=nodata, compress=codec, predictor=pred)
        t1 = time.perf_counter()
        if used != codec:
            path.unlink(missing_ok=True)
            continue
        with rasterio.open(path) as src:
            t2 = time.perf_counter()
            back = src.read(1)
            t3 = time.perf_counter()
            for c, r in wins:
                src.read(1, window=Window(c, r, min(256, grid.width), min(256, grid.height)))
            t4 = time.perf_counter()
        rows.append({"codec": codec, "predictor": pred or "none", "bytes": path.stat().st_size,
                     "write_s": round(t1 - t0, 2), "full_read_s": round(t3 - t2, 3),
                     "window_read_ms": round((t4 - t3) / len(wins) * 1000, 2),
                     "lossless": bool(np.array_equal(back, arr))})
        path.unlink()
    return rows


def rasterize_protected(vector_path, grid):
    import geopandas as gpd
    from rasterio.features import rasterize
    xmin, ymax = grid.x0, grid.y0
    xmax, ymin = grid.x0 + grid.width * grid.dx, grid.y0 + grid.height * grid.dy
    gdf = gpd.read_file(vector_path, bbox=(xmin, ymin, xmax, ymax))
    if gdf.empty:
        return np.zeros((grid.height, grid.width), np.uint8)
    gdf = gdf.to_crs(grid.crs)
    return rasterize(((g, 1) for g in gdf.geometry if g is not None and not g.is_empty),
                     out_shape=(grid.height, grid.width), transform=grid.transform,
                     fill=0, dtype="uint8")


def polygons(values, mask, grid):
    """(value, geometry) for each 4-connected group of equal values inside mask."""
    from rasterio.features import shapes
    from shapely.geometry import shape
    return [(int(v), shape(g)) for g, v in shapes(values, mask=mask, transform=grid.transform,
                                                   connectivity=4)]


def read_plots(path, id_field="ProductionPlace"):
    """Plots from GeoJSON in the EU Information System format (WGS84; ProducerName,
    ProducerCountry, ProductionPlace, Area). Points become circles of their Area (default 4 ha,
    as the EU system assumes), so every plot is judged on an area."""
    import geopandas as gpd
    from shapely import affinity
    from shapely.geometry import Point
    gdf = gpd.read_file(path)
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    elif gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs("EPSG:4326")
    ids = gdf[id_field].astype(str) if id_field in gdf.columns else \
        [f"plot_{i + 1}" for i in range(len(gdf))]
    geoms = []
    for g, row in zip(gdf.geometry, gdf.itertuples()):
        if g is not None and g.geom_type in ("Point", "MultiPoint"):
            area = getattr(row, "Area", None)
            area = float(area) if area not in (None, "") and not (isinstance(area, float) and math.isnan(area)) else 4.0
            r = math.sqrt(area * 1e4 / math.pi)
            c = g.centroid
            unit = Point(0, 0).buffer(1, 64)
            g = affinity.translate(affinity.scale(unit, r / (111320 * math.cos(math.radians(c.y))), r / 110574,
                                                  origin=(0, 0)), c.x, c.y)
        geoms.append(g)
    out = gdf.drop(columns="geometry").copy()
    out.insert(0, "plot_id", list(ids))
    return gpd.GeoDataFrame(out, geometry=geoms, crs="EPSG:4326")


def plot_window(geom, grid):
    """Rows r0:r1 and columns c0:c1 of the grid under a plot (its bounds plus one pixel)."""
    minx, miny, maxx, maxy = geom.bounds
    c0 = max(0, math.floor((minx - grid.x0) / grid.dx) - 1)
    c1 = min(grid.width, math.ceil((maxx - grid.x0) / grid.dx) + 1)
    r0 = max(0, math.floor((maxy - grid.y0) / grid.dy) - 1)
    r1 = min(grid.height, math.ceil((miny - grid.y0) / grid.dy) + 1)
    return r0, r1, c0, c1


def window_grid(grid, r0, r1, c0, c1):
    """The part of the grid in rows r0:r1, columns c0:c1, as a grid of its own."""
    return Grid(grid.x0 + c0 * grid.dx, grid.y0 + r0 * grid.dy, grid.dx, grid.dy, c1 - c0, r1 - r0, grid.crs)


def read_window_fields(found, annual, grid, r0, r1, c0, c1, args, reader=None, lc=None):
    """The facts for rows r0:r1, columns c0:c1 of the grid straight from the original rasters,
    read with a margin so the 'context' fact (which looks at neighbours up to 2 x radius away)
    comes out exactly as in the whole-box build; the margin is cut off again."""
    m = 2 * context_radius(grid) + 1
    R0, R1, C0, C1 = max(0, r0 - m), min(grid.height, r1 + m), max(0, c0 - m), min(grid.width, c1 + m)
    big = read_inputs(found, annual, window_grid(grid, R0, R1, C0, C1), args, reader,
                      decision_only=True, lc=lc)["fields"]
    return {k: v[r0 - R0:r1 - R0, c0 - C0:c1 - C0] for k, v in big.items()}


def plot_mask(geom, wgrid):
    """Pixels of the window whose centre is inside the plot, or, for a plot smaller than a
    pixel, every pixel it touches. Returns (mask, note)."""
    from rasterio.features import geometry_mask
    shape = (wgrid.height, wgrid.width)
    mask = geometry_mask([geom], out_shape=shape, transform=wgrid.transform, invert=True)
    if mask.any():
        return mask, ""
    return (geometry_mask([geom], out_shape=shape, transform=wgrid.transform, invert=True, all_touched=True),
            "tiny plot: pixels touching it used")


def judge(fields, mask, row_area_w, rule, min_ha=0.0, outcome=None):
    """Facts, verdict and reason for one plot from the facts under it. The rule (hansen_only
    mode, settled_until) is applied to the facts unless a stored outcome window is given."""
    oc = decide(fields, *rule) if outcome is None else outcome
    facts = plot_facts(fields, oc, mask, row_area_w, rule[2] if len(rule) > 2 else None)
    verdict, reason = plot_verdict(facts, min_ha)
    return facts, verdict, reason


def query_plots(plots, ev_path, grid, row_area, rule, min_ha=0.0):
    """The plot-level query against the compact layer on disk: for each plot read only the
    window under it from evidence.tif, decode the facts, apply the rule, keep pixels whose
    centre is inside the polygon and add them up into facts and a verdict. evidence.tif is the
    only file read. Returns (rows, seconds)."""
    import rasterio
    from rasterio.windows import Window
    rows = []
    t0 = time.perf_counter()
    with rasterio.open(ev_path) as ev:
        for pid, geom in zip(plots["plot_id"], plots.geometry):
            r0, r1, c0, c1 = plot_window(geom, grid)
            note = ""
            if c1 <= c0 or r1 <= r0:
                facts, verdict, reason = judge(unpack(np.zeros((1, 1), PACKED_DTYPE)), np.zeros((1, 1), bool),
                                               row_area[:1], rule, min_ha)
            else:
                mask, note = plot_mask(geom, window_grid(grid, r0, r1, c0, c1))
                evw = ev.read(1, window=Window(c0, r0, c1 - c0, r1 - r0))
                facts, verdict, reason = judge(unpack(evw), mask, row_area[r0:r1], rule, min_ha)
            rows.append({"plot_id": pid, **facts, "verdict": verdict, "reason": reason, "note": note})
    return rows, time.perf_counter() - t0


def verify_plots(plots, ev_path, oc_path, grid, row_area, found, annual, args, rule, min_ha=0.0,
                 plot_set="given"):
    """Decision and pixel fidelity against the original layers. Each plot is judged three ways
    and every output (verdict, reason, every hectare figure, the years) compared:
      1. from evidence.tif, exactly as the plot query does;
      2. straight from the original input rasters: the window under the plot is read from every
         input file the decision uses and turned into facts and a verdict, without touching
         anything this script wrote;
      3. from outcome.tif (the stored per-pixel outcome) with evidence.tif's facts.
    Also counts, field by field, pixels where the decoded evidence differs from the facts rebuilt
    from the original rasters, and times ways 1 and 2 (input files stay open between plots, so
    the time is reading and computing, not opening files). Returns (rows, pixel counts)."""
    import rasterio
    from rasterio.windows import Window
    handles = {}
    reader = lambda name, paths, g, fill=0, band=1: read_on_grid(paths, g, fill, band, handles)
    lc = landcover_plan(found, args.no_landcover)
    names = [n for n, _ in FIELDS]
    px = {"pixels_compared": 0, "field_mismatches": dict.fromkeys(names, 0), "outcome_mismatches": 0}
    strata = list(plots["test_stratum"]) if "test_stratum" in plots.columns else [""] * len(plots)
    rows = []
    try:
        with rasterio.open(ev_path) as ev, rasterio.open(oc_path) as oc:
            for pid, geom, stratum in zip(plots["plot_id"], plots.geometry, strata):
                r0, r1, c0, c1 = plot_window(geom, grid)
                if c1 <= c0 or r1 <= r0:
                    continue                    # outside the box: nothing to compare
                win, wg, ra = Window(c0, r0, c1 - c0, r1 - r0), window_grid(grid, r0, r1, c0, c1), row_area[r0:r1]
                t0 = time.perf_counter()        # 1. the compact layer
                mask, _ = plot_mask(geom, wg)
                fe = unpack(ev.read(1, window=win))
                facts_e, verdict_e, reason_e = judge(fe, mask, ra, rule, min_ha)
                t_e = time.perf_counter() - t0
                t0 = time.perf_counter()        # 2. the original rasters
                mask_o, _ = plot_mask(geom, wg)
                fo = read_window_fields(found, annual, grid, r0, r1, c0, c1, args, reader, lc)
                facts_o, verdict_o, reason_o = judge(fo, mask_o, ra, rule, min_ha)
                t_o = time.perf_counter() - t0
                facts_m, verdict_m, reason_m = judge(fe, mask, ra, rule, min_ha,   # 3. outcome.tif
                                                     outcome=oc.read(1, window=win))
                a = {**facts_e, "verdict": verdict_e, "reason": reason_e}
                diff_o = compare_outputs(a, {**facts_o, "verdict": verdict_o, "reason": reason_o})
                diff_m = compare_outputs(a, {**facts_m, "verdict": verdict_m, "reason": reason_m})
                px["outputs_per_plot"] = len(a)
                px["pixels_compared"] += fe["land"].size
                for k in names:
                    px["field_mismatches"][k] += int(np.count_nonzero(fe[k] != fo[k]))
                px["outcome_mismatches"] += int(np.count_nonzero(decide(fe, *rule) != decide(fo, *rule)))
                rows.append({"plot_id": pid, "set": plot_set, "test_stratum": stratum,
                             "verdict_from_evidence": verdict_e, "verdict_from_original_layers": verdict_o,
                             "verdict_from_outcome_tif": verdict_m,
                             "all_outputs_match": not diff_o, "outcome_map_matches": not diff_m,
                             "outputs_that_differ": " ".join(diff_o),
                             "outcome_tif_outputs_that_differ": " ".join(diff_m),
                             "pixels": facts_e["pixels"], "area_ha": facts_e["area_ha"],
                             "forest_2020_ha": facts_e["forest_2020_ha"],
                             "evidence_ms": round(1000 * t_e, 3), "original_layers_ms": round(1000 * t_o, 3)})
    finally:
        px["original_files"] = len(handles)
        for src in handles.values():
            src.close()
    return rows, px


def query_only(args):
    """--query-only: judge --plots against an existing results folder, reading only its
    evidence.tif (no input rasters needed). The rule comes from the file (RULE_PARAMS);
    --hansen-only re-judges under another rule variant without rebuilding anything."""
    import rasterio
    import pandas as pd
    if not args.plots:
        sys.exit("--query-only needs --plots.")
    out_dir = Path(args.out_dir)
    ev_path = out_dir / "evidence.tif"
    if not ev_path.exists():
        sys.exit(f"--query-only needs evidence.tif in {out_dir}; run the build there first.")
    with rasterio.open(ev_path) as src:
        tags, t = src.tags(), src.transform
        grid = Grid(t.c, t.f, t.a, t.e, src.width, src.height, src.crs)
    layout = [r["field"] for r in json.loads(tags.get("BIT_LAYOUT", "[]"))]
    if layout != [n for n, _ in FIELDS]:
        sys.exit(f"{ev_path} was written by another version of this script (bit layout {layout}); "
                 "rebuild it with this version.")
    params = json.loads(tags.get("RULE_PARAMS", "{}"))
    mode = args.hansen_only or params.get("hansen_only", "settle")
    rule = (mode, settled_year(mode, params.get("tmf_last_year")),
            None if args.no_alerts else params.get("alerts_after_year"))
    plots = read_plots(args.plots, args.plot_id_field)
    rows, t_q = query_plots(plots, ev_path, grid, row_areas_m2(grid), rule, args.min_plot_ha)
    df = pd.DataFrame(rows)
    first = ["plot_id", "verdict", "reason", "area_ha", "forest_2020_ha", "confirmed_ha", "probable_ha",
             "review_ha", "first_deforestation_year"]
    df = df[first + [c for c in df.columns if c not in first]]
    out = out_dir / f"{Path(args.plots).stem}_verdicts{'_' + args.hansen_only if args.hansen_only else ''}.csv"
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"Judged {len(df)} plots from {ev_path.name} alone in {1000 * t_q:.1f} ms "
          f"({1000 * t_q / max(1, len(df)):.2f} ms per plot); rule: hansen-only {mode}"
          f"{', settled until ' + str(rule[1]) if rule[1] else ''}.")
    print(df["verdict"].value_counts().to_string())
    print(f"Saved {out}")


def geodesic_area_m2(geoms):
    import shapely
    from pyproj import Geod
    geod = Geod(ellps="WGS84")
    out = []
    for g in geoms:
        g = shapely.segmentize(g, 0.0005)   # follow parallels closely (~55 m segments)
        out.append(abs(geod.geometry_area_perimeter(g)[0]))
    return np.array(out)


# --------------------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------------------

def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Build EUDR deforestation layers for one area.")
    p.add_argument("--data-dir", help="folder holding the input GeoTIFFs (searched recursively)")
    p.add_argument("--layer", action="append", default=[], metavar="NAME=PATH",
                   help="set or override one input, e.g. gfc2020=C:\\data\\gfc.tif or "
                        "tmf_annual_2021=C:\\data\\x.tif (repeatable)")
    p.add_argument("--bbox", nargs=4, type=float,
                   metavar=("XMIN", "YMIN", "XMAX", "YMAX"),
                   help="area in longitude/latitude (optional with --plots: their extent + ~1 km)")
    p.add_argument("--plots", help="plot polygons to judge one by one (GeoJSON, EU Information "
                                   "System format: ProducerName, ProducerCountry, ProductionPlace, Area)")
    p.add_argument("--plots-csv", help="due diligence CSV for the same plots (a3_plot_id, c2_..., ...) "
                                       "to compare against")
    p.add_argument("--plot-id-field", default="ProductionPlace", help="plot id property in the GeoJSON")
    p.add_argument("--min-plot-ha", type=float, default=0.0,
                   help="ignore deforestation of at most this many ha per plot (EUDR has no "
                        "tolerance; default 0)")
    p.add_argument("--out-dir", required=True, help="results folder (created if needed)")
    p.add_argument("--grid", choices=("10m", "30m"), default="10m",
                   help="10m = JRC GFC2020 grid (default), 30m = Hansen grid")
    p.add_argument("--protected", help="protected-area polygons (e.g. WDPA .gpkg/.shp)")
    p.add_argument("--gfc-forest-values", type=int, nargs="+", default=[1],
                   help="GFC2020 pixel value(s) meaning forest (check the .qml legend)")
    p.add_argument("--treecover-threshold", type=int, default=10,
                   help="canopy %% for the Hansen-only comparison rule (EUDR: 10)")
    p.add_argument("--compress", default="ZSTD", help="COG codec for outputs (ZSTD or DEFLATE)")
    p.add_argument("--compress-test", action="store_true",
                   help="also compare lossless codecs on the packed layer")
    p.add_argument("--no-forest-polygons", action="store_true")
    p.add_argument("--no-rubber-polygons", action="store_true")
    p.add_argument("--no-baseline", action="store_true",
                   help="skip writing clipped, compressed inputs for the size baseline")
    p.add_argument("--max-pixels", type=int, default=60_000_000)
    p.add_argument("--hansen-only", choices=("settle", "review", "probable"), default=None,
                   help="where only Hansen records the clearing: 'settle' (default) = needs review, "
                        "except clearing 3+ years before TMF's latest year that TMF still sees as "
                        "forest, which counts as damaged forest; 'review' = always needs review; "
                        "'probable' = the rule before 14 Oct 2026. With --query-only: re-judge under "
                        "this variant (default: the rule stored in evidence.tif)")
    p.add_argument("--test-plots", type=int, default=0, metavar="N",
                   help="also generate N test plots in the box (half at random, half on each kind of "
                        "outcome) and judge them: test_plots.geojson, test_plots_result.csv")
    p.add_argument("--seed", type=int, default=0, help="random seed for --test-plots")
    p.add_argument("--verify", action="store_true",
                   help="decision and pixel fidelity: judge every plot (--plots and --test-plots) "
                        "straight from the original rasters too, compare every output and time "
                        "both ways: fidelity_plots.csv, 'fidelity' in summary.json")
    p.add_argument("--query-only", action="store_true",
                   help="judge --plots against evidence.tif in an existing --out-dir, without the "
                        "input rasters (writes <plots>_verdicts.csv there)")
    p.add_argument("--no-landcover", action="store_true",
                   help="ignore Sentinel-2 land cover (Dynamic World / Impact Observatory) even if "
                        "its files are in --data-dir")
    p.add_argument("--no-radd", action="store_true",
                   help="ignore RADD radar alerts even if a RADD file is in --data-dir")
    p.add_argument("--no-alerts", action="store_true",
                   help="ignore the near-real-time optical alerts (GFW integrated alerts, DIST-ALERT) even if "
                        "their files are in --data-dir")
    p.add_argument("--simple-only", action="store_true",
                   help="only rebuild outcome_simple.tif in --out-dir from its outcome.tif and "
                        "evidence.tif (fast; e.g. after changing the compact map's colours)")
    return p.parse_args(argv)


def collect_inputs(args):
    found, annual = discover(args.data_dir) if args.data_dir else ({}, {})
    for item in args.layer:
        if "=" not in item:
            sys.exit(f"--layer needs NAME=PATH, got {item}")
        name, path = item.split("=", 1)
        m = re.fullmatch(r"tmf_annual_(\d{4})", name)
        if m:
            annual[int(m.group(1))] = [Path(path)]
        else:
            found[name] = [Path(path)]
    missing = [r for r in REQUIRED if r not in found]
    if missing:
        sys.exit(f"Missing required input(s): {', '.join(missing)}. "
                 "Check file names or pass them with --layer NAME=PATH.")
    if "lossyear" not in found and not annual and "tmf_deforestation_year" not in found:
        sys.exit("No clearing data found: add Hansen lossyear or the JRC TMF files.")
    return found, annual


class _Tee:
    """Write to the console and the run log at the same time."""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, text):
        for s in self.streams:
            try:
                s.write(text)
                s.flush()
            except Exception:
                pass
        return len(text)

    def flush(self):
        for s in self.streams:
            try:
                s.flush()
            except Exception:
                pass


def main(argv=None):
    """Run the build, saving everything printed to <out-dir>/run_log.txt as well."""
    import traceback
    from datetime import datetime
    args = parse_args(argv)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    log_name = ("run_log_simple.txt" if args.simple_only else
                "run_log_query.txt" if args.query_only else "run_log.txt")
    log = open(out_dir / log_name, "w", encoding="utf-8")
    saved = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = _Tee(saved[0], log), _Tee(saved[1], log)
    try:
        print(f"eudr_layers.py started {datetime.now():%Y-%m-%d %H:%M:%S}  "
              f"args: {' '.join(sys.argv[1:] if argv is None else argv)}")
        if args.simple_only:
            rebuild_simple(args)
        elif args.query_only:
            query_only(args)
        else:
            run(args)
        print("FINISHED OK")
    except SystemExit as exc:
        if isinstance(exc.code, str):
            log.write(f"STOPPED: {exc.code}\n")
        raise
    except BaseException:
        log.write("FAILED:\n" + traceback.format_exc())
        raise
    finally:
        sys.stdout, sys.stderr = saved
        log.close()


def write_simple_outputs(out_dir, outcome, fields, transition, grid, row_area, common, has_rubber,
                         compress="ZSTD"):
    """Write outcome_simple.tif (COG with colours, LEGEND and hectares inside), its QGIS style,
    attribute table and a PNG quick look. Returns (path, class grid, legend, hectares, read-back ok)."""
    simple = simple_outcome(outcome, fields, transition)
    legend, cmap = simple_legend(has_rubber), simple_colormap()
    entries = legend_entries(legend, cmap)
    areas = class_areas_ha(simple, row_area)
    path = Path(out_dir) / "outcome_simple.tif"
    write_cog(
        path, simple, grid, nodata=S_NODATA, colormap=cmap, compress=compress, overview_resampling="mode",
        tags={**common, "CONTENT": "Compact EUDR outcome per pixel for viewing: one colour per kind of "
                                   "result, 'not forest' split by land type (see LEGEND). Years and "
                                   "the full detail are in outcome.tif.",
              "LEGEND": json.dumps(legend),
              "HECTARES": json.dumps({c: round(float(areas[c]), 2) for c in legend if areas[c] > 0})},
        band_tags={f"CLASS_{c}": v["label"] for c, v in legend.items()},
        band_description="Compact EUDR outcome (see LEGEND)")
    qml_for_outcome(Path(out_dir) / "outcome_simple.qml", entries)
    step = max(1, math.ceil(max(simple.shape) / 3000))
    write_png(Path(out_dir) / "outcome_simple_preview.png", mode_downsample(simple, step), cmap)
    ok = read_back_equal(path, simple)
    Path(str(path) + ".aux.xml").write_text(rat_aux_xml(areas, entries), encoding="utf-8")
    return path, simple, legend, areas, ok


def evidence_path(out_dir):
    """The packed evidence layer in a results folder (evidence_16bit.tif in results made before
    the RADD fields were added)."""
    new = Path(out_dir) / "evidence.tif"
    old = Path(out_dir) / "evidence_16bit.tif"
    return new if new.exists() or not old.exists() else old


def rebuild_simple(args):
    """--simple-only: rebuild outcome_simple.* in an existing results folder from its outcome.tif
    and evidence.tif (plus TMF's transition map from --data-dir, if found), without
    recomputing anything else. Useful after changing the compact map's classes or colours."""
    import rasterio
    out_dir = Path(args.out_dir)
    oc_path, ev_path = out_dir / "outcome.tif", evidence_path(out_dir)
    for p in (oc_path, ev_path):
        if not p.exists():
            sys.exit(f"--simple-only needs {p.name} in {out_dir}; run the full build there first.")
    t0 = time.perf_counter()
    with rasterio.open(oc_path) as src:
        outcome, tags, t = src.read(1), src.tags(), src.transform
        grid = Grid(t.c, t.f, t.a, t.e, src.width, src.height, src.crs)
    with rasterio.open(ev_path) as src:
        fields = unpack(src.read(1))
    bbox = json.loads(tags["BBOX_LONLAT"]) if "BBOX_LONLAT" in tags else \
        [grid.x0, grid.y0 + grid.height * grid.dy, grid.x0 + grid.width * grid.dx, grid.y0]
    transition, used = None, []
    if args.data_dir:
        found, annual = discover(args.data_dir)
        found, annual, _ = keep_overlapping(found, annual, bbox)
        if "tmf_transition" in found:
            transition = read_on_grid(found["tmf_transition"], grid)[0]
            used = [p.name for p in found["tmf_transition"]]
    if "INPUT_LAYERS" in tags:
        has_rubber = "rubber" in json.loads(tags["INPUT_LAYERS"])
    else:                                   # results from before the tag existed
        has_rubber = bool((fields["rubber"] == 1).any())
    common = {k: tags[k] for k in ("REGULATION", "BBOX_LONLAT", "GRID", "SOURCE_FILES", "INPUT_LAYERS", "RULE")
              if k in tags}
    common.update({"PRODUCER": f"eudr_layers.py ({SCRIPT_VERSION}), --simple-only",
                   "CREATED_UTC": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "DERIVED_FROM": json.dumps(["outcome.tif", ev_path.name] + used)})
    path, _, legend, areas, ok = write_simple_outputs(
        out_dir, outcome, fields, transition, grid, row_areas_m2(grid), common, has_rubber, args.compress)
    print(f"Rebuilt {path} in {time.perf_counter() - t0:.1f}s (read back identical: {ok}; "
          f"TMF transition map: {', '.join(used) or 'not found'}; rubber map in the run: {has_rubber})")
    for c in legend:
        if c != S_NODATA:
            print(f"  {legend[c]['label']:55s} {areas[c]:12,.2f} ha")


def read_due_diligence_csv(path, id_col="a3_plot_id"):
    import csv
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return {r[id_col]: r for r in csv.DictReader(fh) if r.get(id_col)}


def run(args):
    t_start = time.perf_counter()
    args.hansen_only = args.hansen_only or "settle"
    out_dir = Path(args.out_dir)
    plots = None
    if args.plots:
        plots = read_plots(args.plots, args.plot_id_field)
        print(f"Plots: {len(plots)} from {Path(args.plots).name}")
        if not args.plots_csv:                      # the supplier's record saved next to the GeoJSON
            pp = Path(args.plots)
            match = sorted(pp.parent.glob(pp.stem + "*.csv"))
            if match:
                args.plots_csv = str(match[0])
                print(f"Comparing with the due diligence record {match[0].name}")
        if args.bbox is None:
            b = plots.total_bounds
            args.bbox = [math.floor((b[0] - 0.01) * 1e4) / 1e4, math.floor((b[1] - 0.01) * 1e4) / 1e4,
                         math.ceil((b[2] + 0.01) * 1e4) / 1e4, math.ceil((b[3] + 0.01) * 1e4) / 1e4]
            print(f"Box from the plots' extent plus ~1 km: {args.bbox}")
    if args.bbox is None:
        sys.exit("Give --bbox, or --plots to use the plots' extent.")
    found, annual = collect_inputs(args)
    found, annual, dropped = keep_overlapping(found, annual, args.bbox)
    for name, names in dropped:
        print(f"  Skipping {name} file(s) that don't cover the box: {', '.join(names)}")
    jrc_tiles, hansen_tiles = tile_names(args.bbox)
    if "gfc2020" not in found:
        sys.exit(f"No JRC GFC2020 file covers the box {args.bbox}. Download JRC tile(s) "
                 f"{', '.join(jrc_tiles)} (GFC2020, plus GFT2020 and the TMF files for the same tile).")
    has_tmf = bool(annual) or "tmf_deforestation_year" in found
    if "lossyear" not in found and not has_tmf:
        sys.exit(f"No clearing data covers the box: add Hansen lossyear (tile {', '.join(hansen_tiles)}) "
                 f"or the JRC TMF files (tile {', '.join(jrc_tiles)}).")
    if "lossyear" not in found:
        print("  Hansen lossyear not found for this box: running with TMF only. Nothing can be "
              "'confirmed' (that needs two sources), so TMF clearing on 2020 forest shows as 'probable'. "
              f"Add Hansen {', '.join(hansen_tiles)} and run again for the full result.")
    elif not annual and "tmf_deforestation_year" not in found:
        print(f"  No JRC TMF files for this box: running with Hansen only, so nothing can be "
              f"'confirmed'. Add the TMF files for tile {', '.join(jrc_tiles)} for the full result.")

    print("Inputs found:")
    for name, paths in found.items():
        print(f"  {name:24s} {', '.join(p.name for p in paths)}")
    for y in sorted(annual):
        print(f"  tmf_annual_{y:<13d} {', '.join(p.name for p in annual[y])}")
    if CUTOFF_YEAR not in annual and annual:
        print("  Warning: no TMF AnnualChange_2020; TMF clearing uses DeforestationYear only.")
    for opt in ("gft2020", "rubber", "tmf_deforestation_year", "treecover2000"):
        if opt not in found:
            print(f"  Note: no {opt} found; the related fields will be empty.")

    # 1. Grid
    if args.grid == "30m" and "lossyear" not in found:
        sys.exit("--grid 30m uses the Hansen grid, but no Hansen lossyear file covers the box.")
    ref = found["gfc2020"][0] if args.grid == "10m" else found["lossyear"][0]
    grid = grid_from_file(ref, args.bbox)
    if not grid.crs or not grid.crs.is_geographic:
        sys.exit("The reference layer must be in longitude/latitude (EPSG:4326).")
    if grid.n_pixels > args.max_pixels:
        sys.exit(f"Grid is {grid.width} x {grid.height} = {grid.n_pixels:,} pixels "
                 f"(limit {args.max_pixels:,}). Use a smaller bbox or --max-pixels.")
    row_area = row_areas_m2(grid)
    print(f"\nGrid: {args.grid}, {grid.width} x {grid.height} pixels, "
          f"pixel {abs(grid.dx):.8f} deg, ~{row_area.mean():.0f} m2 per pixel")

    # 2. Read every layer onto the grid and turn them into per-pixel facts
    print("Reading layers onto the grid ...")
    t0 = time.perf_counter()
    profile = {}

    def load(name, paths, g, fill=0, band=1):
        arr, has = read_on_grid(paths, g, fill, band)
        if name is None:                     # land cover years: not profiled one by one
            return arr, has
        if arr.dtype == np.uint8:
            counts = np.bincount(arr.ravel(), minlength=256)
            vals = {int(v): int(c) for v, c in enumerate(counts) if c}
        else:
            u, c = np.unique(arr, return_counts=True)
            vals = {int(v): int(n) for v, n in zip(u, c)}
        top = dict(sorted(vals.items(), key=lambda kv: -kv[1])[:12])
        profile[name] = {"files": [p.name for p in paths], "dtype": str(arr.dtype),
                         "coverage": round(float(has.mean()), 4), "top_values": top}
        return arr, has

    L = read_inputs(found, annual, grid, args, load)
    fields = L["fields"]
    gft, rubber, defo, transition, protected = L["gft"], L["rubber"], L["defo"], L["transition"], L["protected"]
    treecover, lossyear, datamask = L["treecover"], L["lossyear"], L["datamask"]
    use_radd, lc_name, use_alerts = L["use_radd"], L["lc_name"], L["use_alerts"]
    use_dw = lc_name is not None
    t_read = time.perf_counter() - t0
    print(f"Read and aligned {len(profile)} layers and built the facts in {t_read:.1f}s"
          + (f" (land cover after each clearing: {lc_name}, {L['lc_seconds']:.1f}s)" if use_dw else ""))
    for name, info in profile.items():
        print(f"  {name:24s} coverage {info['coverage']:.0%}  values {list(info['top_values'])[:8]}")
    if use_radd and L["primary"] is None:
        print("  Note: RADD found but no primary forest map; RADD alerts count, but RADD's silence "
              "can't be used (where it watches is unknown).")
    if L["has_gfc"].mean() < 0.5:
        print(f"  Note: GFC2020 has data for only {L['has_gfc'].mean():.0%} of the box; "
              "check the tile and its legend values.")

    # 3. Pack the facts and apply the rule. The rule reads only the packed facts, so the same
    #    outcome can be recomputed later from evidence.tif alone (plot queries do exactly that).
    t0 = time.perf_counter()
    packed = pack(fields)
    roundtrip_ok = all(np.array_equal(v, fields[k]) for k, v in unpack(packed).items())
    tmf_last = max(annual) if annual and CUTOFF_YEAR in annual else None
    hansen_last = None
    if "lossyear" in found and lossyear.size:
        mx = int(lossyear.max())
        hansen_last = CUTOFF_YEAR - 20 + mx if 1 <= mx <= 50 else None
    yearly_last = max([y for y in (tmf_last, hansen_last) if y], default=None)
    alerts_after = yearly_last if use_alerts else None   # alerts count only after the yearly data
    rule = (args.hansen_only, settled_year(args.hansen_only, tmf_last), alerts_after)
    settled_until = rule[1]
    outcome = decide(fields, *rule)
    areas = class_areas_ha(outcome, row_area)
    t_logic = time.perf_counter() - t0

    # 4. Write rasters, each carrying its own legend or bit layout and provenance inside
    how = {"probable": "probable", "review": "needs review",
           "settle": "needs review" + (f", except {CUTOFF_YEAR + 1}-{settled_until} where TMF still sees "
                                       "forest (damaged forest)" if settled_until else "")}[args.hansen_only]
    print(f"Rules applied in {t_logic:.1f}s (clearing seen by Hansen only -> {how}"
          f"{'; RADD radar used as a third source' if use_radd else ''}"
          f"{'; ' + lc_name + ' land cover checks what is left' if use_dw else ''}"
          f"{f'; near-real-time optical alerts (GLAD-L, DIST-ALERT) with RADD used after {alerts_after}' if alerts_after else ''}).")
    f20 = fields["gfc_forest"] == 1
    cell = lambda m: round(float(class_areas_ha(m.astype(np.uint8), row_area)[1]), 2)
    dw_summary = None
    if use_dw:
        dw_summary = {"2020_forest_cleared_then_trees_every_year_ha": cell(f20 & (fields["dw_after"] == DW_STAYED)),
                      "2020_forest_cleared_now_farm_bare_built_grass_ha": cell(f20 & (fields["dw_after"] == DW_REPLACED)),
                      "2020_forest_cleared_mixed_ha": cell(f20 & (fields["dw_after"] == DW_MIXED))}
        dw_summary["source"] = lc_name
        print(f"  {lc_name}: " + "; ".join(f"{k.replace('_', ' ')} {v:,.1f}" for k, v in dw_summary.items()
                                          if k != "source"))
    radd_summary = None
    if use_radd:
        hi = (fields["radd_high"] == 1) & (fields["radd_alert"] > 0)
        radd_summary = {
            "2020_forest_watched_by_radd_ha": cell(f20 & (fields["radd_watch"] == 1)),
            "2020_forest_with_radd_alert_after_2020_ha": cell(f20 & (fields["radd_alert"] > 0)),
            "of_which_high_confidence_ha": cell(f20 & hi),
            "radd_only_alert_on_2020_forest_ha": cell(f20 & hi & (fields["tmf_clear"] == 0)
                                                       & (fields["hansen_loss"] == 0)),
        }
        print("  RADD: " + "; ".join(f"{k.replace('_', ' ')} {v:,.1f}" for k, v in radd_summary.items()))
    alerts_summary = None
    if use_alerts:
        after_code = (alerts_after or 2100) - CUTOFF_YEAR
        recent = fields["alert_year"] > after_code
        agree = recent & (fields["radd_alert"] > after_code)
        recent &= agree | (fields["alert_conf"] == ALERT_HIGH)   # the alerts the rule counts
        alerts_summary = {
            "files": [p.name for k in ("alerts", "dist_alerts") for p in found.get(k, [])],
            "yearly_data_until": yearly_last,
            "2020_forest_with_optical_alert_after_2020_ha": cell(f20 & (fields["alert_year"] > 0)),
            "2020_forest_with_counted_optical_alert_after_yearly_data_ha": cell(f20 & recent),
            "of_which_radd_radar_agrees_ha": cell(f20 & agree),
            "of_which_no_yearly_or_radd_record_ha": cell(f20 & recent & (fields["tmf_clear"] == 0)
                                                         & (fields["hansen_loss"] == 0) & (fields["radd_alert"] == 0)),
        }
        print("  Alerts: " + "; ".join(f"{k.replace('_', ' ')} {v:,.1f}" for k, v in alerts_summary.items()
                                       if isinstance(v, float)))
    print("Writing rasters ...")
    t0 = time.perf_counter()
    ev_path, oc_path = out_dir / "evidence.tif", out_dir / "outcome.tif"
    input_names = sorted({p.name for ps in found.values() for p in ps} |
                         {p.name for ps in annual.values() for p in ps})
    common = {
        "PRODUCER": f"eudr_layers.py ({SCRIPT_VERSION})",
        "CREATED_UTC": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "REGULATION": "EU Deforestation Regulation (EU) 2023/1115; deforestation cut-off 31 December 2020",
        "BBOX_LONLAT": json.dumps(args.bbox),
        "GRID": f"{args.grid} grid, pixel {abs(grid.dx):.10f} deg, {grid.crs}",
        "SOURCE_FILES": json.dumps(input_names),
        "INPUT_LAYERS": json.dumps(sorted(found) + [f"tmf_annual_{y}" for y in sorted(annual)]),
        "RULE": rule_text(args.hansen_only, settled_until, use_radd, use_dw, alerts_after),
        "RULE_PARAMS": json.dumps({"hansen_only": rule[0], "settled_until": rule[1], "tmf_last_year": tmf_last,
                                   "alerts_after_year": alerts_after}),
    }
    legend = outcome_legend()
    codec_ev = write_cog(
        ev_path, packed, grid, compress=args.compress,
        tags={**common, "CONTENT": f"Every EUDR decision fact per pixel, packed into {PACKED_BITS} bits",
              "BIT_LAYOUT": json.dumps(field_layout()),
              "DECODE": "field value = (pixel >> first bit) & (2**number_of_bits - 1)"},
        band_description="Packed EUDR evidence (see BIT_LAYOUT)")
    codec_oc = write_cog(
        oc_path, outcome, grid, nodata=NODATA, colormap=outcome_colormap(), compress=args.compress,
        tags={**common, "CONTENT": "EUDR outcome class per pixel (see LEGEND)",
              "LEGEND": json.dumps(legend),
              "HECTARES": json.dumps({c: round(float(areas[c]), 2) for c in legend if areas[c] > 0})},
        band_tags={f"CLASS_{c}": v["label"] for c, v in legend.items()},
        band_description="EUDR outcome class (see LEGEND)")
    qml_for_outcome(out_dir / "outcome.qml")

    # Compact version for viewing: one colour per kind of result, "not forest" by land type
    s_path, simple, s_legend, s_areas, s_ok = write_simple_outputs(
        out_dir, outcome, fields, transition, grid, row_area, common, rubber is not None, args.compress)

    ev_ok = read_back_equal(ev_path, packed)
    cog_ok = ev_ok and read_back_equal(oc_path, outcome) and s_ok
    meta_ok = (tags_inside(ev_path, ["BIT_LAYOUT", "RULE", "RULE_PARAMS"]) and tags_inside(oc_path, ["LEGEND", "RULE"])
               and tags_inside(s_path, ["LEGEND", "RULE"]))
    # Pixel fidelity: what evidence.tif keeps of each original layer. The file read back
    # bit-identical (ev_ok), so its decoded fields are `fields`.
    with rasterio_open(ev_path) as src:
        fb = fields if ev_ok else unpack(src.read(1))
    pix_rows = pixel_fidelity(L, fb, tuple(args.gfc_forest_values))
    import pandas as pd
    pd.DataFrame(pix_rows).to_csv(out_dir / "pixel_fidelity.csv", index=False)
    Path(str(oc_path) + ".aux.xml").write_text(rat_aux_xml(areas), encoding="utf-8")
    t_write = time.perf_counter() - t0

    # 5. Tables
    defo_table, other_table = deforestation_table(areas)
    defo_table.to_csv(out_dir / "deforestation_by_year.csv", index=False)
    other_table.to_csv(out_dir / "land_by_class.csv", index=False)
    comparison = None
    if treecover is not None and "lossyear" in found and "datamask" in found:
        flagged, _ = hansen_only(treecover, lossyear, datamask, args.treecover_threshold)
        group = np.where(outcome >= 20, (outcome // 10) * 10, outcome).astype(np.uint8)
        a_flag = class_areas_ha(np.where(flagged, group, 255).astype(np.uint8), row_area)
        a_all = class_areas_ha(group, row_area)
        rows = []
        for g in [CONFIRMED, PROBABLE, REVIEW, CLEARED_NON_FOREST, FOREST, FOREST_RUBBER,
                  DEGRADED, NOT_FOREST, WATER]:
            name = GROUP_NAMES.get(g, BASE_LABELS.get(g, ("?",))[0])
            rows.append({"this_rule": name, "hectares": round(a_all[g], 2),
                         "of_which_hansen_only_flags": round(a_flag[g], 2)})
        import pandas as pd
        comparison = pd.DataFrame(rows)
        comparison.to_csv(out_dir / "hansen_vs_multisource.csv", index=False)

    # 6. Polygons
    print(f"Rasters written in {t_write:.1f}s. Drawing polygons ...")
    t0 = time.perf_counter()
    import geopandas as gpd
    gpkg = out_dir / "layers.gpkg"
    if gpkg.exists():
        gpkg.unlink()
    poly_checks = {}

    defo_codes = [c for c in range(CONFIRMED + 1, REVIEW + 8) if areas[c] > 0]
    labels, code_of = patch_labels(outcome, defo_codes)
    n_patches = len(code_of) - 1
    if n_patches:
        stats = patch_stats(labels, code_of, row_area, {
            "hansen_prior_px": fields["hansen_prior"] == 1,
            "tmf_to_plantation_px": (transition == TMF_TO_PLANTATION) if transition is not None else None,
            "protected_px": fields["protected"] == 1 if protected is not None else None,
        })
        geoms = {}
        for lab, g in polygons(labels, labels > 0, grid):
            geoms[lab] = g if lab not in geoms else geoms[lab].union(g)
        ids = np.array(sorted(geoms))
        codes = code_of[ids]
        groups = (codes // 10) * 10
        gdf = gpd.GeoDataFrame({
            "patch_id": ids,
            "outcome": [GROUP_NAMES[g] for g in groups],
            "year": CUTOFF_YEAR + codes - groups,
            "pixels": stats["pixels"][ids],
            "area_ha": np.round(stats["area_ha"][ids], 4),
            **{k: v[ids] for k, v in stats.items() if k not in ("pixels", "area_ha")},
        }, geometry=[geoms[i] for i in ids], crs=grid.crs)
        geo_ha = geodesic_area_m2(gdf.geometry) / 1e4
        poly_checks["deforestation"] = {
            "patches": int(len(gdf)), "pixel_ha": round(float(stats["area_ha"][ids].sum()), 4),
            "polygon_ha": round(float(geo_ha.sum()), 4),
            "max_relative_difference": float(np.max(np.abs(geo_ha - stats["area_ha"][ids])
                                                    / np.maximum(stats["area_ha"][ids], 1e-9)))}
        gdf.to_file(gpkg, layer="deforestation", driver="GPKG")
        print(f"  deforestation: {len(gdf):,} patches ({time.perf_counter() - t0:.0f}s)")

    def class_polygons(layer, values, mask, names):
        t1 = time.perf_counter()
        print(f"  {layer} ...")
        items = polygons(values, mask, grid)
        if not items:
            return
        gdf = gpd.GeoDataFrame({"class": [names[v] for v, _ in items]},
                               geometry=[g for _, g in items], crs=grid.crs)
        gdf["area_ha"] = np.round(geodesic_area_m2(gdf.geometry) / 1e4, 4)
        pix = class_areas_ha(np.where(mask, values, 0).astype(np.uint8), row_area)
        poly_checks[layer] = {"polygons": int(len(gdf)),
                              "pixel_ha": round(float(pix[1:].sum()), 2),
                              "polygon_ha": round(float(gdf["area_ha"].sum()), 2)}
        gdf.to_file(gpkg, layer=layer, driver="GPKG")
        print(f"  {layer}: {len(gdf):,} polygons ({time.perf_counter() - t1:.0f}s)")

    if not args.no_forest_polygons:
        ftype = np.where(fields["gfc_forest"] == 1,
                         np.where(fields["gft_type"] > 0, fields["gft_type"], 4), 0).astype(np.uint8)
        class_polygons("forest_2020", ftype, ftype > 0, GFT_NAMES)
    if rubber is not None and not args.no_rubber_polygons:
        r = fields["rubber"]
        class_polygons("rubber_2020", r, r > 0, {1: "rubber"})
    t_poly = time.perf_counter() - t0

    # 6b. Plot-level query against the compact layers on disk
    plot_summary = None
    if plots is not None:
        print("Judging each plot against the compact layers ...")
        rows_p, t_q = query_plots(plots, ev_path, grid, row_area, rule, args.min_plot_ha)
        hv = re.search(r"v\d+\.\d+", found["lossyear"][0].name) if "lossyear" in found else None
        layers_used = " + ".join(
            ["JRC GFC2020"] + (["JRC GFT2020"] if gft is not None else [])
            + ([f"JRC TMF annual change {min(annual)}-{max(annual)}"] if annual else [])
            + (["JRC TMF DeforestationYear"] if defo is not None else [])
            + ([f"Hansen GFC {hv.group(0) if hv else ''} loss 2021-2025".replace("  ", " ")]
               if "lossyear" in found else [])
            + (["rubber map 2020"] if rubber is not None else []))
        theirs = read_due_diligence_csv(args.plots_csv) if args.plots_csv else {}
        for r in rows_p:
            r["c2_deforestation_spatial_analysis_result"] = (
                "not assessed" if r["verdict"] == "not assessed"
                else C2_NO if r["verdict"] == "deforestation-free" else C2_YES)
            r["c1_deforestation_analysis_layers"] = layers_used
            r["d1_strict_protected_area"] = (
                ("a. No overlap with strict PA" if r["protected_ha"] == 0 else "b. Overlap with strict PA")
                if protected is not None else "not assessed (no protected-area layer given)")
            r["hansen_only_screen_2021_2023"] = C2_YES if r["hansen_loss_2021_2023_ha"] > 0 else C2_NO
            t = theirs.get(r["plot_id"])
            if t is not None:
                r["their_c1"] = t.get("c1_indicate_deforestation_analysis_layer_used", "")
                r["their_c2"] = t.get("c2_deforestation_spatial_analysis_result", "")
                r["their_c5"] = t.get("c5_deforestation_final_status", "")
                r["their_d1"] = t.get("d1_strict_protected_area_spatial_analysis_result", "")
                r["c2_matches_theirs"] = r["c2_deforestation_spatial_analysis_result"] == r["their_c2"]
                r["hansen_screen_matches_theirs"] = r["hansen_only_screen_2021_2023"] == r["their_c2"]
        import pandas as pd
        df = pd.DataFrame(rows_p)
        first = ["plot_id", "verdict", "reason", "c2_deforestation_spatial_analysis_result",
                 "d1_strict_protected_area", "area_ha", "forest_2020_ha", "confirmed_ha", "probable_ha",
                 "probable_tmf_only_ha", "probable_hansen_only_ha", "review_ha",
                 "first_deforestation_year", "deforestation_years"]
        df = df[first + [c for c in df.columns if c not in first]]
        df.to_csv(out_dir / "plots_result.csv", index=False, encoding="utf-8-sig")
        gp = out_dir / "plots_result.gpkg"
        if gp.exists():
            gp.unlink()
        gpd.GeoDataFrame(df.astype({c: str for c in df.columns if df[c].dtype == object}),
                         geometry=list(plots.geometry), crs="EPSG:4326").to_file(gp, layer="plots", driver="GPKG")
        plot_summary = {"plots": int(len(df)), "verdicts": {k: int(v) for k, v in df["verdict"].value_counts().items()},
                        "query_seconds_all_plots": round(t_q, 4),
                        "query_ms_per_plot": round(1000 * t_q / max(1, len(df)), 2)}
        if theirs:
            n = same_c2 = same_h = 0
            if "c2_matches_theirs" in df:
                known = df["c2_matches_theirs"].notna()
                n = int(known.sum())
                same_c2 = int(df.loc[known, "c2_matches_theirs"].astype(bool).sum())
                same_h = int(df.loc[known, "hansen_screen_matches_theirs"].astype(bool).sum())
            plot_summary["c2_same_as_their_csv"] = f"{same_c2}/{n}"
            plot_summary["hansen_2021_2023_screen_same_as_their_csv"] = f"{same_h}/{n}"

    # 6c. Test plots: half at random, half on each kind of outcome
    test_plots, test_summary = None, None
    if args.test_plots > 0:
        tp_path = out_dir / "test_plots.geojson"
        tp_path.write_text(json.dumps(make_test_plots(args.test_plots, grid, outcome, args.seed)))
        test_plots = read_plots(tp_path, "ProductionPlace")
        print(f"Judging {len(test_plots):,} generated test plots against evidence.tif ...")
        rows_t, t_qt = query_plots(test_plots, ev_path, grid, row_area, rule, args.min_plot_ha)
        import pandas as pd
        df_t = pd.DataFrame(rows_t)
        df_t.insert(1, "test_stratum", list(test_plots["test_stratum"]))
        df_t.to_csv(out_dir / "test_plots_result.csv", index=False, encoding="utf-8-sig")
        test_summary = {"plots": int(len(df_t)),
                        "verdicts": {k: int(v) for k, v in df_t["verdict"].value_counts().items()},
                        "by_stratum": {k: int(v) for k, v in df_t["test_stratum"].value_counts().items()},
                        "query_seconds_all_plots": round(t_qt, 4),
                        "query_ms_per_plot": round(1000 * t_qt / max(1, len(df_t)), 3)}

    # 6d. Decision and pixel fidelity against the original layers
    fidelity, t_verify = None, 0.0
    if args.verify:
        sets = ([("given", plots)] if plots is not None else []) + \
               ([("test", test_plots)] if test_plots is not None else [])
        if not sets:
            print("  --verify needs plots to check: add --plots and/or --test-plots N.")
        else:
            t0 = time.perf_counter()
            n_all = sum(len(P) for _, P in sets)
            print(f"Checking {n_all:,} plots against the original layers (each judged from evidence.tif, "
                  "from outcome.tif and straight from the input rasters) ...")
            all_rows, px_all = [], None
            for set_name, P in sets:
                rows_v, px = verify_plots(P, ev_path, oc_path, grid, row_area, found, annual, args, rule,
                                          args.min_plot_ha, set_name)
                all_rows += rows_v
                if px_all is None:
                    px_all = px
                else:
                    px_all["pixels_compared"] += px["pixels_compared"]
                    px_all["outcome_mismatches"] += px["outcome_mismatches"]
                    px_all["original_files"] = max(px_all["original_files"], px["original_files"])
                    for k, v in px["field_mismatches"].items():
                        px_all["field_mismatches"][k] += v
            import pandas as pd
            pd.DataFrame(all_rows).to_csv(out_dir / "fidelity_plots.csv", index=False, encoding="utf-8-sig")
            fidelity = fidelity_summary(all_rows, px_all)
            t_verify = time.perf_counter() - t0

    # 7. Sizes and optional codec comparison
    print(f"Polygons drawn in {t_poly:.1f}s. Measuring sizes ...")
    inputs =[p for ps in found.values() for p in ps] + [p for ps in annual.values() for p in ps]
    raw_aoi = sum(native_window_bytes([p], args.bbox) for p in inputs)
    baseline = None
    with tempfile.TemporaryDirectory() as tmp:
        if not args.no_baseline:
            baseline = compressed_baseline_bytes(inputs, args.bbox, tmp)
        codec_rows = compression_test(packed, grid, tmp) if args.compress_test else None
    if codec_rows:
        import pandas as pd
        pd.DataFrame(codec_rows).to_csv(out_dir / "codec_test_evidence.csv", index=False)

    sizes = {"evidence.tif": ev_path.stat().st_size, "outcome.tif": oc_path.stat().st_size,
             "outcome_simple.tif": s_path.stat().st_size,
             "layers.gpkg": gpkg.stat().st_size if gpkg.exists() else 0}
    summary = {
        "bbox": args.bbox, "grid": {"choice": args.grid, "width": grid.width, "height": grid.height,
                                    "pixel_deg": abs(grid.dx), "crs": str(grid.crs),
                                    "mean_pixel_m2": round(float(row_area.mean()), 2)},
        "profile": profile,
        "codecs": {"evidence": codec_ev, "outcome": codec_oc},
        "sizes_bytes": {"inputs_raw_for_bbox": int(raw_aoi),
                        "inputs_clipped_deflate": baseline, **sizes},
        "hectares": {outcome_label(c): round(float(a), 2) for c, a in enumerate(areas) if a > 0},
        "hectares_simple_map": {s_legend[c]["label"]: round(float(s_areas[c]), 2)
                                for c in s_legend if c != S_NODATA},
        "checks": {"pack_unpack_identical": roundtrip_ok, "cog_read_back_identical": cog_ok,
                   "legend_and_bit_layout_inside_tifs": meta_ok, "polygons": poly_checks},
        "rule_params": {"hansen_only": rule[0], "settled_until": rule[1], "tmf_last_year": tmf_last,
                        "alerts_after_year": alerts_after},
        "plot_query_reads": "evidence.tif only: one window per plot, rule applied to the decoded facts",
        "plots": plot_summary,
        "test_plots": test_summary,
        "fidelity": fidelity,
        "pixel_fidelity": pix_rows,
        "radd": radd_summary,
        "alerts": alerts_summary,
        "dynamic_world": dw_summary,
        "timings_s": {"read_align_and_facts": round(t_read, 1), "packing_and_rule": round(t_logic, 1),
                      "write_rasters": round(t_write, 1), "polygons": round(t_poly, 1),
                      "verify": round(t_verify, 1), "total": round(time.perf_counter() - t_start, 1)},
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str))

    # 8. Report
    print(f"\nDeforestation by year (ha), {out_dir / 'deforestation_by_year.csv'}:")
    print(defo_table.to_string(index=False))
    print("\nOther land (ha):")
    print(other_table.to_string(index=False))
    print("\nCompact map, outcome_simple.tif (ha):")
    for c in s_legend:
        if c != S_NODATA:
            print(f"  {s_legend[c]['label']:55s} {s_areas[c]:12,.2f}")
    if comparison is not None:
        print("\nWhat a Hansen-only rule would flag, by this rule's outcome (ha):")
        print(comparison.to_string(index=False))
    print("\nSizes for this box:")
    print(f"  inputs, uncompressed            {human(raw_aoi)}")
    if baseline:
        print(f"  inputs, clipped + DEFLATE       {human(baseline)}")
    for k, v in sizes.items():
        print(f"  {k:31s} {human(v)}")
    if codec_rows:
        print("\nLossless codecs on the packed layer:")
        for r in codec_rows:
            print(f"  {r['codec']:9s} predictor {r['predictor']:4}  {human(r['bytes']):>9s}  "
                  f"write {r['write_s']:5.2f}s  read {r['full_read_s']:6.3f}s  "
                  f"256px window {r['window_read_ms']:6.2f} ms  lossless={r['lossless']}")
    if plot_summary:
        print(f"\nPlots ({out_dir / 'plots_result.csv'}), queried from the compact layers in "
              f"{plot_summary['query_ms_per_plot']} ms per plot:")
        cols = ["plot_id", "area_ha", "forest_2020_ha", "confirmed_ha", "probable_ha", "review_ha",
                "verdict", "hansen_loss_2021_2023_ha"] + (["their_c2"] if "their_c2" in df else [])
        print(df[cols].to_string(index=False))
        for k in ("c2_same_as_their_csv", "hansen_2021_2023_screen_same_as_their_csv"):
            if k in plot_summary:
                print(f"  {k.replace('_', ' ')}: {plot_summary[k]}")
    if test_summary:
        print(f"\nTest plots ({out_dir / 'test_plots_result.csv'}): {test_summary['plots']:,} judged in "
              f"{test_summary['query_ms_per_plot']} ms per plot from evidence.tif; verdicts: "
              + ", ".join(f"{k} {v:,}" for k, v in test_summary["verdicts"].items()))
    print("\nPixel fidelity, per input layer (pixel_fidelity.csv):")
    for r in pix_rows:
        pct = "n/a" if r["exact_values_recovered_pct"] is None else f"{r['exact_values_recovered_pct']:.2f}%"
        print(f"  {r['layer'][:44]:44s} {r['stored_as'][:44]:44s} exact {pct:>8s}  lost: {r['what_is_lost']}")
    if fidelity and fidelity.get("plots_compared"):
        f_ = fidelity
        bad_fields = [k for k, v in f_["pixel_fidelity_pct_by_field"].items() if v < 100]
        print(f"\nFidelity against the original layers ({out_dir / 'fidelity_plots.csv'}):")
        print(f"  plots compared                     {f_['plots_compared']:,} "
              f"({', '.join(f'{v:,} {k}' for k, v in f_['plots_by_set'].items())})")
        print(f"  every output identical             {f_['plots_with_every_output_identical']:,} = "
              f"{f_['decision_fidelity_pct']}%  (decision fidelity; {f_['outputs_compared_per_plot']} outputs "
              "per plot: verdict, reason, every hectare figure, years)")
        print(f"  same verdict                       {f_['same_verdict_pct']}%")
        print(f"  outcome.tif gives the same         {f_['outcome_tif_gives_same_outputs_pct']}%")
        print(f"  pixels compared                    {f_['pixels_compared']:,}; facts identical in "
              f"{len(FIELDS) - len(bad_fields)}/{len(FIELDS)} fields"
              + (f" (differ: {', '.join(bad_fields)})" if bad_fields else "")
              + f"; outcome identical {f_['outcome_pixels_identical_pct']}%")
        print(f"  time per plot (median)             evidence.tif {f_['query_ms_median']['evidence_tif']} ms "
              f"(1 file) vs original layers {f_['query_ms_median']['original_layers']} ms "
              f"({f_['files_read_per_plot']['original_layers']} files): {f_['speedup_median']}x faster")
    print("\nChecks:")
    print(f"  pack/unpack identical          {roundtrip_ok}")
    print(f"  COGs read back identical       {cog_ok}")
    print(f"  legend/bit layout inside tifs  {meta_ok}")
    for layer, c in poly_checks.items():
        print(f"  {layer:30s} pixel {c['pixel_ha']:,} ha vs polygon {c['polygon_ha']:,} ha")
    print(f"\nDone in {summary['timings_s']['total']}s. Outputs in {out_dir}")


if __name__ == "__main__":
    main()
