"""Test 7 (Test B, independent checker): analysis/test_b_independent.py shares no code with eudr_layers.py
and reads the original layers. Compares c2 (any indication of deforestation), two-source agreement vs
'non-compliant', plot area, 2020 forest, Hansen loss on its own 30 m grid, and protected-area overlap
(exact polygons; also with the corrected Thai WDPA and an IUCN Ia/Ib/II filter). Then measures the
Hansen + RADD 'settle' override across the Jambi box (override_scan.py).
Recorded result (16 Oct): Jambi c2 972/1,000, two-source 999/1,000; Thai c2 215/217, two-source 217/217,
d1 217/217; every difference explained; 1 bug found (about 430 ha). About 2 minutes."""
from _common import J, OUT, results_dir, run, tail

R = results_dir()
T = J(OUT, "test_b")
tmf = lambda tile: [f"JRC_TMF_AnnualChange_v1_{y}_{tile}.tif" for y in range(2020, 2026)]
run([J("analysis", "test_b_independent.py"), "--name", "jambi",
     "--plots", J(R, "jambi_1000", "test_plots.geojson"), "--proto", J(R, "jambi_1000", "test_plots_result.csv"),
     "--gfc", "JRC_GFC2020_V3_N0_E100.tif", "--loss", "Hansen_GFC-2025-v1.13_lossyear_00N_100E.tif",
     "--datamask", "Hansen_GFC-2025-v1.13_datamask_00N_100E.tif", "--tmf-annual", *tmf("ASI_ID39_N0_E100"),
     "--tmf-defyear", "JRC_TMF_DeforestationYear_INT_1982_2025_v1_ASI_ID39_N0_E100.tif",
     "--rubber", J("rubber", "rubber_SEA_2020_100.17E_2.35S.tif"), "--radd", J("radd", "radd_alerts_00N_100E_date_conf.tif"),
     "--dist", J("alerts", "umd_glad_dist_alerts_00N_100E_date_conf.tif"), "--alerts-after", "2025", "--out", T],
    J("test_b", "jambi_log.txt"), "Test B, Jambi")
tail(J("test_b", "jambi_log.txt"), 14)
run([J("analysis", "test_b_independent.py"), "--name", "thai",
     "--plots", J("plots", "200001-17plots-valid.geojson"), J(R, "thai_wdpa", "test_plots.geojson"),
     "--proto", J(R, "thai_wdpa", "plots_result.csv"), J(R, "thai_wdpa", "test_plots_result.csv"),
     "--gfc", "JRC_GFC2020_V3_N10_E90.tif", "--loss", "Hansen_GFC-2025-v1.13_lossyear_10N_090E.tif",
     "--datamask", "Hansen_GFC-2025-v1.13_datamask_10N_090E.tif", "--tmf-annual", *tmf("ASI_ID60_N10_E90"),
     "--tmf-defyear", "JRC_TMF_DeforestationYear_INT_1982_2025_v1_ASI_ID60_N10_E90.tif",
     "--rubber", J("rubber", "rubber_SEA_2020_96.17E_5.65N.tif"), "--dist", J("alerts", "umd_glad_dist_alerts_10N_090E_date_conf.tif"),
     "--alerts-after", "2025", "--wdpa", J("wdpa", "wdpa_test_areas.geojson"), "--out", T],
    J("test_b", "thai_log.txt"), "Test B, Thai (with WDPA, corrected WDPA and IUCN filter)")
tail(J("test_b", "thai_log.txt"), 14)
run([J("tests_v16", "test_b", "override_scan.py")], J("test_b", "override_scan.txt"), "Hansen + RADD settle override, Jambi box")
tail(J("test_b", "override_scan.txt"), 5)
