"""
Bundle a trained head + CLIP into one self-contained, portable file.

This does NOT pickle the whole live model (that would mean saving a class
reference, not just data -- see clipfeat.export_clip_bundle's docstring for
why that specific distinction is the actual safety line, not "how much" is
in the file). Everything here is plain data: config dicts, state_dicts,
numbers, strings. That's what makes it safe to reload with weights_only=True,
and it's what makes the result genuinely portable -- no HuggingFace cache,
no network access needed to use it on another machine.

    python export_bundle.py --ckpt runs/cvar/head.pt --out runs/cvar/bundle.pt

Load it back with clipfeat.load_clip_from_bundle() instead of load_clip() --
see load_bundle_example.py for the minimal inference pattern.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import torch

import clipfeat as CF


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", required=True, help="an existing head.pt from train.py")
    ap.add_argument("--out", required=True, help="where to write the combined bundle")
    args = ap.parse_args()

    ck = torch.load(args.ckpt, map_location="cpu", weights_only=False)
    print(f"[export] loading CLIP ({ck['clip_model']}) to bundle its weights...")
    model, _ = CF.load_clip(ck["clip_model"], device="cpu")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    torch.save({
        "clip_config": model.config.to_dict(),
        "clip_state_dict": model.state_dict(),
        "clip_model_name": ck["clip_model"],   # kept for reference/logging only
        "head_state_dict": ck["state_dict"],
        "head_dim": ck["dim"],
        "head_config": ck["config"],
        "mu": torch.as_tensor(ck["mu"]), "sd": torch.as_tensor(ck["sd"]),
        "threshold": ck["threshold"],
        "platt_a": ck.get("platt_a", 1.0), "platt_b": ck.get("platt_b", 0.0),
        "feature": ck["feature"], "preproc": ck["preproc"], "l2": ck["l2"],
    }, out)

    print(f"[export] wrote {out} ({out.stat().st_size/1e6:.0f} MB)")
    print("[export] plain data only (config dicts + state_dicts) -- "
         "loadable with torch.load(..., weights_only=True)")


if __name__ == "__main__":
    main()