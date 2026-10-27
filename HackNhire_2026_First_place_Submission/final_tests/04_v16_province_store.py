"""Test 4 (LONG, about 4 hours): build all of Jambi province (101.1-104.6 E, 0.7-2.8 S) as a tile store of
0.1-degree squares (735 squares), check 2,000 test plots against the original layers, and measure the
storage. Keep the PC awake. Asks for confirmation unless run with --yes.
Recorded result (16 Oct): 2,000/2,000 identical; evidence 268.9 MB (baseline 155.0, yearly 62.0,
alerts 51.8) + outcome 39.2 MB; 7.5 vs 146.7 ms (19.5x)."""
import sys
from _common import DRY, J, OUT, run, tail

if not DRY and "--yes" not in sys.argv:
    if input("This takes about 4 hours. Type yes to start: ").strip().lower() != "yes":
        sys.exit("Not started.")
run(["eudr_layers.py", "--data-dir", ".", "--tile-store", J(OUT, "store"), "--bbox", "101.1", "-2.8", "104.6", "-0.7",
     "--out-dir", J(OUT, "store_province"), "--test-plots", "2000", "--test-strata", "all", "--verify"],
    "store_province_out.txt", "Jambi province tile store, 2,000 plots, --verify")
run([J("analysis", "area_sizes.py"), J(OUT, "store"), "101.1", "-2.8", "104.6", "-0.7",
     "--skip-files", "_jambi.tif", "--skip-files", "_berbak.tif"],
    "s1_province_sizes.txt", "Province storage sizes")
tail("store_province_out.txt", 20)
