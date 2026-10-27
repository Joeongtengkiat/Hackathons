"""
layers.py - the compaction step's results folder, as the database side sees it.

Step 1, compaction (compaction/eudr_layers.py, Joe's code), turns about 20 satellite layers into a
results folder, normally layers/<map>/:
    evidence.tif   every EUDR decision fact per pixel, packed into 32 bits: forest on 31 Dec 2020,
                   forest type, the year each source (TMF, Hansen, RADD radar, optical alerts) records
                   a clearing, rubber, water, ... Nothing the decision needs is lost.
    outcome.tif    the rule's answer per pixel: not forest, forest, deforestation confirmed /
                   probable / needs review (with the year), water, no data
    test_plots.geojson, summary.json, fidelity_plots.csv, ...   (see eudr_layers.py)

This module reads that folder for step 2, storage:
  * class_map(): outcome.tif in the 5 colours the stencils and outlines store (land, water, forest,
    deforestation, needs review), saved next to it as outcome_5class.tif. They are the colours of
    Joe's own 5-colour map (eudr_layers.py 1.4), which needs no conversion.
  * the evidence's rule and bit layout, for evidence_approach.py.
The EUDR rule is only ever run by eudr_layers.py: nothing here re-implements it.
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np
import rasterio
from rasterio.windows import Window

import common

if common.COMPACTION_DIR not in sys.path:
    sys.path.insert(0, common.COMPACTION_DIR)
import eudr_layers  # noqa: E402

CLASS_MAP = "outcome_5class.tif"
NO_DATA_VALUE = 255                # the class map's no-data value (read as common.NO_DATA)
BUILD_HINT = ("Build one first (it reads your satellite layers and writes layers/NAME/):\n"
              "  python pipeline.py --name NAME --data-dir YOUR_LAYERS --bbox XMIN YMIN XMAX YMAX")

# eudr_layers.py's outcome class -> colour, as in its own 5-colour map (1.4):
#   land          not forest on 31 Dec 2020, including land cleared since (not deforestation)
#   forest        2020 forest still standing, including damaged-but-standing forest and forest the
#                 rubber map calls rubber
#   deforestation confirmed clearing of 2020 forest (two independent sources)
#   needs review  probable or unconfirmed clearing of 2020 forest
_COLOUR = np.full(256, NO_DATA_VALUE, np.uint8)
_COLOUR[eudr_layers.WATER] = common.WATER
_COLOUR[[eudr_layers.NOT_FOREST, eudr_layers.CLEARED_NON_FOREST]] = common.LAND
_COLOUR[[eudr_layers.FOREST, eudr_layers.FOREST_RUBBER, eudr_layers.DEGRADED]] = common.FOREST
for _year in range(1, eudr_layers.LAST_CODABLE_YEAR - eudr_layers.CUTOFF_YEAR + 1):
    _COLOUR[eudr_layers.CONFIRMED + _year] = common.DEFORESTATION
    _COLOUR[eudr_layers.PROBABLE + _year] = common.REVIEW
    _COLOUR[eudr_layers.REVIEW + _year] = common.REVIEW

LEGEND = {
    common.LAND: ("Land (not forest in 2020)", (225, 225, 225),
                  "Not forest on 31 Dec 2020 (JRC GFC2020), including land cleared since then, which is "
                  "not deforestation under the EUDR."),
    common.WATER: ("Water", (107, 174, 214), "Water per Hansen's data mask or TMF."),
    common.FOREST: ("Forest (2020 forest still standing)", (27, 120, 55),
                    "Forest on 31 Dec 2020, not cleared since: includes forest damaged but standing, and "
                    "forest the rubber map calls rubber."),
    common.DEFORESTATION: ("Deforestation (confirmed)", (227, 26, 28),
                           "2020 forest cleared in 2021 or later; two independent sources record it."),
    common.REVIEW: ("Needs review", (106, 61, 154),
                    "2020 forest that may have been cleared after 2020, on evidence short of two "
                    "independent sources (probable or unconfirmed)."),
    NO_DATA_VALUE: ("No data", (0, 0, 0), "Outside the data."),
}
_KEEP_TAGS = ("PRODUCER", "CREATED_UTC", "REGULATION", "BBOX_LONLAT", "GRID", "SOURCE_FILES",
              "INPUT_LAYERS", "RULE", "RULE_PARAMS")


def five_classes(outcome: np.ndarray) -> np.ndarray:
    """eudr_layers.py outcome classes -> the 5 colours (0-4), with NO_DATA_VALUE for no data."""
    return _COLOUR[outcome]


def evidence_file(folder: str) -> str:
    """The evidence in a results folder (evidence_16bit.tif in results made before RADD was added)."""
    return str(eudr_layers.evidence_path(folder))


def results_folder(path: str) -> str:
    """A results folder of the compaction step, given as its path, a file in it, or its name in
    layers/ (e.g. 'jambi'). Stops with what to do if there isn't one."""
    for candidate in (path, os.path.join(common.ROOT_DIR, path), os.path.join(common.LAYERS_DIR, path)):
        if os.path.isfile(candidate):
            candidate = os.path.dirname(candidate) or "."
        if os.path.isdir(candidate):
            folder = os.path.abspath(candidate)
            if not (os.path.isfile(evidence_file(folder))
                    and os.path.isfile(os.path.join(folder, "outcome.tif"))):
                sys.exit(f"{common.nice_path(folder)} has no evidence.tif and outcome.tif, so it isn't "
                         f"a results folder of the compaction step. {BUILD_HINT}")
            return folder
    sys.exit(f"Can't find the results folder '{path}'. Give its path, or the name of a folder in "
             f"{common.nice_path(common.LAYERS_DIR)}{os.sep} (e.g. jambi). {BUILD_HINT}")


def read_tags(path: str) -> dict:
    with rasterio.open(path) as src:
        return src.tags()


def summary(folder: str) -> dict | None:
    """The compaction step's summary.json (sizes, timings, fidelity), if it is there."""
    path = os.path.join(folder, "summary.json")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def class_map(folder: str) -> str:
    """The results folder's outcome in the 5 colours, as a TIFF the stencils and outlines are built
    from. eudr_layers.py 1.4 and later write outcome.tif in the 5 colours already (it says so in its
    OUTCOME_SCHEME tag); otherwise outcome_5class.tif is written next to it (once: it is reused while
    it is newer than outcome.tif)."""
    outcome = os.path.join(folder, "outcome.tif")
    if read_tags(outcome).get("OUTCOME_SCHEME"):
        return outcome
    target = os.path.join(folder, CLASS_MAP)
    if os.path.isfile(target) and os.path.getmtime(target) >= os.path.getmtime(outcome):
        return target
    partial = target + ".partial"                     # never leave a half-written class map behind
    with rasterio.open(outcome) as src:
        tags = src.tags()
        profile = dict(driver="GTiff", width=src.width, height=src.height, count=1, dtype="uint8",
                       crs=src.crs, transform=src.transform, nodata=NO_DATA_VALUE, tiled=True,
                       blockxsize=common.PAGE, blockysize=common.PAGE, compress="deflate")
        with rasterio.open(partial, "w", **profile) as dst:
            for row in range(0, src.height, common.PAGE):
                win = Window(0, row, src.width, min(common.PAGE, src.height - row))
                dst.write(five_classes(src.read(1, window=win)), 1, window=win)
            dst.write_colormap(1, {code: (*rgb, 0 if code == NO_DATA_VALUE else 255)
                                   for code, (_, rgb, _) in LEGEND.items()})
            dst.update_tags(**{k: tags[k] for k in _KEEP_TAGS if k in tags},
                            CONTENT="EUDR outcome per pixel in 5 colours: 0 land, 1 water, 2 forest, "
                                    "3 deforestation, 4 needs review, 255 no data (see LEGEND). Years "
                                    "and the finer classes are in outcome.tif and evidence.tif.",
                            DERIVED_FROM=json.dumps(["outcome.tif"]),
                            LEGEND=json.dumps({str(code): {"label": label, "meaning": meaning,
                                                           "colour": "#{:02X}{:02X}{:02X}".format(*rgb)}
                                               for code, (label, rgb, meaning) in LEGEND.items()}))
    os.replace(partial, target)
    return target


def rule_from(params: dict) -> tuple:
    """(hansen_only mode, settled_until, alerts_after) from evidence.tif's RULE_PARAMS, worked out the
    way eudr_layers.py --query-only does."""
    mode = params.get("hansen_only") or "settle"
    return mode, eudr_layers.settled_year(mode, params.get("tmf_last_year")), params.get("alerts_after_year")


def check_layout(layout: list) -> None:
    """Stop unless the evidence's bit layout (BIT_LAYOUT: field dicts or names) is this
    eudr_layers.py's: otherwise its facts would be read from the wrong bits."""
    names = [f["field"] if isinstance(f, dict) else f for f in layout]
    ours = [name for name, _ in eudr_layers.FIELDS]
    if names != ours:
        sys.exit(f"This evidence was written by another version of eudr_layers.py (bit layout {names}, "
                 f"this version's is {ours}). Rebuild it with compaction/eudr_layers.py, or put that "
                 "version of eudr_layers.py in compaction/.")


def joe_grid(grid: common.Grid) -> eudr_layers.Grid:
    """A stored map's grid as eudr_layers.py's Grid (dy is negative there), so its plot windows,
    masks and pixel areas come out exactly as when it reads evidence.tif."""
    return eudr_layers.Grid(grid.x0, grid.y0, grid.pixel_w, -grid.pixel_h, grid.width, grid.height,
                            rasterio.crs.CRS.from_epsg(grid.srid))
