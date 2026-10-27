"""Test 1: rebuild the Jambi test box (103.0-103.5 E, 1.8-2.3 S) with 1,000 test plots, and check every
plot against the original layers (--verify): decision fidelity (37 outputs per plot), outcome.tif,
pixel facts, pixel fidelity, storage and speed.
Recorded result (16 Oct): 1,000/1,000 identical; 642,540 pixels identical in 17/17 facts; evidence
11.7 MB; 8.9 vs 88 ms per plot (9.9x). About 12 minutes."""
from _common import J, OUT, run, tail

run(["eudr_layers.py", "--data-dir", ".", "--bbox", "103.0", "-2.3", "103.5", "-1.8",
     "--out-dir", J(OUT, "jambi_1000"), "--test-plots", "1000", "--verify", "--skip-files", "_province.tif"],
    "jambi_1000_out.txt", "Jambi box, 1,000 plots, --verify")
tail("jambi_1000_out.txt", 30)
