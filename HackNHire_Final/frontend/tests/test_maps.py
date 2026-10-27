import contextlib
import io
import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

import numpy as np

from backend import common, maps
from tests.helpers import Script, write_tiff


class ListMapsTest(unittest.TestCase):
    def test_maps_are_listed_from_the_catalogue(self):
        added = datetime(2026, 10, 15, 9, 30, tzinfo=timezone.utc)
        conn = Script([("'public.maps'", [(True,)]),
                       ("FROM public.maps m", [("jambi-v1", "map_jambi_v1", "my_map.tif", 6000, 6000,
                                                added, True, True)])])
        self.assertEqual(maps.list_maps(conn), [{
            "name": "jambi-v1", "schema": "map_jambi_v1", "source_file": "my_map.tif", "width": 6000,
            "height": 6000, "added_at": added.isoformat(), "stencils": True, "outlines": True}])

    def test_no_catalogue_means_no_maps_yet(self):
        conn = Script([("'public.maps'", [(False,)]), ("'public.grid_info'", [(False,)])])
        self.assertEqual(maps.list_maps(conn), [])

    def test_the_old_layout_must_be_moved_first(self):
        conn = Script([("'public.maps'", [(False,)]), ("'public.grid_info'", [(True,)])])
        with self.assertRaises(SystemExit) as cm:
            maps.list_maps(conn)
        self.assertIn("migrate", str(cm.exception.code))


class UploadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.tif = write_tiff(os.path.join(cls.tmp.name, "small.tif"), np.zeros((40, 50), np.uint8))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.calls, self.fail_outlines = [], None
        grid = common.Grid("Small", 110.0, -7.0, 1 / 12000, 1 / 12000, 50, 40, 4326, True, schema="map_small")

        def builder(kind):
            def build(conn, tif, name, report=None, tidy=True):
                self.calls.append((kind, name, tidy))
                if kind == "outlines" and self.fail_outlines:
                    raise self.fail_outlines
                return grid
            return build

        for target, attr, new in ((maps.stencil_approach, "build", builder("stencils")),
                                  (maps.polygon_approach, "build", builder("outlines")),
                                  (maps.common, "tidy_up", lambda conn, table: self.calls.append(("tidy", table))),
                                  (maps.common, "print_storage", lambda conn, tables: None)):
            patcher = mock.patch.object(target, attr, new)
            patcher.start()
            self.addCleanup(patcher.stop)

    def upload(self, conn, name="Small"):
        with contextlib.redirect_stdout(io.StringIO()):
            return maps.upload(conn, self.tif, name)

    def test_both_approaches_are_built_in_one_transaction_then_tidied(self):
        conn = Script([("'public.maps'", [(True,)])])
        self.upload(conn)
        self.assertEqual(self.calls, [("stencils", "Small", False), ("outlines", "Small", False),
                                      ("tidy", "map_small.stencil_pages"), ("tidy", "map_small.class_polys"),
                                      ("tidy", "map_small.square_labels")])
        self.assertEqual((conn.commits, conn.rollbacks), (1, 0))

    def test_a_failure_rolls_everything_back(self):
        self.fail_outlines = RuntimeError("the connection to Neon dropped")
        conn = Script([("'public.maps'", [(True,)])])
        with self.assertRaises(RuntimeError):
            self.upload(conn)
        self.assertEqual((conn.commits, conn.rollbacks), (0, 1))
        self.assertNotIn("tidy", [c[0] for c in self.calls])

    def test_a_taken_name_is_refused_before_anything_is_built(self):
        conn = Script([("'public.maps'", [(True,)]), ("SELECT 1 FROM public.maps", [(1,)])])
        with self.assertRaises(SystemExit) as cm:
            self.upload(conn)
        self.assertIn("already a map called 'Small'", str(cm.exception.code))
        self.assertEqual(self.calls, [])

    def test_uploads_wait_until_the_old_layout_is_moved(self):
        conn = Script([("'public.maps'", [(False,)]), ("'public.grid_info'", [(True,)])])
        with self.assertRaises(SystemExit) as cm:
            self.upload(conn)
        self.assertIn("migrate", str(cm.exception.code))

    def test_maps_too_big_for_the_app_are_refused(self):
        with mock.patch.object(maps, "MAX_PIXELS", 100), self.assertRaises(SystemExit) as cm:
            self.upload(Script([("'public.maps'", [(True,)])]))
        self.assertIn("Crop it", str(cm.exception.code))
        self.assertEqual(self.calls, [])

    def test_a_cancel_after_the_commit_cannot_undo_the_upload(self):
        """Final review: Cancel noticed during tidy-up must not report 'nothing was saved'."""
        def report(label, done, total):
            if label == "tidying up":
                raise RuntimeError("Cancelled.")
        conn = Script([("'public.maps'", [(True,)])])
        with contextlib.redirect_stdout(io.StringIO()):
            grid = maps.upload(conn, self.tif, "Small", report)
        self.assertEqual(grid.schema, "map_small")
        self.assertEqual((conn.commits, conn.rollbacks), (1, 0))
        self.assertEqual([c for c in self.calls if c[0] == "tidy"], [
            ("tidy", "map_small.stencil_pages"), ("tidy", "map_small.class_polys"),
            ("tidy", "map_small.square_labels")])


class LastCheckpointTest(unittest.TestCase):
    """Final review: each build's last progress report comes before the commit, so a Cancel pressed
    while the indexes are built still rolls the whole upload back."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        data = np.zeros((40, 50), np.uint8)
        data[5:15, 10:30] = common.FOREST
        cls.tif = write_tiff(os.path.join(cls.tmp.name, "small.tif"), data)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def last_report(self, module):
        seen = []
        with contextlib.redirect_stdout(io.StringIO()):
            module.build(Script(), self.tif, "Small", lambda *args: seen.append(args), tidy=False)
        return seen[-1]

    def test_the_stencil_build_reports_its_index_as_done(self):
        self.assertEqual(self.last_report(maps.stencil_approach), ("building the index", 1, 1))

    def test_the_outline_build_reports_its_indexes_as_done(self):
        self.assertEqual(self.last_report(maps.polygon_approach), ("building the indexes", 1, 1))


class DeleteTest(unittest.TestCase):
    def conn(self):
        return Script([("'public.maps'", [(True,)]), ("ORDER BY lower(name)", [("riau", "map_riau")])])

    def test_delete_drops_the_maps_schema_and_its_catalogue_row(self):
        conn = self.conn()
        self.assertEqual(maps.delete(conn, "riau"), {"name": "riau", "schema": "map_riau"})
        self.assertIn("DROP SCHEMA IF EXISTS map_riau CASCADE", conn.sql)
        self.assertIn("DELETE FROM public.maps WHERE name = %s", conn.sql)
        self.assertEqual(conn.commits, 1)

    def test_delete_needs_the_exact_name(self):
        conn = self.conn()
        with self.assertRaises(SystemExit):
            maps.delete(conn, "Riau")
        self.assertFalse(any("DROP SCHEMA" in s for s in conn.sql))

    def test_describe_says_what_would_go(self):
        conn = self.conn()
        conn.answers += [("pg_total_relation_size", [(34_200_000,)]), ("'map_riau.plot_results'", [(True,)]),
                         ("count(*) FROM map_riau.plot_results", [(12,)]),
                         ("source_file, width, height FROM public.maps", [("riau.tif", 700, 600)])]
        self.assertEqual(maps.describe(conn, "riau"), {
            "name": "riau", "schema": "map_riau", "bytes": 34_200_000, "saved_results": 12,
            "source_file": "riau.tif", "width": 700, "height": 600})


class RenameTest(unittest.TestCase):
    def conn(self, schema_taken=False):
        return Script([("'public.maps'", [(True,)]),
                       ("ORDER BY lower(name)", [("jambi-v1", "map_jambi_v1"), ("riau", "map_riau")]),
                       ("FROM public.maps WHERE name <> %s", [("jambi-v1", "map_jambi_v1")]),
                       ("to_regnamespace", [(schema_taken,)]),
                       (".plot_results'", [(True,)])])

    def test_rename_moves_the_schema_and_updates_every_name(self):
        conn = self.conn()
        self.assertEqual(maps.rename(conn, "riau", "  Riau 2024 "),
                         {"old": "riau", "name": "Riau 2024", "schema": "map_riau_2024"})
        sql = "\n".join(conn.sql)
        self.assertIn("ALTER SCHEMA map_riau RENAME TO map_riau_2024", sql)
        self.assertIn("UPDATE map_riau_2024.grid_info SET map_version = %s", sql)
        self.assertIn("UPDATE map_riau_2024.plot_results SET map_version = %s", sql)
        self.assertIn("UPDATE public.maps SET name = %s, schema_name = %s WHERE name = %s", sql)
        self.assertIn(("Riau 2024", "map_riau_2024", "riau"), conn.params)
        self.assertEqual(conn.commits, 1)

    def test_a_change_of_case_keeps_the_schema(self):
        conn = self.conn()
        self.assertEqual(maps.rename(conn, "riau", "Riau")["schema"], "map_riau")
        self.assertFalse(any("ALTER SCHEMA" in s for s in conn.sql))
        self.assertIn(("Riau", "map_riau", "riau"), conn.params)

    def test_names_that_clash_are_refused_before_anything_changes(self):
        for new, why in (("jambi-v1", "already a map called"), ("JAMBI v1", "too close"),
                         ("riau", "already called"), ("", "needs a name")):
            conn = self.conn()
            with self.assertRaises(SystemExit) as cm:
                maps.rename(conn, "riau", new)
            self.assertIn(why, str(cm.exception.code))
            self.assertFalse(any(s.startswith(("ALTER", "UPDATE")) for s in conn.sql))
            self.assertEqual(conn.commits, 0)

    def test_a_stray_schema_with_the_new_name_is_refused(self):
        conn = self.conn(schema_taken=True)
        with self.assertRaises(SystemExit) as cm:
            maps.rename(conn, "riau", "Riau 2024")
        self.assertIn("map_riau_2024 already exists", str(cm.exception.code))


class MigrateTest(unittest.TestCase):
    def test_the_old_tables_move_into_the_maps_schema(self):
        conn = Script([("'public.", [(True,)]), ("to_regnamespace", [(False,)]),
                       ("FROM public.grid_info", [("jambi-v1", "fp", "my_map.tif", 6000, 6000)])])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(maps.migrate(conn), "jambi-v1")
        sql = "\n".join(conn.sql)
        for table in maps.MAP_TABLES:
            self.assertIn(f"ALTER TABLE public.{table} SET SCHEMA map_jambi_v1", sql)
        self.assertIn("INSERT INTO public.maps", sql)
        self.assertEqual(conn.commits, 1)

    def test_nothing_to_move(self):
        with self.assertRaises(SystemExit) as cm:
            maps.migrate(Script([("'public.grid_info'", [(False,)])]))
        self.assertIn("Nothing to move", str(cm.exception.code))
