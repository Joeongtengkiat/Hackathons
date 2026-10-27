import unittest
from unittest import mock

import neon_service
from backend import common


class FakeConnection:
    closed = False

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def make_service():
    service = neon_service.NeonService(connect=FakeConnection)
    service.grid = common.Grid("test", 103.0, -1.8, 1 / 12000, 1 / 12000, 6000, 6000, 4326, True,
                               default_class=0, fingerprint="test")
    return service


def forest_everywhere(conn, grid, farms, *args):
    return [common.point_result(f, common.FOREST) for f in farms], {"total_s": 0.01}


class CheckTest(unittest.TestCase):
    def test_rows_follow_the_map_ids_and_skips_are_reported(self):
        features = [
            {"type": "Feature", "properties": {"id": 7, "name": "P"},
             "geometry": {"type": "Point", "coordinates": [103.1, -1.9]}},
            {"type": "Feature", "properties": {"id": 9, "name": "L"},
             "geometry": {"type": "LineString", "coordinates": [[103.1, -1.9], [103.2, -1.9]]}},
        ]
        with mock.patch.object(neon_service.stencil_approach, "check", forest_everywhere), \
                mock.patch.object(neon_service.polygon_approach, "check", forest_everywhere):
            out = make_service().check(features)
        self.assertEqual([r["key"] for r in out["rows"]], [7])
        self.assertEqual(out["rows"][0]["verdict"], "PASS")
        self.assertEqual(out["skipped"], [{"name": "L", "reason": "unsupported shape: LineString"}])
        self.assertFalse(out["options"]["tiff"])
        self.assertEqual(out["summary"]["verdicts"]["stencils"]["PASS"], 1)
        self.assertEqual(out["map"], "test")

    def test_points_and_outlines_are_metered_as_separate_runs(self):
        features = [
            {"type": "Feature", "properties": {"id": 1, "name": "P1"},
             "geometry": {"type": "Point", "coordinates": [103.1, -1.9]}},
            {"type": "Feature", "properties": {"id": 2, "name": "O1"},
             "geometry": {"type": "Polygon", "coordinates": [[[103.1, -1.9], [103.101, -1.9],
                                                              [103.101, -1.901], [103.1, -1.9]]]}},
            {"type": "Feature", "properties": {"id": 3, "name": "P2"},
             "geometry": {"type": "Point", "coordinates": [103.2, -1.95]}},
        ]
        batches = []

        def recording(name):
            def check(conn, grid, farms, *args):
                batches.append((name, [f.plot_id for f in farms], type(conn).__name__))
                return forest_everywhere(conn, grid, farms)
            return check

        with mock.patch.object(neon_service.stencil_approach, "check", recording("stencils")), \
                mock.patch.object(neon_service.polygon_approach, "check", recording("polygons")):
            out = make_service().check(features)
        self.assertEqual([b[:2] for b in batches], [("stencils", ["P1", "P2"]), ("stencils", ["O1"]),
                                                    ("polygons", ["P1", "P2"]), ("polygons", ["O1"])])
        self.assertTrue(all(b[2] == "MeteredConnection" for b in batches))
        self.assertEqual(out["runs"]["stencils"]["point"]["count"], 2)
        self.assertEqual(out["runs"]["polygons"]["outline"]["count"], 1)
        self.assertEqual(out["runs"]["polygons"]["outline"]["round_trips"], 0)
        self.assertEqual([r["key"] for r in out["rows"]], [1, 2, 3])

    def test_nothing_to_check(self):
        out = make_service().check([])
        self.assertEqual((out["rows"], out["summary"]["n"]), ([], 0))
