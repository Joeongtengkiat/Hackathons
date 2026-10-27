"""Test 2: the 17 real Thai plots plus 200 test plots, with protected areas (WDPA), checked against
the original layers, and compared with the organisers' due diligence record.
Recorded result (16 Oct): 217/217 identical; evidence 2.4 MB; 7.3 vs 69 ms (9.5x). About 3 minutes."""
from _common import J, OUT, run, tail

run(["eudr_layers.py", "--data-dir", ".", "--plots", J("plots", "200001-17plots-valid.geojson"),
     "--out-dir", J(OUT, "thai_wdpa"), "--protected", J("wdpa", "wdpa_test_areas.geojson"),
     "--test-plots", "200", "--test-strata", "all", "--verify", "--no-baseline"],
    "thai_wdpa_out.txt", "Thai plots + WDPA, --verify")
tail("thai_wdpa_out.txt", 30)
