"""(Version 1.5) Run when nothing else runs: the 10,000-plot query benchmarks again (box and tile store), the
100,000-plot run over the province store (Q1, Q2, Q6), and the storage sizes (S1). About 10 minutes."""
from _run import J, echo, now, run
T = "tests_v15"
A = lambda s: J("analysis", s)
t = lambda *p: J(T, *p)
run([A("query_bench.py"), t("jambi_10k"), t("jambi_10k", "test_plots.geojson"), "--label", "box_10k_idle", "--out-dir", t("bench")], t("bench_box_10k_idle.txt"))
run([A("query_bench.py"), t("store"), t("jambi_10k", "test_plots.geojson"), "--label", "store_10k_idle", "--out-dir", t("bench")], t("bench_store_10k_idle.txt"))
run([A("query_bench.py"), t("store"), "--random", "100000", "--label", "store_100k_idle", "--out-dir", t("bench")], t("bench_store_100k_idle.txt"))
print("===== S1: the province's squares against its inputs =====")
run([A("area_sizes.py"), t("store"), "101.1", "-2.8", "104.6", "-0.7", "--skip-files", "_jambi.tif", "--skip-files", "_berbak.tif"], t("s1_province_sizes.txt"))
run([A("area_sizes.py"), t("store"), "103.0", "-2.3", "103.5", "-1.8", "--skip-files", "_province.tif"], t("s1_jambi_box_sizes.txt"))
echo(t("progress_b.txt"), f"idle benchmarks done {now()}")
