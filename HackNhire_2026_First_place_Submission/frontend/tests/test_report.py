import unittest

from shapely.geometry import Point, box

import report
from backend import common
from legend import DEFORESTATION, FOREST, LAND, NO_DATA, REVIEW, WATER


def outline_farm(name="F"):
    return common.Farm(name, {}, box(103.1, -1.91, 103.11, -1.9), "outline")


def point_farm(name="P"):
    return common.Farm(name, {}, Point(103.1, -1.9), "point")


def exact(areas_m2, name="F"):
    """An answer like the outlines give, from areas in m2 (the farm's area is their sum)."""
    return common.exact_result(outline_farm(name), dict(areas_m2), sum(areas_m2.values()))


def row_for(stencils, polygons, tiff_pixel=None, tiff_exact=None, kind="outline", area_ha=1.0, key=1, name="F"):
    results = {"stencils": stencils, "polygons": polygons}
    if tiff_pixel is not None:
        results["tiff_pixel"], results["tiff_exact"] = tiff_pixel, tiff_exact
    return report.farm_row(key, name, kind, area_ha, results)


class RowTest(unittest.TestCase):
    def test_worst(self):
        self.assertEqual(report.worst(["PASS", "FAIL", None]), "FAIL")
        self.assertEqual(report.worst(["CHECK_LOCATION", "CANNOT_CHECK"]), "CANNOT_CHECK")
        self.assertIsNone(report.worst([None]))

    def test_agreement_follows_compare_py(self):
        a, b = exact({LAND: 9000, FOREST: 1000}), exact({LAND: 10000})
        row = row_for(a, a, tiff_pixel=a, tiff_exact=b)
        self.assertEqual(row["agree"], {"stencils_tiff": True, "polygons_tiff": False, "stencils_polygons": True})
        self.assertIsNone(row_for(a, a)["agree"]["stencils_tiff"])

    def test_result_json(self):
        r = report.result_json(exact({LAND: 9000, DEFORESTATION: 1000}))
        self.assertEqual(r["verdict"], "FAIL")
        self.assertEqual(r["codes"], [LAND, DEFORESTATION])
        self.assertAlmostEqual(r["ha"][str(DEFORESTATION)], 0.1)


class CardTest(unittest.TestCase):
    def card(self, stencils, polygons, kind="outline", area_ha=1.0):
        return report.verdict_card(row_for(stencils, polygons, kind=kind, area_ha=area_ha))

    def test_fail(self):
        r = exact({LAND: 9000, DEFORESTATION: 1000})
        card = self.card(r, r)
        self.assertEqual((card["headline"], card["icon"], card["colour"]), ("Not compliant", "✖", "#d03b3b"))
        self.assertEqual(card["sentence"], "Deforestation after 31 Dec 2020 on 0.10 ha (10.0% of the farm).")

    def test_the_worse_approach_wins(self):
        card = self.card(exact({LAND: 9990, DEFORESTATION: 10}), exact({LAND: 10000}))
        self.assertEqual((card["verdict"], card["source"]), ("FAIL", "stencils"))
        self.assertEqual(card["sentence"], "Deforestation after 31 Dec 2020 on <0.01 ha (0.1% of the farm).")

    def test_a_farm_in_a_protected_area_needs_review_and_says_why(self):
        from backend import protected_areas
        r = exact({LAND: 10000})
        protected_areas.apply([r], {0: protected_areas.Overlap(("Berbak",), 2500.0)})
        card = self.card(r, r)
        self.assertEqual((card["verdict"], card["outcome"]), ("MANUAL_REVIEW", "NEEDS_REVIEW"))
        self.assertEqual(card["detail"], report.PROTECTED_DETAIL)
        self.assertEqual(card["sentence"],
                         "The farm doesn't overlap forest of 31 Dec 2020, so it can't be deforestation. 0.25 ha of "
                         "the farm is in a protected area (Berbak), so a person has to check that farming is "
                         "allowed there.")
        row = row_for(r, r)
        import results_panel
        self.assertEqual(results_panel.table_cells(row)[6], "0.25 ha")
        self.assertIn(report.PROTECTED_DETAIL, results_panel.table_cells(row)[1])
        p = common.point_result(point_farm(), LAND)
        protected_areas.apply([p], {0: protected_areas.Overlap(("Berbak",), 0.0)})
        self.assertEqual(results_panel.table_cells(row_for(p, p, kind="point"))[6], "yes")
        self.assertEqual(results_panel.table_cells(row_for(exact({LAND: 10000}), exact({LAND: 10000})))[6], "–")

    def test_needs_review(self):
        r = exact({LAND: 7500, REVIEW: 2500})
        self.assertEqual(self.card(r, r)["sentence"],
                         "0.25 ha (25.0% of the farm) may have been cleared after 2020, so a person has to check.")

    def test_check_location(self):
        r = exact({LAND: 4000, WATER: 6000})
        self.assertEqual(self.card(r, r)["sentence"],
                         "60.0% of the farm is water, so its coordinates are probably wrong.")

    def test_no_data(self):
        r = exact({LAND: 9500, NO_DATA: 500})
        self.assertEqual(self.card(r, r)["sentence"], "0.05 ha of the farm has no data.")

    def test_pass_with_forest(self):
        r = exact({LAND: 5000, FOREST: 5000})
        card = self.card(r, r)
        self.assertEqual(card["headline"], "Compliant")
        self.assertEqual(card["sentence"],
                         "No deforestation after 31 Dec 2020. Forest inside: 0.50 ha (allowed under the EUDR).")

    def test_outline_outside_the_map(self):
        r = common.outside_result(outline_farm())
        self.assertEqual(self.card(r, r)["sentence"], "Part of the farm is outside the map, so it can't be checked.")

    def test_points(self):
        def card_for(colour):
            r = common.point_result(point_farm(), colour)
            return self.card(r, r, kind="point", area_ha=0.0)

        self.assertEqual(card_for(DEFORESTATION)["sentence"],
                         "The point is on deforestation: forest cleared after 31 Dec 2020.")
        self.assertEqual(card_for(REVIEW)["sentence"],
                         "The point is on land flagged for review: it may have been cleared after 2020, "
                         "so a person has to check.")
        self.assertEqual(card_for(WATER)["sentence"], "The point is on water, so its coordinates are probably wrong.")
        self.assertEqual(card_for(FOREST)["sentence"], "The point is on forest. Forest is allowed under the EUDR.")
        self.assertEqual(card_for(LAND)["sentence"], "The point is on land.")
        outside = common.outside_result(point_farm())
        self.assertEqual(self.card(outside, outside, kind="point", area_ha=0.0)["sentence"],
                         "The point is outside the map.")


class SummaryTest(unittest.TestCase):
    def test_counts_agreement_timing_and_forest(self):
        fail, ok = exact({LAND: 9000, DEFORESTATION: 1000}), exact({LAND: 5000, FOREST: 5000})
        forest_point = common.point_result(point_farm(), FOREST)
        rows = [row_for(fail, fail, key=1), row_for(ok, ok, key=2),
                report.farm_row(3, "P", "point", 0.0, {"stencils": forest_point, "polygons": forest_point})]
        s = report.summarize(rows, {"stencils": 0.03, "polygons": 0.06})
        self.assertEqual(s["verdicts"]["stencils"]["FAIL"], 1)
        self.assertEqual(s["verdicts"]["polygons"]["PASS"], 2)
        self.assertEqual(s["agree"]["stencils_polygons"], {"same": 3, "of": 3, "pct": 100.0})
        self.assertEqual(s["agree"]["stencils_tiff"], {"same": 0, "of": 0, "pct": None})
        self.assertAlmostEqual(s["timing"]["polygons"]["per_farm_ms"], 20.0)
        self.assertEqual(s["forest"], {"points_in_forest": 1, "outlines_overlapping": 1})
        self.assertEqual((s["n"], s["points"], s["outlines"]), (3, 1, 2))
