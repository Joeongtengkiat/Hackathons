"""
Full pipeline sequence, pure Python. Run with:  python run_all.py

Same commands as the old run_all.sh, mapped onto the 3-day deadline, just
invoked from Python instead of bash — no shell scripting required anywhere.

This runs everything unattended, which is NOT how you should actually use it:
read the comments, run day by day, and look at each stage's output before
moving to the next. Day 1's audit step in particular should change what you
do next, not just print something you skim past.

Usage:
    python run_all.py --day 1
    python run_all.py --day 2
    python run_all.py --day 3
    python run_all.py --day all      # everything, back to back (not recommended)
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def run(cmd: list[str], allow_fail: bool = False) -> int:
    print(f"\n$ {' '.join(cmd)}")
    r = subprocess.run(cmd, cwd=HERE)
    if r.returncode != 0 and not allow_fail:
        print(f"\nFAILED (exit {r.returncode}): {' '.join(cmd)}")
        sys.exit(r.returncode)
    return r.returncode


def pip_install() -> None:
    run([sys.executable, "-m", "pip", "install",
        "torch", "torchvision", "transformers", "datasets",
        "pillow", "numpy", "scikit-learn", "matplotlib"])


# ==========================================================================
# DAY 1 — plumbing and the shortcut check
# ==========================================================================

def day1() -> None:
    print("\n" + "=" * 70)
    print("DAY 1 — plumbing and the shortcut check")
    print("=" * 70)

    pip_install()

    # 1.1  Measure the shortcut BEFORE anything else. ~2 minutes.
    #      SID_Set synthetics are all 1024x1024; reals are OpenImages photos.
    #      If "square => fake" scores in the 90s, every later number is
    #      suspect until geometry is normalised. Screenshot this output.
    run([sys.executable, "prepare_sid.py", "--audit", "--n", "3000"])

    # 1.2  Build the binary data. Geometry normalisation is ON by default.
    run([sys.executable, "prepare_sid.py", "--split", "train", "--out", "data/train",
        "--per-class", "25000"])
    run([sys.executable, "prepare_sid.py", "--split", "validation", "--out", "data/val",
        "--per-class", "3000"])
    run([sys.executable, "prepare_sid.py", "--split", "train", "--out", "data/test",
        "--per-class", "4000", "--offset", "25000"])

    # 1.3  Tampered probe — BONUS scoring only. Never trained on.
    run([sys.executable, "prepare_sid.py", "--split", "validation", "--out", "data/probe",
        "--per-class", "2000", "--tampered"])

    # 1.4  Smoke test end to end on 100 images per class. Ignore the metrics;
    #      you are checking that every script runs and writes its output.
    run([sys.executable, "embed.py", "--data", "data/train", "--out", "cache/smoke_tr",
        "--views", "2", "--limit", "100"])
    run([sys.executable, "embed.py", "--data", "data/val", "--out", "cache/smoke_va",
        "--views", "2", "--limit", "100"])
    run([sys.executable, "train.py", "--train", "cache/smoke_tr", "--val", "cache/smoke_va",
        "--out", "runs/smoke", "--epochs", "3"])
    run([sys.executable, "evaluate.py", "--data", "data/test", "--ckpt", "runs/smoke/head.pt",
        "--out", "reports/smoke", "--limit", "50", "--groups", "grid"])

    # 1.5  ABLATION 1 — the geometry shortcut, quantified.
    #      Rebuild a small slice WITHOUT normalisation and compare clean AUROC
    #      against an equivalent normalised run. A large gap IS the finding.
    run([sys.executable, "prepare_sid.py", "--split", "train", "--out", "data/train_raw",
        "--per-class", "3000", "--no-normalise-geometry"])
    run([sys.executable, "prepare_sid.py", "--split", "validation", "--out", "data/val_raw",
        "--per-class", "800", "--no-normalise-geometry"])
    run([sys.executable, "embed.py", "--data", "data/train_raw", "--out", "cache/raw_tr",
        "--views", "2"])
    run([sys.executable, "embed.py", "--data", "data/val_raw", "--out", "cache/raw_va",
        "--views", "2"])
    run([sys.executable, "train.py", "--train", "cache/raw_tr", "--val", "cache/raw_va",
        "--out", "runs/raw", "--epochs", "15"])

    print("\nDay 1 done. Compare runs/raw/history.json against a normalised run "
         "before proceeding to Day 2.")


# ==========================================================================
# DAY 2 — the real model and robustness
# ==========================================================================

def day2() -> None:
    print("\n" + "=" * 70)
    print("DAY 2 — the real model and robustness")
    print("=" * 70)

    # 2.1  Full embedding cache. The only expensive step (~1h at 50k x 6).
    run([sys.executable, "embed.py", "--data", "data/train", "--out", "cache/train",
        "--views", "6", "--num-workers", "16"])
    run([sys.executable, "embed.py", "--data", "data/val", "--out", "cache/val",
        "--views", "6", "--num-workers", "16"])
    run([sys.executable, "embed.py", "--data", "data/probe", "--out", "cache/probe",
        "--views", "6", "--num-workers", "16"])

    # 2.2  ABLATION 3 — the min-max objective. Three runs, seconds each.
    run([sys.executable, "train.py", "--train", "cache/train", "--val", "cache/val",
        "--out", "runs/mean", "--adv-mode", "mean"])
    run([sys.executable, "train.py", "--train", "cache/train", "--val", "cache/val",
        "--out", "runs/cvar", "--adv-mode", "cvar"])
    run([sys.executable, "train.py", "--train", "cache/train", "--val", "cache/val",
        "--out", "runs/max", "--adv-mode", "max"])

    # 2.3  ABLATION 2 and 4 — augmentation and consistency.
    run([sys.executable, "train.py", "--train", "cache/train", "--val", "cache/val",
        "--out", "runs/clean_only", "--adv-views", "1", "--consistency", "0"])
    run([sys.executable, "train.py", "--train", "cache/train", "--val", "cache/val",
        "--out", "runs/no_consistency", "--adv-mode", "cvar", "--consistency", "0"])

    # 2.4  ABLATION 5 — head capacity.
    run([sys.executable, "train.py", "--train", "cache/train", "--val", "cache/val",
        "--out", "runs/linear", "--head", "linear"])

    # 2.5  Robustness grid on the winner (usually runs/cvar).
    run([sys.executable, "evaluate.py", "--data", "data/test", "--ckpt", "runs/cvar/head.pt",
        "--out", "reports/cvar"])

    print("\nDay 2 done. Compare runs/{mean,cvar,max}/history.json to pick the winner "
         "before Day 3.")


# ==========================================================================
# DAY 3 — bonus probe, error analysis, write-up
# ==========================================================================

def day3() -> None:
    print("\n" + "=" * 70)
    print("DAY 3 — bonus probe, error analysis, write-up")
    print("=" * 70)

    # 3.1  BONUS — tampered images, binned by edited-region size.
    run([sys.executable, "probe_tampered.py", "--probe", "data/probe", "--ckpt",
        "runs/cvar/head.pt", "--out", "reports/tampered",
        "--reference", "reports/cvar/scores.npz"])

    # 3.2  ABLATIONS 6 and 7 — feature tap and preprocessing. Each needs a
    #      re-embed, so only run these if Day 2 finished early. Left as
    #      commented-out reference commands, matching the old script:
    #
    # run([sys.executable, "embed.py", "--data", "data/train", "--out",
    #     "cache/train_pooled", "--views", "6", "--feature", "pooled"])
    # run([sys.executable, "embed.py", "--data", "data/val", "--out",
    #     "cache/val_pooled", "--views", "6", "--feature", "pooled"])
    # run([sys.executable, "train.py", "--train", "cache/train_pooled", "--val",
    #     "cache/val_pooled", "--out", "runs/pooled"])

    # 3.3  Error analysis. Pull the 20 real images with the highest scores and
    #      LOOK at them. One honest paragraph about what your false positives
    #      have in common beats another 0.01 of AUROC.
    error_analysis_code = '''
import numpy as np, json
d = np.load("reports/cvar/scores.npz", allow_pickle=True)
y, s = d["y"], d["clean"]
idx = np.where(y == 0)[0]
worst = idx[np.argsort(-s[idx])][:20]
print(json.dumps({"indices": worst.tolist(), "scores": s[worst].round(3).tolist()}, indent=2))
'''
    run([sys.executable, "-c", error_analysis_code])

    print("\nDay 3 done. Stop tuning. Write the report.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", choices=["1", "2", "3", "all"], required=True)
    args = ap.parse_args()

    if args.day in ("1", "all"):
        day1()
    if args.day in ("2", "all"):
        day2()
    if args.day in ("3", "all"):
        day3()


if __name__ == "__main__":
    main()