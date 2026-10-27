# Final tests (the ones the report uses)

Only the tests whose results appear in the submitted report, one Python file each, numbered in run order.
Open the `datasets` folder in VS Code, open a file here and press Run, or from a terminal in `datasets`:

```
python final_tests\01_v16_jambi_box.py
python final_tests\13_zstd_baseline.py --dry-run     # print the steps, run nothing
```

Version 1.6 tests (01-06), the cost model (10) and the two checks from 16 Oct evening (13, 14) write to
`tests_v16_rerun\`, so the recorded results in `tests_v16\` are never overwritten. Tests 05, 06 and 14 read the
recorded `tests_v16` results (or the rerun ones once 01-02 have been run). The version 1.5 scripts (07-09) and
the Neon test (11-12) write where they always did; 07 and 08 skip any step whose results are already there.

| File | Test | Report section | Recorded result | Time |
| --- | --- | --- | --- | --- |
| `01_v16_jambi_box.py` | Jambi box rebuild, 1,000 plots vs original layers | 3.1, 3.3, 3.4 | 1,000/1,000 identical; evidence 11.7 MB; pixel fidelity table | ~12 min |
| `02_v16_thai_plots.py` | 17 real + 200 test Thai plots, WDPA, organiser record | 3.5 | 217/217 identical; 16/17, 16/17, 15/17, 11/17, 5/5 | ~3 min |
| `03_v16_jambi_10k_query.py` | 10,000 plots from the evidence alone (needs 01) | 3.3 | 10,000/10,000 identical | ~16 min |
| `04_v16_province_store.py` | Jambi province tile store, 735 squares, 2,000 plots | 3.1 | 2,000/2,000 identical; 268.9 + 39.2 MB | ~4 h |
| `05_test_a_rule_sweep.py` | Test A: 864 rule variants | 3.3 | 100% of 412,992 comparisons | ~5 min |
| `06_test_b_independent_checker.py` | Test B: independent checker, corrected WDPA, override scan | 3.3, 3.5 | Jambi 972/1,000 and 999/1,000; Thai 215, 217, 217; Klong Phraya 83% / 94%; ~430 ha | ~2 min |
| `07_v15_test_plan.py` | Version 1.5 test plan (benchmarks, codecs, 7-bit years, 30 m grid, landscapes) | 2.3.1, 3.1, 3.2 | 1.13 vs 10.9 ms; 0.92 ms median; 100,000 plots; codec table | ~2 h |
| `08_v15_test_plan_part2.py` | Version 1.5 part 2 (WDPA, edge cases, HTTP stand-in) | 2.6.1 | ~4 requests and 60 KB per plot | ~15 min |
| `09_v15_benchmarks.py` | Version 1.5 benchmarks on a quiet PC | 3.2 | box and store 10,000 plots; 100,000 plots | ~10 min |
| `10_cost_model.py` | Cost model, draft inputs | 3.6 | $0.10, $1.60 vs $12, $0.52, $30.95; ~$33 vs $43 | seconds |
| `11_neon_list.py` then `12_neon_full.py` | Neon app, read-only | 3.2, 2.6.1 | stencils 6.5 ms batched; outlines 0.38 ms batched | ~10 min |
| `13_zstd_baseline.py` | Original layers re-compressed with each codec | 3.1 | ZSTD 12.50 MB vs evidence 11.69 MB (~1.07x) | ~1 min |
| `14_after_fix_check.py` | The settle-step fix on a temporary copy, then Test B | 3.3 | 4 verdicts change; Test B Jambi 973/1,000 | ~2 min |

`_common.py` and `_run.py` are shared helpers, not tests. Speeds depend on what else the PC is doing: compare
ratios, not absolute milliseconds.
