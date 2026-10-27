import os
import tempfile
import unittest
from unittest import mock

import numpy as np
from PyQt5.QtCore import QSettings
from PyQt5.QtWidgets import QDialog

import compare_tab
import maps_ui
from tests.helpers import FakeWindow, write_tiff
from tests.qt_app import qt_app

MAPS = [{"name": "jambi-v1", "schema": "map_jambi_v1", "source_file": "my_map.tif", "width": 6000,
         "height": 6000, "added_at": None, "stencils": True, "outlines": True},
        {"name": "riau", "schema": "map_riau", "source_file": "riau.tif", "width": 700, "height": 600,
         "added_at": None, "stencils": True, "outlines": False}]


class HelpersTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.tif = write_tiff(os.path.join(cls.tmp.name, "riau_2024.tif"), np.zeros((40, 50), np.uint8))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_name_problems(self):
        existing = [("jambi-v1", "map_jambi_v1")]
        self.assertEqual(maps_ui.name_problem("Riau 2024", existing), "")
        self.assertIn("already a map called", maps_ui.name_problem("jambi-v1", existing))
        self.assertIn("too close to 'jambi-v1'", maps_ui.name_problem("JAMBI v1", existing))
        self.assertIn("letter", maps_ui.name_problem("名前", existing))

    def test_rename_problems(self):
        existing = [("jambi-v1", "map_jambi_v1"), ("riau", "map_riau")]
        self.assertEqual(maps_ui.rename_problem("Riau 2024", "riau", existing), "")
        self.assertEqual(maps_ui.rename_problem("Riau", "riau", existing), "")   # its own schema is fine
        self.assertIn("already called", maps_ui.rename_problem(" riau ", "riau", existing))
        self.assertIn("too close to 'jambi-v1'", maps_ui.rename_problem("JAMBI v1", "riau", existing))

    def test_choose_map(self):
        self.assertEqual(maps_ui.choose_map(["a", "b"], None, "x", "b"), "b")
        self.assertEqual(maps_ui.choose_map(["a", "b"], None, "", ""), "a")
        self.assertIsNone(maps_ui.choose_map([], "a"))

    def test_texts(self):
        self.assertEqual(maps_ui.map_label(MAPS[1]), "riau  (no outlines)")
        self.assertEqual(maps_ui.map_tooltip(MAPS[0]), "my_map.tif · 6,000 × 6,000 px · stored as map_jambi_v1")
        self.assertEqual(maps_ui.describe_text({"bytes": 34_200_000, "saved_results": 1, "source_file": "my_map.tif"}),
                         "Uses 34.2 MB in Neon · 1 saved result · from my_map.tif")
        self.assertTrue(maps_ui.confirmed(" riau ", "riau"))
        self.assertFalse(maps_ui.confirmed("Riau", "riau"))
        self.assertEqual(maps_ui.upload_error_text("Cancelled."), "Cancelled. Nothing was saved to Neon.")
        self.assertIn("0.5 GB", maps_ui.upload_error_text(
            "could not extend file because project size limit (512 MB) has been exceeded"))

    def test_tiff_summary_for_a_map_in_metres(self):
        tif = write_tiff(os.path.join(self.tmp.name, "utm.tif"), np.zeros((30, 40), np.uint8),
                         x0=300000, y0=9800000, pixel=30, crs="EPSG:32748")
        text, problem = maps_ui.tiff_summary(tif)
        self.assertIn("40 × 30 px · EPSG:32748", text)
        self.assertEqual(problem, "")

    def test_tiff_summary_flags_maps_too_big_for_memory(self):
        with mock.patch.object(maps_ui.maps, "MAX_PIXELS", 100):
            _, problem = maps_ui.tiff_summary(self.tif)
        self.assertIn("Crop it", problem)
        self.assertEqual(maps_ui.tiff_summary(os.path.join(self.tmp.name, "missing.tif"))[0], "")


class DialogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        qt_app()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.tif = write_tiff(os.path.join(cls.tmp.name, "riau_2024.tif"), np.zeros((40, 50), np.uint8))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_the_name_follows_the_file_and_upload_waits_for_both(self):
        window = FakeWindow()  # keep a reference: the dialog is its child
        dialog = maps_ui.UploadDialog(window, [("jambi-v1", "map_jambi_v1")])
        self.assertFalse(dialog.upload_btn.isEnabled())
        dialog.path.setText(self.tif)
        self.assertEqual(dialog.name.text(), "riau_2024")
        self.assertIn("50 × 40 px", dialog.info.text())
        self.assertTrue(dialog.upload_btn.isEnabled())
        dialog.name.setText("Jambi V1")
        self.assertIn("too close to 'jambi-v1'", dialog.problem.text())
        self.assertFalse(dialog.upload_btn.isEnabled())

    def test_cancel_before_the_upload_begins_is_passed_on(self):
        window = FakeWindow()
        dialog = maps_ui.UploadDialog(window, [])
        dialog.path.setText(self.tif)
        dialog.start()
        self.assertEqual(window.jobs[-1][1], "upload_map")
        dialog.reject()                         # pressed before the worker began the upload
        self.assertEqual(window.cancelled, 0)
        dialog.progress(10, 1000, "Step 1 of 7 · Stencils · reading the map · 7%")
        self.assertEqual(window.cancelled, 1)   # passed on as soon as it is running
        dialog.failed("Cancelled.")
        self.assertEqual(dialog.problem.text(), "Cancelled. Nothing was saved to Neon.")

    def test_delete_needs_the_exact_name(self):
        window = FakeWindow()
        dialog = maps_ui.DeleteDialog(window, ["jambi-v1", "riau"], current="riau")
        self.assertEqual(window.jobs[-1][1:3], ("describe_map", ("riau",)))
        self.assertFalse(dialog.delete_btn.isEnabled())
        dialog.typed.setText("Riau")
        self.assertFalse(dialog.delete_btn.isEnabled())
        dialog.typed.setText("riau")
        self.assertTrue(dialog.delete_btn.isEnabled())
        dialog.combo.setCurrentText("jambi-v1")  # another map: type its name again
        self.assertEqual(dialog.typed.text(), "")
        self.assertFalse(dialog.delete_btn.isEnabled())


    def test_rename_starts_from_the_current_name_and_waits_for_a_new_one(self):
        window = FakeWindow()
        dialog = maps_ui.RenameDialog(window, [(m["name"], m["schema"]) for m in MAPS], current="riau")
        self.assertEqual(dialog.name.text(), "riau")
        self.assertFalse(dialog.rename_btn.isEnabled())
        dialog.name.setText("jambi-v1")
        self.assertFalse(dialog.rename_btn.isEnabled())
        self.assertIn("already a map called", dialog.problem.text())
        dialog.name.setText("Riau 2024")
        self.assertTrue(dialog.rename_btn.isEnabled())
        dialog.start()
        self.assertEqual(window.jobs[-1][1:3], ("rename_map", ("riau", "Riau 2024")))
        dialog.combo.setCurrentText("jambi-v1")   # another map: its own name is filled in
        self.assertEqual(dialog.name.text(), "jambi-v1")

class ControllerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.settings = QSettings(os.path.join(tmp.name, "settings.ini"), QSettings.IniFormat)
        self.window = FakeWindow()

    def controller(self, preferred=None):
        controller = maps_ui.MapsController(self.window, preferred=preferred, settings=self.settings)
        self.assertEqual(self.window.jobs[-1][1], "list_maps")
        return controller

    def test_start_up_shows_the_last_map_used(self):
        self.settings.setValue("map", "riau")
        controller = self.controller()
        controller.listed(MAPS)
        self.assertEqual(self.window.eudr.calls[-1], ("load", "riau"))
        self.assertEqual(controller.combo.currentData(), "riau")

    def test_the_map_flag_wins_at_start_up(self):
        self.settings.setValue("map", "riau")
        self.controller(preferred="jambi-v1").listed(MAPS)
        self.assertEqual(self.window.eudr.calls[-1], ("load", "jambi-v1"))

    def test_no_maps_offers_an_upload(self):
        controller = self.controller()
        controller.listed([])
        kind, _, button = self.window.eudr.calls[-1]
        self.assertEqual((kind, button), ("clear", "Upload map…"))
        self.assertFalse(controller.delete_act.isEnabled())

    def test_switching_keeps_each_maps_farms(self):
        controller = self.controller()
        controller.listed(MAPS)                          # shows jambi-v1, the first
        controller.on_loaded({"map_version": "jambi-v1"})
        farm = {"type": "Feature", "properties": {"id": 1, "name": "A"},
                "geometry": {"type": "Point", "coordinates": [103.1, -1.9]}}
        self.window.geojson = {"type": "FeatureCollection", "features": [farm]}
        controller.switch_to("riau")
        self.assertIn(("map_switching", "jambi-v1", "riau"), self.window.events)
        self.assertIn("clearAll()", self.window.js_calls)
        self.window.geojson = {"type": "FeatureCollection", "features": []}
        controller.on_loaded({"map_version": "riau"})
        controller.switch_to("jambi-v1")
        controller.on_loaded({"map_version": "jambi-v1"})
        self.assertEqual(self.window.map_calls[-1],
                         ("addFeatures", {"type": "FeatureCollection", "features": [farm]}))
        self.assertEqual(self.settings.value("map"), "jambi-v1")

    def test_a_late_load_of_an_earlier_choice_is_ignored(self):
        controller = self.controller()
        controller.listed(MAPS)
        controller.switch_to("riau")
        controller.on_loaded({"map_version": "jambi-v1"})   # the earlier load, finishing late
        self.assertEqual(self.settings.value("map", ""), "")

    def test_deleting_the_shown_map_moves_on(self):
        controller = self.controller()
        controller.listed(MAPS)
        controller.on_loaded({"map_version": "jambi-v1"})
        with mock.patch.object(maps_ui, "DeleteDialog") as dialog_class:
            dialog = dialog_class.return_value
            dialog.exec_.return_value = QDialog.Accepted
            dialog.deleted = {"name": "jambi-v1", "schema": "map_jambi_v1"}
            controller.delete()
        self.assertIsNone(controller.current)
        self.assertIn(("map_switching", "jambi-v1", None), self.window.events)
        self.assertEqual(self.window.jobs[-1][1], "list_maps")  # lists again, then shows another map


    def test_renaming_the_shown_map_keeps_it_on_screen(self):
        controller = self.controller()
        controller.listed(MAPS)
        controller.on_loaded({"map_version": "jambi-v1"})
        controller.farms["riau"] = ["farm"]
        info = {"map_version": "Jambi", "schema": "map_jambi"}
        with mock.patch.object(maps_ui, "RenameDialog") as dialog_class:
            dialog = dialog_class.return_value
            dialog.exec_.return_value = QDialog.Accepted
            dialog.renamed = {"old": "jambi-v1", "name": "Jambi", "schema": "map_jambi", "map": info}
            controller.rename()
        self.assertEqual(controller.current, "Jambi")
        self.assertEqual(self.window.eudr.calls[-1], ("renamed", info))
        self.assertFalse(any(e[0] == "map_switching" for e in self.window.events[1:]))
        self.assertEqual(self.settings.value("map"), "Jambi")
        self.assertEqual(self.window.jobs[-1][1], "list_maps")

    def test_renaming_another_map_moves_its_farms(self):
        controller = self.controller()
        controller.listed(MAPS)
        controller.farms["riau"] = ["farm"]
        with mock.patch.object(maps_ui, "RenameDialog") as dialog_class:
            dialog = dialog_class.return_value
            dialog.exec_.return_value = QDialog.Accepted
            dialog.renamed = {"old": "riau", "name": "Riau 2024", "schema": "map_riau_2024", "map": None}
            controller.rename()
        self.assertEqual(controller.farms, {"Riau 2024": ["farm"]})
        self.assertEqual(controller.current, "jambi-v1")

class CompareResetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        qt_app()

    def test_switching_maps_clears_and_stops_the_benchmark(self):
        window = FakeWindow()
        tab = compare_tab.CompareTab(window)
        tab.job, tab.result = 5, {"map": "jambi-v1"}
        story = tab.sections["outline"][0]
        story.setText("Outlines faster for 100 to 1,000 farms.")
        window.emit("map_switching", "jambi-v1", "riau")
        self.assertEqual(window.cancelled, 1)
        self.assertIsNone(tab.result)
        self.assertEqual(story.text(), "Run the benchmark to compare the approaches.")
