import unittest

from backend import common, polygon_approach, stencil_approach
from tests.helpers import Script


class QualifiedTablesTest(unittest.TestCase):
    """Every query of both approaches names the map's own schema."""

    GRID = common.Grid("riau", 101.0, 0.5, 1 / 8192, 1 / 8192, 600, 500, 4326, True,
                       default_class=0, fingerprint="f", schema="map_riau")

    def farms(self, n):
        return common.farms_from_points([(101.001 + 0.0002 * i, 0.49) for i in range(n)], self.GRID)

    def test_stencils_read_the_maps_own_pages(self):
        conn = Script([("to_regclass", [(True,)])])
        results, _ = stencil_approach.check(conn, self.GRID, self.farms(3))
        self.assertEqual(len(results), 3)
        self.assertIn(("map_riau.stencil_pages",), conn.params)
        self.assertTrue(any("JOIN map_riau.stencil_pages" in s for s in conn.sql))

    def test_outlines_and_labels_come_from_the_maps_own_tables(self):
        conn = Script([("to_regclass", [(True,)])])
        polygon_approach.check(conn, self.GRID, self.farms(polygon_approach.LABELS_FROM))
        sql = "\n".join(conn.sql)
        self.assertIn("JOIN map_riau.square_labels", sql)
        self.assertIn("JOIN map_riau.class_polys", sql)
        self.assertIn(("map_riau.class_polys",), conn.params)
