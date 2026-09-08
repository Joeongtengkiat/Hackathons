"""
Smoke test, pure Python. Run with:  python smoke_test.py

Same five stages as before, just with no shell scripting involved anywhere —
no bash, no `set`, nothing that depends on which shell you happen to be in.
Works identically on Windows PowerShell, WSL, macOS, or Linux.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY_FILES = sorted(p.name for p in HERE.glob("*.py") if p.name != Path(__file__).name)


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    print(f"  $ {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=HERE, **kw)


def stage(n: int, total: int, title: str) -> None:
    print(f"\n== {n}/{total}  {title} ==")


def fail(msg: str) -> None:
    print(f"\nFAILED: {msg}")
    sys.exit(1)


def main() -> None:
    total = 5

    # ---- 1. syntax check every file --------------------------------------
    stage(1, total, "syntax check every file")
    for f in PY_FILES:
        r = run([sys.executable, "-m", "py_compile", f])
        if r.returncode != 0:
            fail(f"{f} failed to compile — see output above")
        print(f"  OK  {f}")

    # ---- 2. augment.py unit check (no torch needed) ----------------------
    stage(2, total, "augment.py unit check (no torch needed)")
    check_code = '''
import pickle
import numpy as np, random
from PIL import Image
import augment as A
import clipfeat as CF

rng, pr = np.random.default_rng(0), random.Random(0)
img = Image.fromarray(np.random.default_rng(1).integers(0, 256, (300, 400, 3)).astype("uint8"))

n = 0
for c in A.EVAL_GRID + A.HELDOUT_GRID + A.CHAIN_GRID:
    out = c.fn(img, rng)
    assert out.mode == "RGB", c.name
    n += 1
print(f"  {n} conditions applied without error")

for _ in range(5):
    out, params = A.sample_chain(img, rng, pr)
    assert out.mode == "RGB"
print("  sample_chain OK")

out = A.normalise_source(img, rng, pr)
assert out.mode == "RGB"
print("  normalise_source OK")

# Pickling check: this is what Windows num_workers>0 actually needs (spawn),
# even though fork-based Linux/Mac workers never exercise this path. A lambda
# or closure here works fine everywhere except Windows, so this check exists
# specifically to catch that platform-specific failure mode before it ships.
for grid_name, grid in [("EVAL_GRID", A.EVAL_GRID), ("HELDOUT_GRID", A.HELDOUT_GRID),
                        ("CHAIN_GRID", A.CHAIN_GRID)]:
    for c in grid:
        pickle.loads(pickle.dumps(c))
print(f"  all {len(A.EVAL_GRID)+len(A.HELDOUT_GRID)+len(A.CHAIN_GRID)} conditions pickle OK "
     "(required for Windows DataLoader workers)")

for name, v in [("clean_view", CF.clean_view()), ("random_view", CF.random_view()),
               ("condition_view", CF.condition_view(A.EVAL_GRID[0]))]:
    pickle.loads(pickle.dumps(v))
print("  clean_view / random_view / condition_view all pickle OK")
'''
    r = run([sys.executable, "-c", check_code])
    if r.returncode != 0:
        fail("augment.py unit check failed — see output above")

    # ---- 3. generate synthetic smoke dataset -----------------------------
    stage(3, total, "generate synthetic smoke dataset")
    smoke_dir = HERE / "data_smoke"
    if smoke_dir.exists():
        shutil.rmtree(smoke_dir)
    r = run([sys.executable, "make_smoke_data.py", "--out", "data_smoke", "--n", "60"])
    if r.returncode != 0:
        fail("make_smoke_data.py failed — see output above")

    # ---- 4. full pipeline on synthetic data --------------------------------
    stage(4, total, "full pipeline on synthetic data (needs torch + transformers + network)")

    missing = []
    for pkg in ("torch", "transformers", "sklearn"):
        r = subprocess.run([sys.executable, "-c", f"import {pkg}"], cwd=HERE)
        if r.returncode != 0:
            missing.append(pkg)
    if missing:
        print(f"  SKIPPED — missing packages: {missing}")
        print("  Install with: pip install torch transformers scikit-learn")
        print("  (stages 1-3 above already caught syntax and augmentation-logic errors)")
        return

    try:
        urllib.request.urlopen("https://huggingface.co", timeout=5)
    except Exception as e:
        print(f"  SKIPPED — huggingface.co unreachable ({e})")
        print("  CLIP weights download from there on first use. If you are offline")
        print("  or behind a firewall, pre-download the model on a machine that has")
        print("  access, then copy ~/.cache/huggingface to this machine:")
        print("    python -c \"from transformers import CLIPVisionModelWithProjection as M; "
             "M.from_pretrained('openai/clip-vit-large-patch14')\"")
        print("  (stages 1-3 above already caught syntax and augmentation-logic errors)")
        return

    for d in ("cache_smoke", "runs_smoke", "reports_smoke"):
        p = HERE / d
        if p.exists():
            shutil.rmtree(p)

    steps = [
        [sys.executable, "embed.py", "--data", "data_smoke/train", "--out", "cache_smoke/train",
         "--views", "3", "--limit", "40"],
        [sys.executable, "embed.py", "--data", "data_smoke/val", "--out", "cache_smoke/val",
         "--views", "3", "--limit", "20"],
        [sys.executable, "train.py", "--train", "cache_smoke/train", "--val", "cache_smoke/val",
         "--out", "runs_smoke/head", "--epochs", "5", "--adv-mode", "cvar"],
        [sys.executable, "evaluate.py", "--data", "data_smoke/test", "--ckpt",
         "runs_smoke/head/head.pt", "--out", "reports_smoke/test", "--limit", "20",
         "--groups", "grid"],
        [sys.executable, "probe_tampered.py", "--probe", "data_smoke/probe", "--ckpt",
         "runs_smoke/head/head.pt", "--out", "reports_smoke/probe", "--limit", "20"],
    ]
    for cmd in steps:
        r = run(cmd)
        if r.returncode != 0:
            fail(f"{cmd[1]} failed — see output above")

    # ---- 5. sanity check the numbers --------------------------------------
    stage(5, total, "sanity check the numbers")
    import json
    metrics_path = HERE / "reports_smoke" / "test" / "metrics.json"
    m = json.loads(metrics_path.read_text())
    clean = next(r for r in m if r["condition"] == "clean")
    auroc = clean["auroc"]
    print(f"  clean AUROC: {auroc:.3f}  (expect > 0.6 — the synthetic signal is easy;"
         f" near 0.5 means something in the pipeline is broken, e.g. labels swapped"
         f" or the wrong tensor reaching the head)")
    if auroc <= 0.6:
        fail("AUROC near chance on an easy synthetic task — investigate before touching real data")
    print("  PASS")

    print("\nALL SMOKE TESTS PASSED — safe to move to real data.")


if __name__ == "__main__":
    main()