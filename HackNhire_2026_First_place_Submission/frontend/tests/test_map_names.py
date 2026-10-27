import contextlib
import dataclasses
import io
import os
import tempfile
import unittest

import numpy as np
import rasterio

from backend import common
from tests.helpers import Script, write_tiff


def catalogue(*maps):
    """A database whose public.maps lists these (name, schema) rows."""
    return Script([("'public.maps'", [(True,)]), ("ORDER BY lower(name)", list(maps))])


class MapSchemaTest(unittest.TestCase):
    def test_names_become_schemas(self):
        for name, schema in [("jambi-v1", "map_jambi_v1"), ("Jambi v1", "map_jambi_v1"),
                             ("  Riau 2024 ", "map_riau_2024"), ("Sumatra—South!", "map_sumatra_south")]:
            self.assertEqual(common.map_schema(name), schema)

    def test_the_longest_name_fits_a_postgres_identifier(self):
        self.assertLessEqual(len(common.map_schema("x" * common.MAP_NAME_MAX)), 63)
        with self.assertRaises(SystemExit):
            common.map_schema("x" * (common.MAP_NAME_MAX + 1))

    def test_unusable_names_say_why(self):
        for name in ("", "   ", "!!!", "名前", "a\tb"):
            with self.assertRaises(SystemExit) as cm:
                common.map_schema(name)
            self.assertTrue(str(cm.exception.code))


class GridTableTest(unittest.TestCase):
    GRID = common.Grid("riau", 101.0, 0.5, 1e-4, 1e-4, 100, 80, 4326, True, schema="map_riau")

    def test_tables_are_named_with_their_schema(self):
        self.assertEqual(self.GRID.table("class_polys"), "map_riau.class_polys")
        self.assertEqual(dataclasses.replace(self.GRID, schema="").table("class_polys"), "class_polys")

    def test_the_schema_does_not_change_which_map_a_grid_describes(self):
        self.assertEqual(self.GRID, dataclasses.replace(self.GRID, schema=""))


class FindMapTest(unittest.TestCase):
    def test_the_only_map_is_used_when_no_name_is_given(self):
        self.assertEqual(common.find_map(catalogue(("jambi-v1", "map_jambi_v1"))),
                         ("jambi-v1", "map_jambi_v1"))

    def test_several_maps_need_a_name(self):
        with self.assertRaises(SystemExit) as cm:
            common.find_map(catalogue(("jambi-v1", "map_jambi_v1"), ("riau", "map_riau")))
        self.assertIn("--map", str(cm.exception.code))

    def test_a_name_matches_exactly_or_by_its_schema(self):
        conn = catalogue(("jambi-v1", "map_jambi_v1"), ("riau", "map_riau"))
        self.assertEqual(common.find_map(conn, "riau"), ("riau", "map_riau"))
        self.assertEqual(common.find_map(conn, "Jambi V1"), ("jambi-v1", "map_jambi_v1"))

    def test_an_unknown_name_lists_the_maps(self):
        with self.assertRaises(SystemExit) as cm:
            common.find_map(catalogue(("jambi-v1", "map_jambi_v1")), "borneo")
        self.assertIn("jambi-v1", str(cm.exception.code))

    def test_the_old_one_map_layout_asks_for_migrate(self):
        conn = Script([("'public.maps'", [(False,)]), ("'public.grid_info'", [(True,)])])
        with self.assertRaises(SystemExit) as cm:
            common.find_map(conn)
        self.assertIn("maps.py migrate", str(cm.exception.code))

    def test_a_row_pointing_outside_a_map_schema_is_refused(self):
        with self.assertRaises(SystemExit):
            common.find_map(catalogue(("odd", "public")), "odd")

    def test_load_grid_reads_the_maps_own_grid_info(self):
        conn = catalogue(("riau", "map_riau"))
        conn.answers.append(("FROM map_riau.grid_info",
                             [("riau", 101.0, 0.5, 1e-4, 1e-4, 100, 80, 4326, True, 2, "fp")]))
        grid = common.load_grid(conn, "riau")
        self.assertEqual((grid.schema, grid.table("stencil_pages"), grid.default_class),
                         ("map_riau", "map_riau.stencil_pages", 2))


class StartBuildTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        data = np.zeros((40, 50), np.uint8)
        data[5:15, 10:30] = common.FOREST
        data[20:25, 5:9] = common.DEFORESTATION
        cls.tif = write_tiff(os.path.join(cls.tmp.name, "small.tif"), data)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def build(self, conn, name="Small map"):
        with rasterio.open(self.tif) as src, contextlib.redirect_stdout(io.StringIO()):
            return common.start_build(conn, src, self.tif, name, {})

    def test_a_new_map_gets_its_own_schema_and_a_catalogue_row(self):
        conn = Script()
        grid, counts = self.build(conn)
        sql = "\n".join(conn.sql)
        self.assertEqual(grid.schema, "map_small_map")
        self.assertIn("CREATE SCHEMA IF NOT EXISTS map_small_map", sql)
        self.assertIn("INSERT INTO map_small_map.grid_info", sql)
        self.assertIn("INSERT INTO public.maps", sql)
        self.assertEqual(int(counts[common.FOREST]), 200)

    def test_a_name_too_close_to_another_maps_is_refused(self):
        conn = Script([("WHERE schema_name = %s", [("small-map",)])])
        with self.assertRaises(SystemExit) as cm:
            self.build(conn)
        self.assertIn("too close to the map 'small-map'", str(cm.exception.code))
        self.assertFalse(any("CREATE SCHEMA" in s for s in conn.sql))

    def test_the_same_tiff_under_another_name_is_refused(self):
        conn = Script([("WHERE fingerprint = %s", [("jambi-v1",)])])
        with self.assertRaises(SystemExit) as cm:
            self.build(conn)
        self.assertIn("already stored as 'jambi-v1'", str(cm.exception.code))


class ProgressTest(unittest.TestCase):
    def test_every_step_is_reported(self):
        seen = []
        progress = common.Progress(3, "packing pages", lambda *args: seen.append(args))
        for _ in range(3):
            progress.step()
        self.assertEqual(seen, [("packing pages", 1, 3), ("packing pages", 2, 3), ("packing pages", 3, 3)])

    def test_a_report_can_stop_the_job(self):
        def stop(label, done, total):
            raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            common.Progress(2, "reading the map", stop).step()
