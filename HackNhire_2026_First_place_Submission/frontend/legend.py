"""legend.py - the colours and words the app uses for map classes, methods and verdicts.

Two palettes, on purpose:
  MAP_COLOURS    the legend embedded in my_map.tif - the data's own colours, used on the map
  CHART_COLOURS  colour-blind-checked colours of the same families, used in charts. Checked with
                 the dataviz validator; stacked bars must keep CHART_ORDER (it passes every check)
Verdicts use fixed status colours and always come with an icon and a label.
"""

from backend import common

LAND, WATER, FOREST, DEFORESTATION, REVIEW, NO_DATA = 0, 1, 2, 3, 4, 5  # as in common.py
CLASSES = (LAND, WATER, FOREST, DEFORESTATION, REVIEW, NO_DATA)

CLASS_LABELS = {LAND: "Land", WATER: "Water", FOREST: "Forest", DEFORESTATION: "Deforestation",
                REVIEW: "Needs review", NO_DATA: "No data"}

MAP_COLOURS = {LAND: "#E1E1E1", WATER: "#6BAED6", FOREST: "#1B7837", DEFORESTATION: "#E31A1C",
               REVIEW: "#6A3D9A", NO_DATA: "#000000"}

CHART_COLOURS = {DEFORESTATION: "#e34948", REVIEW: "#6250d6", FOREST: "#008300", WATER: "#2a78d6",
                 NO_DATA: "#52514e", LAND: "#c3c2b7"}
CHART_ORDER = (DEFORESTATION, REVIEW, FOREST, WATER, NO_DATA, LAND)

PROBLEM_CLASSES = (DEFORESTATION, REVIEW, WATER, NO_DATA)  # highlighted in the focus view

PROTECTED_LABEL = "Protected areas"  # the WDPA polygons stored with a map (protected_areas.py)
PROTECTED_COLOUR = "#FFD000"         # yellow, dashed on the map: unlike every class and farm colour

METHODS = {  # key -> (label, chart colour)
    "stencils": ("Stencils", "#eb6834"),
    "polygons": ("Outlines", "#1baf7a"),
    "tiff_pixel": ("TIFF, pixel rule", "#898781"),
    "tiff_exact": ("TIFF, exact area", "#52514e"),
}

VERDICT_ORDER = ("FAIL", "MANUAL_REVIEW", "CANNOT_CHECK", "CHECK_LOCATION", "PASS")  # worst first
# A farm's result is one of three outcomes; which verdict leads to which is the backend's rule
# (common.OUTCOME_OF). The verdict stays as the reason (VERDICT_DETAIL).
OUTCOME_STYLE = {  # outcome -> (status colour, icon, headline)
    "NON_COMPLIANT": ("#d03b3b", "✖", "Not compliant"),
    "NEEDS_REVIEW": ("#ec835a", "⚠", "Needs review"),
    "COMPLIANT": ("#0ca30c", "✔", "Compliant"),
}
VERDICT_STYLE = {v: OUTCOME_STYLE[common.outcome_of(v)] for v in VERDICT_ORDER}  # verdict -> its outcome's style
VERDICT_DETAIL = {  # why, for the three ways to need review
    "FAIL": "deforestation after 31 Dec 2020",
    "MANUAL_REVIEW": "the map is unsure about clearing",
    "CANNOT_CHECK": "no data or outside the map",
    "CHECK_LOCATION": "on water: check the location",
    "PASS": "no deforestation after 31 Dec 2020",
}


def hex_to_rgb(colour):
    colour = colour.lstrip("#")
    return tuple(int(colour[i:i + 2], 16) for i in (0, 2, 4))


def page_config():
    """What the web pages need to draw legends, charts and verdicts (JSON-ready)."""
    return {
        "classes": [{"code": c, "label": CLASS_LABELS[c], "map": MAP_COLOURS[c], "chart": CHART_COLOURS[c]}
                    for c in CLASSES],
        "chartOrder": list(CHART_ORDER),
        "problemClasses": list(PROBLEM_CLASSES),
        "methods": {key: {"label": label, "colour": colour} for key, (label, colour) in METHODS.items()},
        "verdicts": {v: {"colour": c, "icon": i, "headline": h, "detail": VERDICT_DETAIL[v]}
                     for v, (c, i, h) in VERDICT_STYLE.items()},
        "verdictOrder": list(VERDICT_ORDER),
        "protected": {"label": PROTECTED_LABEL, "colour": PROTECTED_COLOUR},
    }
