"""Test 3: judge 10,000 Jambi test plots from the evidence alone (--query-only), each also judged from
the original layers (--verify). Needs Test 1's evidence files.
Recorded result (16 Oct): 10,000/10,000 identical; 7.5 vs 71.9 ms (9.6x). About 16 minutes."""
from _common import J, OLD, OUT, need, run, tail

need(J(OUT, "jambi_1000", "evidence_baseline.tif"), "run 01_v16_jambi_box.py first")
run(["eudr_layers.py", "--query-only", "--out-dir", J(OUT, "jambi_1000"),
     "--plots", J(OLD, "jambi_10k", "test_plots.geojson"), "--verify", "--data-dir", ".",
     "--skip-files", "_province.tif"],
    "jambi_10k_query.txt", "Jambi 10,000 plots from the evidence, --verify")
tail("jambi_10k_query.txt", 20)
