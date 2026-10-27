import unittest

import farms as farms_mod
from backend import common


def make_grid():
    return common.Grid("test", 103.0, -1.8, 1 / 12000, 1 / 12000, 6000, 6000, 4326, True,
                       default_class=0, fingerprint="test")


def square(lon, lat, size=0.002):
    return {"type": "Polygon", "coordinates": [[[lon, lat], [lon + size, lat], [lon + size, lat - size],
                                                [lon, lat - size], [lon, lat]]]}


def feature(id_, name, geometry, **props):
    return {"type": "Feature", "properties": dict(props, id=id_, name=name), "geometry": geometry}


class PrepareFarmsTest(unittest.TestCase):
    def setUp(self):
        self.grid = make_grid()

    def test_points_outlines_and_skips(self):
        features = [
            feature(1, "P", {"type": "Point", "coordinates": [103.1, -1.9]}),
            feature(2, "A", square(103.2, -2.0)),
            feature(3, "Line", {"type": "LineString", "coordinates": [[103.1, -1.9], [103.2, -1.9]]}),
            feature(4, "Empty", None),
            feature(5, "A", square(103.3, -2.1)),
        ]
        farms, keys, skipped = farms_mod.prepare_farms(features, self.grid)
        self.assertEqual([f.plot_id for f in farms], ["P", "A", "A-2"])
        self.assertEqual(keys, [1, 2, 5])
        self.assertEqual([f.kind for f in farms], ["point", "outline", "outline"])
        self.assertEqual(skipped, [("Line", "unsupported shape: LineString"),
                                   ("Empty", "unsupported shape: no geometry")])

    def test_a_bow_tie_is_repaired(self):
        bow = {"type": "Polygon", "coordinates": [[[103.1, -1.9], [103.11, -1.91], [103.11, -1.9],
                                                   [103.1, -1.91], [103.1, -1.9]]]}
        farms, _, skipped = farms_mod.prepare_farms([feature(1, "Bow", bow)], self.grid)
        self.assertEqual((len(farms), skipped), (1, []))
        self.assertTrue(farms[0].geom.is_valid)

    def test_circle_mode_turns_points_with_an_area_into_outlines(self):
        f = feature(1, "C", {"type": "Point", "coordinates": [103.1, -1.9]}, area_ha=4)
        farms, _, _ = farms_mod.prepare_farms([f], self.grid, point_mode="circle")
        self.assertEqual(farms[0].kind, "outline")
        self.assertAlmostEqual(farms_mod.farm_area_ha(farms[0], self.grid), 4, delta=0.05)

    def test_features_from_a_file(self):
        data = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "id": "f1", "properties": {"plot_id": "PLOT-7", "area_ha": 2},
             "geometry": {"type": "Point", "coordinates": [103.1, -1.9]}},
            {"type": "Feature", "properties": {}, "geometry": {"type": "GeometryCollection", "geometries": []}}]}
        kept, skipped = farms_mod.features_from_file(data)
        self.assertEqual(kept[0]["properties"], {"plot_id": "PLOT-7", "area_ha": 2, "name": "PLOT-7"})
        self.assertEqual(skipped, [("farm 2", "unsupported shape: GeometryCollection")])
        bare, _ = farms_mod.features_from_file(square(103.2, -2.0))
        self.assertEqual(len(bare), 1)
