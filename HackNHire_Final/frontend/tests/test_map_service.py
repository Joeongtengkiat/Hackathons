import os
import shutil
import tempfile
import threading
import unittest
from unittest import mock

import numpy as np
import psycopg

import neon_service
from backend import common


class FakeConnection:
    def __init__(self):
        self.closed = False

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        self.closed = True


class UploadProgressTest(unittest.TestCase):
    def test_each_new_step_moves_the_bar_on(self):
        seen = []
        report = neon_service.UploadProgress(lambda done, total, text: seen.append((done, total, text)))
        report("reading the map", 12, 24)
        report("packing pages", 576, 576)
        report("building the index", 0, 1)
        self.assertEqual([done for done, _, _ in seen], [71, 285, 285])
        self.assertEqual(seen[0][2], "Step 1 of 7 · Stencils · reading the map · 50%")

    def test_cancel_stops_the_upload_at_the_next_report(self):
        cancel = threading.Event()
        report = neon_service.UploadProgress(lambda *args: None, cancel)
        report("reading the map", 1, 24)
        cancel.set()
        with self.assertRaises(neon_service.Cancelled):
            report("reading the map", 2, 24)


class CopyToTiffFolderTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tiff_dir = os.path.join(tmp.name, "tiff")
        self.src = os.path.join(tmp.name, "riau.tif")
        with open(self.src, "wb") as fh:
            fh.write(b"made-up map")
        patcher = mock.patch.object(neon_service.common, "TIFF_DIR", self.tiff_dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_copy_is_kept_for_the_tiff_comparison(self):
        self.assertIn("Copied", neon_service.copy_to_tiff_folder(self.src))
        self.assertTrue(os.path.isfile(os.path.join(self.tiff_dir, "riau.tif")))

    def test_a_different_file_with_that_name_is_never_overwritten(self):
        os.makedirs(self.tiff_dir)
        theirs = os.path.join(self.tiff_dir, "riau.tif")
        with open(theirs, "wb") as fh:
            fh.write(b"another map")
        self.assertIn("already has", neon_service.copy_to_tiff_folder(self.src))
        with open(theirs, "rb") as fh:
            self.assertEqual(fh.read(), b"another map")

    def test_a_tiff_already_in_the_folder_is_left_where_it_is(self):
        os.makedirs(self.tiff_dir)
        inside = shutil.copy(self.src, os.path.join(self.tiff_dir, "riau.tif"))
        self.assertIn("already in", neon_service.copy_to_tiff_folder(inside))

    def test_a_failed_copy_doesnt_fail_the_upload(self):
        """Final review: the map is already in Neon, so a failed copy is only a note."""
        with mock.patch.object(neon_service.shutil, "copy2", side_effect=PermissionError("locked")):
            note = neon_service.copy_to_tiff_folder(self.src)
        self.assertIn("Couldn't copy riau.tif", note)


class MapsServiceTest(unittest.TestCase):
    def test_uploads_are_not_retried_after_a_dropped_connection(self):
        made = []

        def connect():
            made.append(FakeConnection())
            return made[-1]

        service = neon_service.NeonService(connect=connect)
        with mock.patch.object(neon_service.maps, "upload", side_effect=psycopg.OperationalError("dropped")):
            with self.assertRaises(psycopg.OperationalError):
                service.upload_map("riau.tif", "riau", keep_copy=False)
        self.assertEqual(len(made), 1)

    def test_deleting_the_shown_map_forgets_it(self):
        service = neon_service.NeonService(connect=FakeConnection)
        service.grid = common.Grid("riau", 101.0, 0.5, 1e-4, 1e-4, 100, 80, 4326, True, schema="map_riau")
        service.classes = np.zeros((1, 1), np.uint8)
        with mock.patch.object(neon_service.maps, "delete", return_value={"name": "riau", "schema": "map_riau"}):
            self.assertEqual(service.delete_map("riau"), {"name": "riau", "schema": "map_riau"})
        self.assertIsNone(service.grid)
        self.assertIsNone(service.classes)

    def test_renaming_the_shown_map_renames_it_in_place(self):
        service = neon_service.NeonService(connect=FakeConnection)
        service.grid = common.Grid("riau", 101.0, 0.5, 1e-4, 1e-4, 100, 80, 4326, True, schema="map_riau")
        service.classes = np.zeros((80, 100), np.uint8)
        service.counts = np.array([8000, 0, 0, 0, 0, 0])
        renamed = {"old": "riau", "name": "Riau 2024", "schema": "map_riau_2024"}
        with mock.patch.object(neon_service.maps, "rename", return_value=dict(renamed)) as rename:
            info = service.rename_map("riau", "Riau 2024")
        rename.assert_called_once()
        self.assertEqual((service.grid.map_version, service.grid.schema), ("Riau 2024", "map_riau_2024"))
        self.assertEqual((info["map"]["map_version"], info["map"]["schema"]), ("Riau 2024", "map_riau_2024"))
        self.assertIsNotNone(service.classes)  # nothing is downloaded again

    def test_renaming_another_map_leaves_the_shown_one(self):
        service = neon_service.NeonService(connect=FakeConnection)
        service.grid = common.Grid("riau", 101.0, 0.5, 1e-4, 1e-4, 100, 80, 4326, True, schema="map_riau")
        renamed = {"old": "jambi-v1", "name": "Jambi", "schema": "map_jambi"}
        with mock.patch.object(neon_service.maps, "rename", return_value=dict(renamed)):
            info = service.rename_map("jambi-v1", "Jambi")
        self.assertIsNone(info["map"])
        self.assertEqual(service.grid.map_version, "riau")
