import unittest

import map_area


class OverlayPositionTest(unittest.TestCase):
    def test_corners(self):
        area, size = (1000, 600), (200, 100)
        self.assertEqual(map_area.overlay_position("top-left", (12, 12), area, size), (12, 12))
        self.assertEqual(map_area.overlay_position("top-center", (0, 12), area, size), (400, 12))
        self.assertEqual(map_area.overlay_position("top-right", (12, 12), area, size), (788, 12))
        self.assertEqual(map_area.overlay_position("bottom-left", (12, 36), area, size), (12, 464))
        self.assertEqual(map_area.overlay_position("bottom-right", (12, 12), area, size), (788, 488))

    def test_never_placed_off_the_top_or_left(self):
        self.assertEqual(map_area.overlay_position("bottom-right", (12, 12), (100, 50), (200, 100)), (0, 0))
