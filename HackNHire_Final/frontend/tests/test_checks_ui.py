import unittest

import checks_ui


def point(id_, lon, lat):
    return {"type": "Feature", "properties": {"id": id_, "name": f"farm {id_}"},
            "geometry": {"type": "Point", "coordinates": [lon, lat]}}


class StaleTest(unittest.TestCase):
    def test_edited_and_deleted_farms(self):
        """Review focus 3: an edited farm's result is out of date; a deleted farm's result goes."""
        checked = {1: checks_ui.geometry_key(point(1, 103.1, -1.9)),
                   2: checks_ui.geometry_key(point(2, 103.2, -1.9))}
        stale, deleted = checks_ui.stale_keys(checked, [point(1, 103.1, -1.95)])
        self.assertEqual((stale, deleted), ({1}, {2}))

    def test_untouched_and_renamed_farms_stay_current(self):
        checked = {1: checks_ui.geometry_key(point(1, 103.1, -1.9))}
        renamed = point(1, 103.1, -1.9)
        renamed["properties"]["name"] = "new name"
        self.assertEqual(checks_ui.stale_keys(checked, [renamed]), (set(), set()))

    def test_rows_sort_worst_first(self):
        rows = [{"verdict": "PASS", "name": "a"}, {"verdict": "FAIL", "name": "b"},
                {"verdict": "MANUAL_REVIEW", "name": "c"}, {"verdict": "FAIL", "name": "A"}]
        self.assertEqual([r["name"] for r in checks_ui.sort_rows(rows)], ["A", "b", "c", "a"])
