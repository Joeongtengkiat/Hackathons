"""(Version 1.5, after run_tests_v15.py) Protected areas (WDPA) for the d1 column (D12), the geometry edge
cases again, the final P2 check, and plot queries over HTTP range requests (Q7 stand-in). About 15 minutes."""
from _run import J, echo, now, run, run_if_missing
T = "tests_v15"
P = J(T, "progress_b.txt")
NOPROV = ["--skip-files", "_province.tif"]
THAI = J("plots", "200001-17plots-valid.geojson")
EL = "eudr_layers.py"
A = lambda s: J("analysis", s)
t = lambda *p: J(T, *p)
NOPOLY = ["--no-baseline", "--no-forest-polygons", "--no-rubber-polygons"]
echo(P, f"started {now()}")

print("===== D12: Thai plots with protected areas (all WDPA areas, then strict ones: IUCN Ia, Ib, II) =====")
run_if_missing(t("thai_wdpa", "summary.json"), [EL, "--data-dir", ".", "--plots", THAI, "--out-dir", t("thai_wdpa"), "--protected", J("wdpa", "wdpa_test_areas.geojson"), "--test-plots", "200", "--test-strata", "all", "--verify", "--no-baseline"])
run_if_missing(t("thai_wdpa_strict", "summary.json"), [EL, "--data-dir", ".", "--plots", THAI, "--out-dir", t("thai_wdpa_strict"), "--protected", J("wdpa", "wdpa_test_areas_strict.geojson"), *NOPOLY])
run([A("compare_runs.py"), t("thai"), t("thai_wdpa")], t("d12_thai_vs_wdpa.txt"))
echo(P, f"D12 Thai: done {now()}")

print("===== D12: Leuser (Gunung Leuser National Park) and Jambi boxes with protected areas =====")
run_if_missing(t("leuser_wdpa", "summary.json"), [EL, "--data-dir", ".", "--bbox", "97.3", "3.4", "97.8", "3.9", "--out-dir", t("leuser_wdpa"), "--protected", J("wdpa", "wdpa_test_areas.geojson"), "--test-plots", "1000", "--test-strata", "all", "--verify", *NOPOLY])
run_if_missing(t("jambi_wdpa", "summary.json"), [EL, "--data-dir", ".", "--bbox", "103.0", "-2.3", "103.5", "-1.8", "--out-dir", t("jambi_wdpa"), "--protected", J("wdpa", "wdpa_test_areas.geojson"), "--test-plots", "1000", "--test-strata", "all", "--verify", *NOPOLY, *NOPROV])
echo(P, f"D12 boxes: done {now()}")

print("===== D4: geometry edge cases again =====")
run([A("make_edge_plots.py"), "make", t("jambi_1000"), t("edge2")], t("edge2_make.txt"))
run([EL, "--query-only", "--out-dir", t("jambi_1000"), "--plots", t("edge2", "edge_plots.geojson"), "--verify", "--data-dir", ".", *NOPROV], t("edge2_query.txt"))
run([EL, "--query-only", "--out-dir", t("jambi_1000"), "--plots", t("edge2", "edge_plots_3857.geojson")], t("edge2_query.txt"), append=True)
run([A("make_edge_plots.py"), "check", t("jambi_1000", "edge_plots_verdicts.csv"), t("edge2", "edge_plots_expected.json"), t("jambi_1000", "edge_plots_3857_verdicts.csv")], t("edge2_check.txt"))
run([EL, "--query-only", "--tile-store", t("store"), "--out-dir", t("store_jambi"), "--plots", t("edge2", "edge_plots.geojson")], t("edge2_store_query.txt"))
run([A("compare_runs.py"), t("jambi_1000", "edge_plots_verdicts.csv"), t("store_jambi", "edge_plots_verdicts.csv")], t("edge2_box_vs_store.txt"))
echo(P, f"D4 again: done {now()}")

print("===== P2 after the last change (window corners): the 1,000 and 10,000 test plots judged again =====")
run([EL, "--query-only", "--out-dir", t("jambi_1000"), "--plots", t("jambi_1000", "test_plots.geojson")], t("p2_final_query_1000.txt"))
run([A("compare_runs.py"), t("jambi_1000", "test_plots_result.csv"), t("jambi_1000", "test_plots_verdicts.csv")], t("p2_final_1000.txt"))
run([EL, "--query-only", "--out-dir", t("jambi_10k"), "--plots", t("jambi_10k", "test_plots.geojson")], t("p2_final_query_10k.txt"))
run([A("compare_runs.py"), t("jambi_10k", "test_plots_result.csv"), t("jambi_10k", "test_plots_verdicts.csv")], t("p2_final_10k.txt"))
echo(P, f"P2 final: done {now()}")

print("===== Q7 stand-in: plot queries over HTTP range requests, as from a cloud bucket =====")
run([A("http_bench.py"), t("jambi_10k"), t("jambi_10k", "test_plots.geojson"), "2000"], t("http_bench.txt"))
run([A("http_bench.py"), t("jambi_10k"), t("jambi_10k", "test_plots.geojson"), "300", "--no-cache"], t("http_bench_nocache.txt"))
echo(P, f"Q7 stand-in: done {now()}")
echo(P, f"all done {now()}")
print("Done. See tests_v15\\progress_b.txt")
