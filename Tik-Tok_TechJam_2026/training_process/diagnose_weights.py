"""
Diagnose whether CLIP's weights actually made it onto the GPU.

Context: fp16 autocast and TF32 have both been ruled out as the cause of the
near-chance embeddings on GPU (three runs with different precision settings
produced IDENTICAL AUROC trajectories -- real precision differences always
wobble the exact numbers; identical numbers mean precision was never the
variable that mattered).

The next candidate: low_cpu_mem_usage=True (added earlier to fix an OOM
during model load) uses a "meta device" trick internally -- the model
skeleton is built with no real memory allocated, then real weights are
loaded directly into place. If that materialization step doesn't complete
correctly for any reason, .to(device) can silently leave some parameters as
meta tensors (placeholders with no real data) or otherwise degenerate. The
model then runs without erroring, but produces meaningless output --
completely insensitive to precision, which matches exactly what was seen.

This script checks for that directly:
  1. Are any parameters still on the meta device, NaN, or suspiciously
     constant after load_clip() + .to(device)?
  2. Do two CLEARLY DIFFERENT real images produce meaningfully different
     embeddings? A model producing garbage often collapses everything to
     nearly the same output regardless of input -- that is the real tell.

    python diagnose_weights.py
"""
from __future__ import annotations

import numpy as np
import torch
from PIL import Image

import clipfeat as CF


def check_parameters(model) -> list[tuple[str, str]]:
    problems = []
    for name, p in model.named_parameters():
        if p.device.type == "meta":
            problems.append((name, "on META device -- weight was never materialized"))
        elif torch.isnan(p).any():
            problems.append((name, "contains NaN"))
        elif torch.isinf(p).any():
            problems.append((name, "contains Inf"))
        elif p.numel() > 4 and p.std().item() < 1e-8:
            problems.append((name, f"suspiciously constant (std={p.std().item():.2e}) -- "
                                   "looks uninitialized, not trained"))
    return problems


def make_probe_images() -> list[tuple[str, Image.Image]]:
    """Two images that should NOT produce similar embeddings: solid black,
    solid white, and (if present) one real image from data_smoke."""
    imgs = [
        ("solid_black", Image.new("RGB", (224, 224), (0, 0, 0))),
        ("solid_white", Image.new("RGB", (224, 224), (255, 255, 255))),
        ("random_noise", Image.fromarray(
            np.random.default_rng(0).integers(0, 256, (224, 224, 3)).astype("uint8"))),
    ]
    from pathlib import Path
    p = Path("data_smoke/train/real/src")
    if p.is_dir():
        files = sorted(p.glob("*.jpg"))[:1]
        if files:
            imgs.append(("real_sample", Image.open(files[0]).convert("RGB")))
    return imgs


def main() -> None:
    print("=" * 70)
    print("1. Loading CLIP and checking every parameter")
    print("=" * 70)
    model, device = CF.load_clip()
    print(f"device={device}")

    problems = check_parameters(model)
    if problems:
        print(f"\n>>> FOUND {len(problems)} BROKEN PARAMETER(S):")
        for name, reason in problems[:20]:
            print(f"    {name}: {reason}")
        if len(problems) > 20:
            print(f"    ... and {len(problems) - 20} more")
        print("\n>>> DIAGNOSIS: the model was not correctly loaded onto the device.")
        print(">>> Fix: drop low_cpu_mem_usage=True from clipfeat.load_clip(), or")
        print(">>> load explicitly then move weights: model = model.to(device)")
        print(">>> after a plain (non-low-mem) from_pretrained call.")
    else:
        print("\nNo broken parameters found (no meta-device, NaN, Inf, or "
             "suspiciously-constant weights). Moving to input-sensitivity check.")

    print("\n" + "=" * 70)
    print("2. Do different images produce different embeddings?")
    print("=" * 70)
    imgs = make_probe_images()
    feats = []
    with torch.no_grad():
        for name, img in imgs:
            t = CF.to_clip_tensor(img, "resize", __import__("random").Random(0))
            t = t.unsqueeze(0).to(device)
            out = model.vision_model(pixel_values=t)
            f = out.pooler_output.float().cpu().numpy()[0]
            feats.append(f)
            print(f"  {name:14s}  norm={np.linalg.norm(f):.4f}  "
                 f"mean={f.mean():.4f}  std={f.std():.4f}")

    print("\n  pairwise cosine similarity (should NOT all be ~1.0):")
    names = [n for n, _ in imgs]
    for i in range(len(feats)):
        for j in range(i + 1, len(feats)):
            a, b = feats[i], feats[j]
            cos = float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-8))
            flag = "  <<< SUSPICIOUS, near-identical" if cos > 0.999 else ""
            print(f"    {names[i]:14s} vs {names[j]:14s}: {cos:.5f}{flag}")

    all_pairs_identical = all(
        float(np.dot(feats[i], feats[j]) /
             (np.linalg.norm(feats[i]) * np.linalg.norm(feats[j]) + 1e-8)) > 0.999
        for i in range(len(feats)) for j in range(i + 1, len(feats))
    )
    print()
    if all_pairs_identical:
        print(">>> DIAGNOSIS CONFIRMED: black, white, noise, and a real photo all produce")
        print(">>> essentially the SAME embedding. The model is not processing input")
        print(">>> meaningfully -- this matches broken/uninitialized weights, not a")
        print(">>> precision issue (which you've already ruled out).")
    elif problems:
        print(">>> Embeddings differ across images, but broken parameters were found")
        print(">>> above -- investigate those specifically; they may affect a subset")
        print(">>> of the computation (e.g. only the projection head) rather than")
        print(">>> everything.")
    else:
        print(">>> Embeddings differ across images AND no broken parameters found.")
        print(">>> This rules out the meta-device/garbage-weights theory entirely.")
        print(">>> Tell me this result -- the next step is comparing actual embedding")
        print(">>> VALUES between a CPU run and this GPU run on the SAME image, to")
        print(">>> find exactly where the two diverge.")


if __name__ == "__main__":
    main()