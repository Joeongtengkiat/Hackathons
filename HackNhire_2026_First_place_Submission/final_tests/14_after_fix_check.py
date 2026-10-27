"""After-fix check (report section 3.3): what the one-line settle-step fix changes, without touching
eudr_layers.py. Makes two temporary copies of eudr_layers.py: one WITHOUT the fix (control) and one WITH it
(`settled = han & ~rv & ...`), re-judges the recorded version 1.6 plots from the same evidence files with each,
compares the verdicts, and runs Test B against the fixed results.
Recorded result (16 Oct): control identical to the recorded results; 4 Jambi verdicts change (TEST-00246
deforestation-free -> probable; TEST-00456, -00467, -00641 needs review -> probable); Thai unchanged;
Test B Jambi c2 972 -> 973 of 1,000, no unexplained differences. About 2 minutes.
Everything goes to <results folder>\\after_fix\\; nothing in tests_v16 or eudr_layers.py is changed."""
import csv
import os
import shutil
import subprocess
import sys
from collections import Counter

from _common import DRY, J, OUT, PY, RECORD, ROOT

W = J(ROOT, OUT, "after_fix")
OLD = 'settled = han & (h <= settled_until'
NEW = 'settled = han & ~rv & (h <= settled_until'
THAI_REAL = J(ROOT, "plots", "200001-17plots-valid.geojson")


def sh(args, log):
    print("> " + " ".join(str(a) for a in args))
    if DRY:
        return 0
    with open(log, "w", encoding="utf-8") as fh:
        return subprocess.call([str(a) for a in args], cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT)


def load(*paths):
    rows = {}
    for p in paths:
        with open(p, encoding="utf-8-sig") as fh:
            rows.update({r["plot_id"]: r for r in csv.DictReader(fh)})
    return rows


def main():
    src = open(J(ROOT, "eudr_layers.py"), encoding="utf-8").read()
    base = src.replace(NEW, OLD)                       # the control never has the fix, even if it is saved
    if base.count(OLD) != 1:
        sys.exit("Could not find the settle line in eudr_layers.py; has it changed?")
    print("eudr_layers.py " + ("already has the fix saved" if NEW in src else "does not have the fix yet") + "; it is not changed.")
    if DRY:
        print(f"> make {W}\\orig and {W}\\fixed, re-judge Jambi and Thai plots with each, then Test B on the fixed results")
        return
    for v, text in (("orig", base), ("fixed", base.replace(OLD, NEW))):
        d = J(W, v)
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        open(J(d, "eudr_layers.py"), "w", encoding="utf-8").write(text)
        shutil.copytree(J(ROOT, RECORD, "jambi_1000"), J(d, "jambi"))
        shutil.copytree(J(ROOT, RECORD, "thai_wdpa"), J(d, "thai"))
        el = J(d, "eudr_layers.py")
        sh([PY, el, "--query-only", "--out-dir", J(d, "jambi"), "--plots", J(ROOT, RECORD, "jambi_1000", "test_plots.geojson"), "--data-dir", "."], J(d, "jambi_query.txt"))
        sh([PY, el, "--query-only", "--out-dir", J(d, "thai"), "--plots", THAI_REAL, "--data-dir", "."], J(d, "thai_real_query.txt"))
        sh([PY, el, "--query-only", "--out-dir", J(d, "thai"), "--plots", J(ROOT, RECORD, "thai_wdpa", "test_plots.geojson"), "--data-dir", "."], J(d, "thai_test_query.txt"))

    lines = []
    rec_j = load(J(ROOT, RECORD, "jambi_1000", "test_plots_result.csv"))
    rec_t = load(J(ROOT, RECORD, "thai_wdpa", "plots_result.csv"), J(ROOT, RECORD, "thai_wdpa", "test_plots_result.csv"))
    o_j, f_j = (load(J(W, v, "jambi", "test_plots_verdicts.csv")) for v in ("orig", "fixed"))
    o_t, f_t = (load(J(W, v, "thai", "200001-17plots-valid_verdicts.csv"), J(W, v, "thai", "test_plots_verdicts.csv")) for v in ("orig", "fixed"))
    lines.append(f"Control (no fix) vs recorded verdicts: Jambi {sum(o_j[p]['verdict'] == rec_j[p]['verdict'] for p in rec_j)}/{len(rec_j)}, "
                 f"Thai {sum(o_t[p]['verdict'] == rec_t[p]['verdict'] for p in rec_t)}/{len(rec_t)} identical")
    for name, o, f in (("Jambi", o_j, f_j), ("Thai", o_t, f_t)):
        lines.append(f"\n{name} verdicts without the fix: {dict(Counter(r['verdict'] for r in o.values()))}")
        lines.append(f"{name} verdicts with the fix:    {dict(Counter(r['verdict'] for r in f.values()))}")
        changed = [p for p in o if o[p]["verdict"] != f[p]["verdict"]]
        lines.append(f"{name} verdict changes: {len(changed)}")
        lines += [f"  {p}: {o[p]['verdict']} -> {f[p]['verdict']} (probable {o[p]['probable_ha']} -> {f[p]['probable_ha']} ha)" for p in changed]
    report = "\n".join(lines)
    print(report)

    print("\nTest B against the fixed results ...")
    tmf = lambda tile: [f"JRC_TMF_AnnualChange_v1_{y}_{tile}.tif" for y in range(2020, 2026)]
    tb = J(W, "test_b")
    sh([PY, J("analysis", "test_b_independent.py"), "--name", "jambi_fixed", "--plots", J(RECORD, "jambi_1000", "test_plots.geojson"),
        "--proto", J(W, "fixed", "jambi", "test_plots_verdicts.csv"), "--gfc", "JRC_GFC2020_V3_N0_E100.tif",
        "--loss", "Hansen_GFC-2025-v1.13_lossyear_00N_100E.tif", "--datamask", "Hansen_GFC-2025-v1.13_datamask_00N_100E.tif",
        "--tmf-annual", *tmf("ASI_ID39_N0_E100"), "--tmf-defyear", "JRC_TMF_DeforestationYear_INT_1982_2025_v1_ASI_ID39_N0_E100.tif",
        "--rubber", J("rubber", "rubber_SEA_2020_100.17E_2.35S.tif"), "--radd", J("radd", "radd_alerts_00N_100E_date_conf.tif"),
        "--dist", J("alerts", "umd_glad_dist_alerts_00N_100E_date_conf.tif"), "--alerts-after", "2025", "--out", tb], J(W, "test_b_jambi_log.txt"))
    sh([PY, J("analysis", "test_b_independent.py"), "--name", "thai_fixed", "--plots", J("plots", "200001-17plots-valid.geojson"), J(RECORD, "thai_wdpa", "test_plots.geojson"),
        "--proto", J(W, "fixed", "thai", "200001-17plots-valid_verdicts.csv"), J(W, "fixed", "thai", "test_plots_verdicts.csv"),
        "--gfc", "JRC_GFC2020_V3_N10_E90.tif", "--loss", "Hansen_GFC-2025-v1.13_lossyear_10N_090E.tif",
        "--datamask", "Hansen_GFC-2025-v1.13_datamask_10N_090E.tif", "--tmf-annual", *tmf("ASI_ID60_N10_E90"),
        "--tmf-defyear", "JRC_TMF_DeforestationYear_INT_1982_2025_v1_ASI_ID60_N10_E90.tif", "--rubber", J("rubber", "rubber_SEA_2020_96.17E_5.65N.tif"),
        "--dist", J("alerts", "umd_glad_dist_alerts_10N_090E_date_conf.tif"), "--alerts-after", "2025", "--wdpa", J("wdpa", "wdpa_test_areas.geojson"), "--out", tb],
       J(W, "test_b_thai_log.txt"))
    import json
    for n in ("jambi_fixed", "thai_fixed"):
        s = json.load(open(J(tb, f"test_b_{n}_summary.json")))
        c, t = s["c2_any_indication"], s["two_source_vs_noncompliant"]
        report += (f"\n\nTest B, {n}: c2 {c['agree']}/{c['judged']} {c['mismatch_reasons']}"
                   f"\n  two-source {t['agree']}/{t['judged']} {t['mismatch_reasons']}")
    print(report[report.index("Test B,"):])
    open(J(W, "after_fix_summary.txt"), "w", encoding="utf-8").write(report + "\n")
    print(f"\nSaved to {J(OUT, 'after_fix', 'after_fix_summary.txt')}")


if __name__ == "__main__":
    main()
