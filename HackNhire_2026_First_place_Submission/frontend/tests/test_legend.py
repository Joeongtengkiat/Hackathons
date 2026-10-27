import json
import unittest

import legend
from backend import common


class LegendTest(unittest.TestCase):
    def test_class_codes_match_the_backend(self):
        self.assertEqual(legend.CLASSES, common.COLOURS)
        for code in legend.CLASSES:
            for table in (legend.CLASS_LABELS, legend.MAP_COLOURS, legend.CHART_COLOURS):
                self.assertIn(code, table)

    def test_chart_order_covers_every_class_once(self):
        self.assertEqual(sorted(legend.CHART_ORDER), list(legend.CLASSES))

    def test_verdicts_match_the_backend(self):
        self.assertEqual(set(legend.VERDICT_STYLE), set(common.VERDICTS))
        self.assertEqual(legend.VERDICT_ORDER, common.VERDICTS)

    def test_page_config_is_json_ready(self):
        config = json.loads(json.dumps(legend.page_config()))
        self.assertEqual(config["verdicts"]["FAIL"]["headline"], "Not compliant")
        self.assertEqual([c["code"] for c in config["classes"]], list(legend.CLASSES))
        self.assertEqual(config["methods"]["polygons"]["label"], "Outlines")

    def test_hex_to_rgb(self):
        self.assertEqual(legend.hex_to_rgb("#E31A1C"), (227, 26, 28))
