import unittest

import compare_tab


def stats(n, seconds, nbytes=100, server=None):
    return {"n": n, "seconds": seconds, "bytes": nbytes, "round_trips": 2, "rows": n, "client_s": seconds / 2,
            "server": None if server is None else {"server_exec_ms": server, "shared_hit": 1, "shared_read": 0}}


RESULT = {
    "sizes": [1, 10, 100, 1000], "repeat": 3,
    "series": {
        "stencils": {"outline": [stats(1, 0.3), stats(10, 0.4), stats(100, 1.8), stats(1000, 5.0)],
                     "point": [stats(1, 0.2), stats(10, 0.2), stats(100, 0.7), stats(1000, 0.8)]},
        "polygons": {"outline": [stats(1, 0.45, server=0.6), stats(10, 0.5, server=2.0),
                                 stats(100, 0.8, server=12.0), stats(1000, 1.9, server=147.0)],
                     "point": [stats(1, 0.5), stats(10, 0.5), stats(100, 0.6), stats(1000, 1.1)]},
    },
    "agreement": {"outline": [{"n": 1, "same": 1, "of": 1}, {"n": 1000, "same": 999, "of": 1000}],
                  "point": [{"n": 1000, "same": 1000, "of": 1000}]},
}


class CompareTest(unittest.TestCase):
    def test_speed_story_groups_winners_by_size(self):
        self.assertEqual(compare_tab.speed_story(RESULT, "outline"),
                         "Stencils faster for 1 to 10 farms; Outlines faster for 100 to 1,000 farms "
                         "(1.90 s vs 5.00 s at 1,000).")
        self.assertEqual(compare_tab.speed_story(RESULT, "point"),
                         "Stencils faster for 1 to 10 farms; Outlines faster for 100 farms; "
                         "Stencils faster for 1,000 farms (0.80 s vs 1.10 s at 1,000).")

    def test_line_data_skips_missing_server_numbers(self):
        server = compare_tab.LINES[2][1]
        data = compare_tab.line_data(RESULT, "outline", server)
        self.assertEqual(data["stencils"], [])
        self.assertEqual(data["polygons"][-1], (1000, 147.0))

    def test_agreement_text(self):
        self.assertEqual(compare_tab.agreement_text(RESULT, "outline"),
                         "Same verdict from both approaches on 1,000 of 1,001 farms (99.9%).")
