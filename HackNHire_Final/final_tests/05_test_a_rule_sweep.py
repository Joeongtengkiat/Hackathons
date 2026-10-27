"""Test 6 (Test A, rule sweep): re-run the decision under 864 rule variants (cut-off year, one vs two sources,
rubber handling, alert confidence, point-plot size, minimum area, protected threshold), each from the
original layers and from the 3 evidence files, and compare all 37 outputs per plot.
Recorded result (16 Oct): 100% on 412,992 comparisons (Jambi 1,000 plots x 288 variants; Thai 217 x 576).
About 3-5 minutes. Reads this suite's rerun results if present, otherwise the recorded ones."""
from _common import J, OUT, results_dir, run, tail

R = results_dir()
run([J("analysis", "rule_sweep.py"), "--evidence-dir", J(R, "jambi_1000"), "--plots", J(R, "jambi_1000", "test_plots.geojson"),
     "--data-dir", ".", "--skip-files", "_province.tif", "--out", J(OUT, "rule_sweep", "jambi")],
    J("rule_sweep", "jambi_out.txt"), "Test A, Jambi")
tail(J("rule_sweep", "jambi_out.txt"), 15)
run([J("analysis", "rule_sweep.py"), "--evidence-dir", J(R, "thai_wdpa"),
     "--plots", J("plots", "200001-17plots-valid.geojson"), J(R, "thai_wdpa", "test_plots.geojson"),
     "--data-dir", ".", "--protected", J("wdpa", "wdpa_test_areas.geojson"), "--out", J(OUT, "rule_sweep", "thai")],
    J("rule_sweep", "thai_out.txt"), "Test A, Thai")
tail(J("rule_sweep", "thai_out.txt"), 15)
