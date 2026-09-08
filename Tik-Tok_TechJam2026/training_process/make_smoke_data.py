"""
Generate a tiny synthetic dataset so the whole pipeline can be tested without
downloading SID_Set or needing a GPU-hour. This is a plumbing test, not a
model-quality test — the "signal" it embeds is a stand-in for whatever real
generator fingerprint your actual data has.

Real images:  smooth low-frequency gradients + mild noise (photo-like).
Fake images:  the same gradients + a bold structural artifact (stand-in for
              a generator artifact) + a different noise profile.
Tampered:     a real image with a small synthetic patch pasted in, plus a mask.

None of this should be mistaken for actual detection difficulty. Its only job
is to be learnable enough that a correct pipeline shows AUROC well above 0.5
and an incorrect one (bad labels, broken shapes, wrong device, etc.) shows up
as a crash or as AUROC near 0.5.

    python make_smoke_data.py --out data_smoke --n 60
"""
from __future__ import annotations

import argparse
import random
from pathlib import Path

import numpy as np
from PIL import Image


def make_real(rng: np.random.Generator, size: int) -> np.ndarray:
    yy, xx = np.mgrid[0:size, 0:size]
    base = (0.5 + 0.3 * np.sin(xx / size * np.pi + rng.uniform(0, 6)) +
            0.2 * np.cos(yy / size * np.pi + rng.uniform(0, 6)))
    img = np.stack(([base] * 3), axis=-1)
    img += rng.normal(0, 0.05, img.shape)
    return np.clip(img, 0, 1)


def make_fake(rng: np.random.Generator, size: int) -> np.ndarray:
    img = make_real(rng, size)
    yy, xx = np.mgrid[0:size, 0:size]
    # Bold, low-frequency, high-amplitude structural marker -- not a subtle
    # texture. A faint high-frequency pattern (earlier versions used
    # amplitude 0.04-0.15 at period 4-24px) turned out to be fragile across
    # hardware: CPU and GPU floating-point kernels use different internal
    # summation orders even at matching nominal precision, and a signal
    # faint enough sits right at the edge of what survives that difference.
    # This plumbing test has no reason to be subtle -- make it unmissable so
    # a real pipeline bug can never hide behind "the signal was too faint".
    quadrant = (np.logical_xor(xx > size // 2, yy > size // 2)).astype(np.float32)
    img = img * 0.6 + quadrant[..., None] * 0.4          # bold checkerboard-quadrant shift
    img[..., 0] += 0.25                                   # strong, saturating red-channel bias
    img += rng.normal(0, 0.02, img.shape)                 # different noise profile
    return np.clip(img, 0, 1)


def make_tampered(rng: np.random.Generator, size: int) -> tuple[np.ndarray, np.ndarray]:
    img = make_real(rng, size)
    mask = np.zeros((size, size), dtype=np.uint8)
    patch = max(4, int(size * rng.uniform(0.1, 0.35)))
    top = rng.integers(0, size - patch)
    left = rng.integers(0, size - patch)
    fake_patch = make_fake(rng, patch)
    img[top:top + patch, left:left + patch] = fake_patch
    mask[top:top + patch, left:left + patch] = 255
    return np.clip(img, 0, 1), mask


def save(arr: np.ndarray, path: Path) -> None:
    Image.fromarray((arr * 255).astype(np.uint8)).save(path, quality=90)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data_smoke")
    ap.add_argument("--n", type=int, default=60, help="images per class per split")
    ap.add_argument("--size", type=int, default=320, help="native size, before any pipeline resize")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    rng = np.random.default_rng(args.seed)
    out = Path(args.out)

    for split, n in [("train", args.n), ("val", max(8, args.n // 4)), ("test", max(8, args.n // 4))]:
        (out / split / "real" / "src").mkdir(parents=True, exist_ok=True)
        (out / split / "fake" / "gen").mkdir(parents=True, exist_ok=True)
        for i in range(n):
            save(make_real(rng, args.size), out / split / "real" / "src" / f"r{i:04d}.jpg")
            save(make_fake(rng, args.size), out / split / "fake" / "gen" / f"f{i:04d}.jpg")

    # probe: real-labelled root + a tampered class, matching scan_dir's real/fake layout
    n_probe = max(8, args.n // 4)
    (out / "probe" / "real" / "src").mkdir(parents=True, exist_ok=True)
    (out / "probe" / "fake" / "tampered").mkdir(parents=True, exist_ok=True)
    mask_frac = {}
    for i in range(n_probe):
        save(make_real(rng, args.size), out / "probe" / "real" / "src" / f"r{i:04d}.jpg")
        img, mask = make_tampered(rng, args.size)
        name = f"t{i:04d}.jpg"
        save(img, out / "probe" / "fake" / "tampered" / name)
        mask_frac[name] = float((mask > 127).mean())

    import json
    (out / "probe" / "mask_fraction.json").write_text(json.dumps(mask_frac, indent=2))

    print(f"[smoke data] wrote {out}/  train/val/test={args.n}/{max(8,args.n//4)}/{max(8,args.n//4)} "
         f"per class, probe={n_probe}")


if __name__ == "__main__":
    main()