import unittest
from types import SimpleNamespace

import eudr_layer
from tests.qt_app import qt_app

INFO = {"map_version": "jambi-v1", "width": 6000, "height": 6000,
        "classes": [{"code": 0, "label": "Land", "colour": "#E1E1E1", "share": 64.93},
                    {"code": 1, "label": "Water", "colour": "#6BAED6", "share": 0.097},
                    {"code": 3, "label": "Deforestation", "colour": "#E31A1C", "share": 2.26}]}


class ShareTextTest(unittest.TestCase):
    def test_share_text(self):
        self.assertEqual([eudr_layer.share_text(p) for p in (64.93, 2.26, 0.097, 0.0)],
                         ["65%", "2.3%", "<0.1%", "<0.1%"])


class LegendTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        qt_app()

    def test_unticking_a_class_hides_it(self):
        legend = eudr_layer.Legend()
        legend.set_info(INFO)
        seen = []
        legend.hidden_changed.connect(seen.append)
        boxes = legend.checkboxes
        self.assertEqual([b.text() for b in boxes], ["Land", "Water", "Deforestation"])
        boxes[0].setChecked(False)
        boxes[2].setChecked(False)
        boxes[0].setChecked(True)
        self.assertEqual(seen, [[0], [0, 3], [3]])

    def test_banner_buttons(self):
        banner = eudr_layer.Banner()
        banner.show_message("Loading the map 'jambi-v1' from Neon…")
        self.assertTrue(banner.button.isHidden())
        banner.show_message("Could not load the map from Neon. offline", error=True, button="Retry")
        self.assertFalse(banner.button.isHidden())
        self.assertEqual((banner.button.text(), banner.property("tone")), ("Retry", "error"))
        banner.show_message("No maps in Neon yet.", button="Upload map…")
        self.assertEqual((banner.button.text(), banner.property("tone")), ("Upload map…", "info"))


class StaleLoadTest(unittest.TestCase):
    """Review focus: after switching maps quickly, only the map chosen last may be shown."""

    def test_a_slower_load_of_an_earlier_choice_is_ignored(self):
        layer = SimpleNamespace(name="riau", w=None)  # touching the window would fail
        self.assertIsNone(eudr_layer.EudrLayer.loaded(layer, {"map_version": "jambi-v1"}))

    def test_a_late_failure_of_an_earlier_choice_is_ignored(self):
        layer = SimpleNamespace(name="riau", w=None)
        self.assertTrue(eudr_layer.EudrLayer.failed(layer, "jambi-v1", "offline"))
