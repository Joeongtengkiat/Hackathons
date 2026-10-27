"""Shared helpers for the version 1.6 test suite.

Every test runs from the datasets folder (the parent of this one) with the same Python that runs
the test. Results go to OUT (default tests_v16_rerun) so the recorded results in tests_v16 are
never overwritten. To write somewhere else:  set EUDR_TESTS_OUT=<folder>  before running.
Add --dry-run to any test to print its commands without running them.
"""
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # datasets
OUT = os.environ.get("EUDR_TESTS_OUT", "tests_v16_rerun")
RECORD = "tests_v16"          # results recorded on 16 Oct 2026
OLD = "tests_v15"             # version 1.5 results, for the comparisons
PY = sys.executable
DRY = "--dry-run" in sys.argv
J = os.path.join


def results_dir():
    """Where Tests A and B read the prototype's results: this suite's own reruns if they exist,
    otherwise the recorded 16 Oct results."""
    if os.path.exists(J(ROOT, OUT, "jambi_1000", "test_plots_result.csv")):
        return OUT
    print(f"  (no rerun results in {OUT}; using the recorded results in {RECORD})")
    return RECORD


def run(args, log, label=None):
    """Run one command from the datasets folder; everything it prints goes to `log` (inside OUT)."""
    cmd = [PY] + [str(a) for a in args]
    log_path = J(ROOT, OUT, log)
    print(f"> {label or args[0]}")
    print("    " + " ".join(cmd) + f"   > {J(OUT, log)}")
    if DRY:
        return 0
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    t0 = time.time()
    with open(log_path, "w", encoding="utf-8") as fh:
        rc = subprocess.call(cmd, cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT)
    print(f"    {'done' if rc == 0 else 'FAILED (exit ' + str(rc) + ')'} in {time.time() - t0:.0f} s; log {J(OUT, log)}")
    return rc


def tail(log, n=12):
    """Print the last lines of a log, for a quick look at the result."""
    p = J(ROOT, OUT, log)
    if DRY or not os.path.exists(p):
        return
    with open(p, encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()
    print("\n".join("    | " + ln for ln in lines[-n:]))


def need(path, what):
    if not DRY and not os.path.exists(J(ROOT, path)):
        sys.exit(f"Missing {path}: {what}")
