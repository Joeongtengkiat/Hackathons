"""(Version 1.5) The test plan's Must tests, about 2 hours (the province store is last). Results go to
tests_v15\\, one folder per test; steps done so far in tests_v15\\progress.txt; everything summed up in
tests_v15\\tests_report.txt. A step whose results are already there is skipped, so after a stop just run again."""
import time
from _run import DRY, J, echo, exists, here, now, run, run_if_missing
T = "tests_v15"
P = J(T, "progress.txt")
JAMBI = ["103.0", "-2.3", "103.5", "-1.8"]
NOPROV = ["--skip-files", "_province.tif"]
THAI = J("plots", "200001-17plots-valid.geojson")
EL = "eudr_layers.py"
A = lambda s: J("analysis", s)
t = lambda *p: J(T, *p)
echo(P, f"started {now()}")

print("===== 1. Jambi box, same settings as version 1.4 (P2) =====")
run_if_missing(t("jambi_1000", "summary.json"), [EL, "--data-dir", ".", "--bbox", *JAMBI, "--out-dir", t("jambi_1000"), "--test-plots", "1000", "--verify", *NOPROV])
run([A("compare_runs.py"), "out", t("jambi_1000")], t("p2_jambi_runs.txt"))
run([A("compare_evidence.py"), "out", t("jambi_1000")], t("p2_jambi_evidence.txt"))
echo(P, f"1 Jambi box 1.4 settings: done {now()}")

print("===== 2. Thai plots, same settings as version 1.4 (P2) =====")
run_if_missing(t("thai", "summary.json"), [EL, "--data-dir", ".", "--plots", THAI, "--out-dir", t("thai"), "--test-plots", "200", "--verify"])
run([A("compare_runs.py"), J("out_plots", "200001-17plots-valid"), t("thai")], t("p2_thai_runs.txt"))
run([A("compare_evidence.py"), J("out_plots", "200001-17plots-valid"), t("thai")], t("p2_thai_evidence.txt"))
echo(P, f"2 Thai plots 1.4 settings: done {now()}")

print("===== 3. Geometry edge cases (D4, D14) =====")
run([A("make_edge_plots.py"), "make", t("jambi_1000"), t("edge")], t("edge_make.txt"))
run([EL, "--query-only", "--out-dir", t("jambi_1000"), "--plots", t("edge", "edge_plots.geojson"), "--verify", "--data-dir", ".", *NOPROV])
run([EL, "--query-only", "--out-dir", t("jambi_1000"), "--plots", t("edge", "edge_plots_3857.geojson")])
run([A("make_edge_plots.py"), "check", t("jambi_1000", "edge_plots_verdicts.csv"), t("edge", "edge_plots_expected.json"), t("jambi_1000", "edge_plots_3857_verdicts.csv")], t("edge_check.txt"))
echo(P, f"3 edge cases: done {now()}")

print("===== 4. Jambi box, 10,000 test plots on every branch of the rule (Q1, Q10, D1, D2) =====")
run_if_missing(t("jambi_10k", "summary.json"), [EL, "--data-dir", ".", "--bbox", *JAMBI, "--out-dir", t("jambi_10k"), "--test-plots", "10000", "--test-strata", "all", "--verify", "--no-baseline", *NOPROV])
echo(P, f"4 Jambi 10k: done {now()}")

print("===== 5. Tile store: Jambi and Thai squares; same as the 1.4 store? (P2); 10,000 plots both ways (Q1, Q6) =====")
run_if_missing(t("store_jambi", "summary.json"), [EL, "--data-dir", ".", "--tile-store", t("store"), "--bbox", *JAMBI, "--out-dir", t("store_jambi"), *NOPROV])
run_if_missing(t("store_thai", "summary.json"), [EL, "--data-dir", ".", "--tile-store", t("store"), "--plots", THAI, "--out-dir", t("store_thai")])
run([A("compare_evidence.py"), "tile_store", t("store"), "--bbox", *JAMBI], t("p2_store_jambi.txt"))
run([A("compare_evidence.py"), "tile_store", t("store"), "--bbox", "98.7", "8.4", "99.1", "8.6"], t("p2_store_thai.txt"))
run([EL, "--query-only", "--tile-store", t("store"), "--out-dir", t("store_jambi"), "--plots", t("edge", "edge_plots.geojson")])
run([A("compare_runs.py"), t("jambi_1000", "edge_plots_verdicts.csv"), t("store_jambi", "edge_plots_verdicts.csv")], t("edge_box_vs_store.txt"))
run([A("query_bench.py"), t("jambi_10k"), t("jambi_10k", "test_plots.geojson"), "--label", "box_10k", "--out-dir", t("bench")], t("bench_box_10k.txt"))
run([A("query_bench.py"), t("store"), t("jambi_10k", "test_plots.geojson"), "--label", "store_10k", "--out-dir", t("bench")], t("bench_store_10k.txt"))
run([A("compare_runs.py"), t("bench", "bench_box_10k_plots.csv"), t("bench", "bench_store_10k_plots.csv")], t("bench_box_vs_store_verdicts.txt"))
run([A("query_bench.py"), t("jambi_10k"), "--sizes", "--label", "box_sizes", "--out-dir", t("bench")], t("bench_sizes.txt"))
echo(P, f"5 store + benchmarks: done {now()}")

print("===== 6. Rule variants re-judged from the evidence and checked against the originals (D3) =====")
run([EL, "--query-only", "--out-dir", t("jambi_10k"), "--plots", t("jambi_10k", "test_plots.geojson"), "--hansen-only", "review", "--verify", "--data-dir", ".", *NOPROV], t("d3_review.txt"))
run([EL, "--query-only", "--out-dir", t("jambi_10k"), "--plots", t("jambi_10k", "test_plots.geojson"), "--hansen-only", "probable", "--verify", "--data-dir", ".", *NOPROV], t("d3_probable.txt"))
echo(P, f"6 rule variants: done {now()}")

print("===== 7. Landscapes (S2, P1) and the land cover / rubber map edges (D8) =====")
for name, box, extra in (("leuser", ["97.3", "3.4", "97.8", "3.9"], ["--compress-test"]), ("java", ["107.3", "-7.0", "107.8", "-6.5"], []),
                         ("pelalawan", ["101.8", "-0.5", "102.3", "-0.01"], []), ("berbak", ["104.0", "-1.6", "104.5", "-1.1"], [])):
    run_if_missing(t(name, "summary.json"), [EL, "--data-dir", ".", "--bbox", *box, "--out-dir", t(name), "--test-plots", "1000", "--test-strata", "all", "--verify", *extra])
run([A("make_edge_plots.py"), "line", "101.8", "-0.5", "102.3", "-0.01", "--lon", "102.0", "90", t("lines", "across_102E.geojson"), "1"])
run([A("make_edge_plots.py"), "line", "104.0", "-1.6", "104.5", "-1.1", "--lon", "104.1672", "90", t("lines", "across_104.17E.geojson"), "2"])
run([EL, "--query-only", "--out-dir", t("pelalawan"), "--plots", t("lines", "across_102E.geojson"), "--verify", "--data-dir", "."], t("d8_102E.txt"))
run([EL, "--query-only", "--out-dir", t("berbak"), "--plots", t("lines", "across_104.17E.geojson"), "--verify", "--data-dir", "."], t("d8_104E.txt"))
run_if_missing(t("store_pelalawan", "summary.json"), [EL, "--data-dir", ".", "--tile-store", t("store"), "--bbox", "101.8", "-0.5", "102.3", "-0.01", "--out-dir", t("store_pelalawan")])
run_if_missing(t("store_berbak", "summary.json"), [EL, "--data-dir", ".", "--tile-store", t("store"), "--bbox", "104.0", "-1.6", "104.5", "-1.1", "--out-dir", t("store_berbak")])
run([A("compare_evidence.py"), t("store"), t("pelalawan"), "--bbox", "101.803", "-0.497", "102.297", "-0.013"], t("d8_store_vs_box_102E.txt"))
run([A("compare_evidence.py"), t("store"), t("berbak"), "--bbox", "104.003", "-1.597", "104.497", "-1.103"], t("d8_store_vs_box_104E.txt"))
echo(P, f"7 landscapes and edges: done {now()}")

print("===== 8. Missing tile: the equator box with the northern tile hidden (D7) =====")
HIDE = ["--skip-files", "n10_e100", "--skip-files", "10n_100e"]
EQ = ["101.3", "-0.1", "101.5", "0.1"]
run_if_missing(t("equator_hidden", "summary.json"), [EL, "--data-dir", ".", "--bbox", *EQ, "--out-dir", t("equator_hidden"), "--test-plots", "1000", "--test-strata", "all", "--verify", *HIDE])
run([A("make_edge_plots.py"), "line", *EQ, "--lat", "0.0", "90", t("lines", "across_equator.geojson"), "3"])
run([EL, "--query-only", "--out-dir", t("equator_hidden"), "--plots", t("lines", "across_equator.geojson"), "--verify", "--data-dir", ".", *HIDE], t("d7_hidden_lines.txt"))
run_if_missing(t("store_hidden_run", "summary.json"), [EL, "--data-dir", ".", "--tile-store", t("store_hidden"), "--bbox", *EQ, "--out-dir", t("store_hidden_run"), *HIDE])
run([EL, "--query-only", "--tile-store", t("store_hidden"), "--out-dir", t("store_hidden_run"), "--plots", t("lines", "across_equator.geojson")])
echo(P, f"8 missing tile: done {now()}")

print("===== waiting for get_test_data.py to finish (N10_E100 tiles, alert versions), up to 90 minutes =====")
for _ in range(90):
    if DRY:
        break
    try:
        if "Done in" in open(here("get_test_data_log.txt"), encoding="utf-8", errors="replace").read():
            break
    except OSError:
        pass
    time.sleep(60)

print("===== 9. Real 10-degree tile edges (D5, D6, D7 with the tile back) =====")
run_if_missing(t("equator_full", "summary.json"), [EL, "--data-dir", ".", "--bbox", *EQ, "--out-dir", t("equator_full"), "--test-plots", "1000", "--test-strata", "all", "--verify"])
run([EL, "--query-only", "--out-dir", t("equator_full"), "--plots", t("lines", "across_equator.geojson"), "--verify", "--data-dir", "."], t("d5_equator_lines.txt"))
run([A("compare_runs.py"), t("equator_hidden", "across_equator_verdicts.csv"), t("equator_full", "across_equator_verdicts.csv")], t("d7_hidden_vs_full.txt"))
RIAU, E100 = ["101.2", "-0.25", "101.6", "0.25"], ["99.8", "0.2", "100.2", "0.6"]
run_if_missing(t("riau_equator", "summary.json"), [EL, "--data-dir", ".", "--bbox", *RIAU, "--out-dir", t("riau_equator"), "--test-plots", "1000", "--test-strata", "all", "--verify"])
run_if_missing(t("e100_north", "summary.json"), [EL, "--data-dir", ".", "--bbox", *E100, "--out-dir", t("e100_north"), "--test-plots", "1000", "--test-strata", "all", "--verify"])
run([A("make_edge_plots.py"), "line", *E100, "--lon", "100.0", "90", t("lines", "across_100E.geojson"), "4"])
run([EL, "--query-only", "--out-dir", t("e100_north"), "--plots", t("lines", "across_100E.geojson"), "--verify", "--data-dir", "."], t("d5_100E_lines.txt"))
run_if_missing(t("store_riau_equator", "summary.json"), [EL, "--data-dir", ".", "--tile-store", t("store"), "--bbox", *RIAU, "--out-dir", t("store_riau_equator")])
run_if_missing(t("store_e100_north", "summary.json"), [EL, "--data-dir", ".", "--tile-store", t("store"), "--bbox", *E100, "--out-dir", t("store_e100_north")])
run([A("compare_evidence.py"), t("store"), t("riau_equator"), "--bbox", "101.203", "-0.247", "101.597", "0.247"], t("d6_store_vs_box_equator.txt"))
run([A("compare_evidence.py"), t("store"), t("e100_north"), "--bbox", "99.803", "0.203", "100.197", "0.597"], t("d6_store_vs_box_100E.txt"))
run_if_missing(t("store_sides_s", "summary.json"), [EL, "--data-dir", ".", "--tile-store", t("store_sides"), "--bbox", "101.2", "-0.25", "101.6", "0.0", "--out-dir", t("store_sides_s")])
run_if_missing(t("store_sides_n", "summary.json"), [EL, "--data-dir", ".", "--tile-store", t("store_sides"), "--bbox", "101.2", "0.0", "101.6", "0.25", "--out-dir", t("store_sides_n")])
run([A("compare_evidence.py"), t("store"), t("store_sides"), "--bbox", *RIAU], t("d6_store_one_go_vs_sides.txt"))
run([EL, "--data-dir", ".", "--tile-store", t("store_hidden"), "--bbox", *EQ, "--out-dir", t("store_hidden_after")], t("d7_store_tile_added.txt"))
run([A("compare_evidence.py"), t("store"), t("store_hidden"), "--bbox", *EQ], t("d7_store_after_vs_store.txt"))
echo(P, f"9 real tile edges: done {now()}")

print("===== 10. Weekly update: two consecutive RADD and DIST-ALERT versions (S8) =====")
if not exists(t("update_cost.txt")):
    run([A("run_update_test.py")], t("update_cost.txt"))
echo(P, f"10 weekly update: done {now()}")

print("===== 11. Years to 2147 (7-bit years, S4) and the 30 m grid (S5) =====")
run_if_missing(t("jambi_yb7", "summary.json"), [EL, "--data-dir", ".", "--bbox", *JAMBI, "--out-dir", t("jambi_yb7"), "--year-bits", "7", "--no-baseline", "--no-forest-polygons", "--no-rubber-polygons", *NOPROV])
run_if_missing(t("jambi_30m", "summary.json"), [EL, "--data-dir", ".", "--bbox", *JAMBI, "--out-dir", t("jambi_30m"), "--grid", "30m", "--no-baseline", "--test-plots", "1000", "--verify", *NOPROV])
run([A("compare_evidence.py"), t("jambi_1000"), t("jambi_yb7")], t("s4_yb7_vs_4bit.txt"))
echo(P, f"11 year bits and 30 m: done {now()}")

print("===== 12. All of Jambi province in the tile store (S1), then 100,000 plots over it (Q2) =====")
run_if_missing(t("store_province", "summary.json"), [EL, "--data-dir", ".", "--tile-store", t("store"), "--bbox", "101.1", "-2.8", "104.6", "-0.7", "--out-dir", t("store_province"), "--test-plots", "2000", "--test-strata", "all", "--verify"])
run([A("query_bench.py"), t("store"), "--random", "100000", "--label", "store_100k", "--out-dir", t("bench")], t("bench_store_100k.txt"))
echo(P, f"12 province store: done {now()}")

print("===== 13. Report =====")
runs = ["jambi_1000", "thai", "jambi_10k", "leuser", "java", "pelalawan", "berbak", "equator_hidden", "equator_full", "riau_equator",
        "e100_north", "jambi_yb7", "jambi_30m", "store_jambi", "store_thai", "store_pelalawan", "store_berbak"]
named = [f"{n}={t(n)}" for n in runs] + [f"store_hidden={t('store_hidden_run')}", f"store_riau_equator={t('store_riau_equator')}",
                                         f"store_e100_north={t('store_e100_north')}", f"store_province={t('store_province')}"]
texts = ["p2_jambi_runs", "p2_jambi_evidence", "p2_thai_runs", "p2_thai_evidence", "p2_store_jambi", "p2_store_thai", "edge_check",
         "edge_box_vs_store", "bench_box_10k", "bench_store_10k", "bench_box_vs_store_verdicts", "bench_sizes", "bench_store_100k",
         "d3_review", "d3_probable", "d8_102E", "d8_104E", "d8_store_vs_box_102E", "d8_store_vs_box_104E", "d7_hidden_lines",
         "d7_hidden_vs_full", "d7_store_tile_added", "d7_store_after_vs_store", "d5_equator_lines", "d5_100E_lines",
         "d6_store_vs_box_equator", "d6_store_vs_box_100E", "d6_store_one_go_vs_sides", "update_cost", "s4_yb7_vs_4bit"]
run([A("tests_report.py"), t("tests_report.txt"), *named, "--texts", *[t(x + ".txt") for x in texts]], t("report_log.txt"))
echo(P, f"all done {now()}")
print("\nDone. Results: tests_v15\\tests_report.txt")
