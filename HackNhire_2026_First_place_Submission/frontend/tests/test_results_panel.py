import unittest

import charts
import results_panel


def run(seconds, trips, rows, nbytes, server_ms=None, blocks=(0, 0)):
    server = None if server_ms is None else {"server_exec_ms": server_ms, "planning_ms": 0.1,
                                             "shared_hit": blocks[0], "shared_read": blocks[1]}
    return {"seconds": seconds, "client_s": seconds * 0.75, "round_trips": trips, "rows": rows, "bytes": nbytes, "server": server,
            "count": 3}


RUNS = {"stencils": {"point": run(0.2, 2, 3, 26000, 0.06, (9, 0))},
        "polygons": {"point": run(0.3, 3, 2, 4, 0.3, (14, 1))}}


def row(name, kind, verdict, s, p, forest=0.0, defo=0.0, stale=False):
    ha = {"2": forest, "3": defo}
    return {"key": name, "name": name, "kind": kind, "verdict": verdict, "stale": stale,
            "results": {"stencils": {"verdict": s, "ha": ha}, "polygons": {"verdict": p, "ha": ha}},
            "agree": {"stencils_polygons": s == p}}


class ComparisonTest(unittest.TestCase):
    def test_metrics_come_from_the_runs(self):
        metrics = {label: values for label, values, _ in results_panel.comparison_metrics(RUNS, "point")}
        self.assertAlmostEqual(metrics["Time over Neon"]["stencils"], 200.0)
        self.assertAlmostEqual(metrics["  waiting on Neon"]["stencils"], 150.0)
        self.assertAlmostEqual(metrics["  computing locally"]["stencils"], 50.0)
        self.assertEqual(metrics["Round trips"], {"stencils": 2, "polygons": 3})
        self.assertEqual(metrics["Data returned"]["stencils"], 26000)
        self.assertEqual(metrics["8 KB blocks touched"], {"stencils": 9, "polygons": 15})

    def test_question_not_asked_gives_nothing(self):
        self.assertEqual(results_panel.comparison_metrics(RUNS, "outline"), [])

    def test_no_server_metrics_without_explain(self):
        runs = {"stencils": {"point": run(0.2, 2, 3, 26000)}, "polygons": {"point": run(0.3, 3, 2, 4)}}
        labels = [label for label, _, _ in results_panel.comparison_metrics(runs, "point")]
        self.assertNotIn("Server execution", labels)

    def test_winners_sentence_lower_wins(self):
        text = results_panel.winners_sentence(results_panel.comparison_metrics(RUNS, "point"))
        self.assertIn("Faster: Stencils (200 ms vs 300 ms)", text)
        self.assertIn("Less data: Outlines (4 B vs 26 kB)", text)

    def test_agreement_and_cells(self):
        rows = [row("A", "outline", "FAIL", "FAIL", "FAIL", 1.234, 0.5),
                row("B", "outline", "MANUAL_REVIEW", "PASS", "MANUAL_REVIEW", stale=True),
                row("C", "point", "PASS", "PASS", "PASS")]
        self.assertEqual(results_panel.agreement(rows, "outline"), (1, 2))
        self.assertEqual(results_panel.table_cells(rows[0]),
                         ["A", "✖ Not compliant", "✖", "✖", "1.23", "0.50"])
        self.assertTrue(results_panel.table_cells(rows[1])[0].endswith("check again"))
        self.assertEqual(results_panel.table_cells(rows[2])[0], "C (point)")
        self.assertEqual(results_panel.table_cells(rows[2])[4], "–")


class ChartMathTest(unittest.TestCase):
    def test_bar_length(self):
        self.assertEqual(charts.bar_length(50, 100, 200), 100)
        self.assertEqual(charts.bar_length(0, 100, 200), 0)
        self.assertEqual(charts.bar_length(None, 100, 200), 0)
        self.assertEqual(charts.bar_length(0.001, 100, 200), 2.0)  # never vanishes

    def test_log_scale(self):
        self.assertEqual(charts.decades(3, 870), (1.0, 1000.0))
        self.assertEqual(charts.decades(2, 5), (1.0, 10.0))
        self.assertAlmostEqual(charts.log_position(10, 1, 1000, 300), 100)
        self.assertAlmostEqual(charts.log_position(0.5, 1, 1000, 300), 0)

    def test_formats(self):
        self.assertEqual(charts.fmt_bytes(512), "512 B")
        self.assertEqual(charts.fmt_bytes(26000), "26 kB")
        self.assertEqual(charts.fmt_bytes(1_535_308), "1.5 MB")
        self.assertEqual(charts.fmt_number(3.14159), "3.14")
        self.assertEqual(charts.fmt_number(1234.5), "1,234")
