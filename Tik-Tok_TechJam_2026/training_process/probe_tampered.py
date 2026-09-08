"""
BONUS PROBE — tampered images (SID_Set label 2).

The detector is trained binary: real vs FULLY synthetic. Tampered images are
real photographs with a locally edited region. They were never trained on.
This script asks a question the training objective never posed:

    does a global semantic detector generalise to local manipulation?

The expected answer is "partially, and only when the edited region is large" —
because CLIP resizes to 224x224, a 3% edited patch becomes roughly 7x7 pixels.
Measuring exactly where that boundary sits is the finding. Reporting it as a
limitation with a number attached is worth more than pretending it works.

    python probe_tampered.py --probe data/probe --ckpt runs/cvar/head.pt \
                             --out reports/tampered
    # optional: compare against fully-synthetic detection on the same model
    python probe_tampered.py ... --reference reports/test/scores.npz
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import torch
from sklearn.metrics import roc_auc_score

import augment as A
import clipfeat as CF
from train import Head

BINS = [(0.00, 0.02), (0.02, 0.05), (0.05, 0.10), (0.10, 0.20), (0.20, 0.40), (0.40, 1.01)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", required=True, help="root with real/ and fake/tampered/")
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--reference", default="",
                    help="scores.npz from evaluate.py on the synthetic test set, for comparison")
    ap.add_argument("--batch-size", type=int, default=4,
                    help="DataLoader batch size, in SAMPLES. This script always uses "
                         "EVAL_GRID + CHAIN_GRID (22 conditions), so actual images-per-batch "
                         "is batch_size * 22 -- kept low by default for the same VRAM reason "
                         "as evaluate.py. Raise it if you have VRAM to spare.")
    ap.add_argument("--num-workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    ck = torch.load(args.ckpt, map_location="cpu", weights_only=False)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    head = Head(ck["dim"], ck["config"]["head"], ck["config"]["hidden"], ck["config"]["dropout"])
    head.load_state_dict(ck["state_dict"])
    head.eval().to(device)
    mu, sd, thr = ck["mu"], ck["sd"], ck["threshold"]
    pa, pb = ck.get("platt_a", 1.0), ck.get("platt_b", 0.0)

    conds = A.EVAL_GRID + A.CHAIN_GRID
    samples = CF.scan_dir(args.probe)
    if args.limit:
        real = [s for s in samples if s.label == 0][: args.limit]
        fake = [s for s in samples if s.label == 1][: args.limit]
        samples = real + fake

    ds = CF.ViewDataset(samples, [CF.condition_view(c) for c in conds],
                        preproc=ck["preproc"], renormalise=True, seed=999)
    model, _ = CF.load_clip(ck["clip_model"], device)
    feats, labels = CF.embed(model, device, ds, feature=ck["feature"],
                             batch_size=args.batch_size, num_workers=args.num_workers)

    ok = labels >= 0
    paths = [s.path for s, k in zip(samples, ok) if k]
    y = np.array([s.label for s, k in zip(samples, ok) if k])
    X = feats[ok].astype(np.float32)
    if ck["l2"]:
        X = X / (np.linalg.norm(X, axis=-1, keepdims=True) + 1e-8)
    X = (X - mu) / sd

    scores = {}
    with torch.no_grad():
        for j, c in enumerate(conds):
            scores[c.name] = np.concatenate(
                [head(torch.from_numpy(X[i:i + 4096, j]).to(device)).cpu().numpy()
                 for i in range(0, X.shape[0], 4096)])

    real_m, tamp_m = y == 0, y == 1
    rep = ["# Bonus probe: tampered images (never trained on)\n",
           f"real: {int(real_m.sum())}   tampered: {int(tamp_m.sum())}\n",
           "Threshold and calibration are inherited unchanged from the binary "
           "real-vs-synthetic model. Nothing here was refitted.\n",
           "\n## Flag rate by condition\n",
           "| condition | flagged tampered (recall) | flagged real (FPR) | AUROC | mean P(fake) tampered |",
           "|---|---|---|---|---|"]
    for c in conds:
        s = scores[c.name]
        p_cal = 1.0 / (1.0 + np.exp(-(pa * s + pb)))
        auc = roc_auc_score(y, s) if len(set(y)) > 1 else float("nan")
        rep.append(f"| {c.name} | {(s[tamp_m] >= thr).mean():.4f} | "
                   f"{(s[real_m] >= thr).mean():.4f} | {auc:.4f} | {p_cal[tamp_m].mean():.3f} |")

    # the interesting part: does detection track how much of the frame was edited?
    mf_path = Path(args.probe) / "mask_fraction.json"
    if mf_path.exists():
        mf = json.loads(mf_path.read_text())
        frac = np.array([mf.get(Path(p).name, np.nan) for p in paths])
        clean = scores["clean"]
        rep.append("\n## Detection vs size of the edited region (clean condition)\n")
        rep.append("| edited fraction | n | recall @ deployed threshold | mean score |")
        rep.append("|---|---|---|---|")
        for lo, hi in BINS:
            m = tamp_m & (frac >= lo) & (frac < hi)
            if m.sum() < 5:
                continue
            rep.append(f"| {lo:.0%}–{hi:.0%} | {int(m.sum())} | "
                       f"{(clean[m] >= thr).mean():.4f} | {clean[m].mean():.3f} |")
        valid = tamp_m & np.isfinite(frac)
        if valid.sum() > 10:
            r = float(np.corrcoef(frac[valid], clean[valid])[0, 1])
            rep.append(f"\nPearson r between edited fraction and detector score: **{r:.3f}**")
            rep.append("A clearly positive r is the headline: the detector only sees a "
                       "manipulation once it is large enough to survive the 224px resize.")
    else:
        rep.append(f"\n_No {mf_path.name} found — re-run prepare_sid.py with --tampered "
                   "to record edited-region sizes._")

    if args.reference:
        ref = np.load(args.reference)
        ry, rs = ref["y"], ref["clean"]
        syn_recall = (rs[ry == 1] >= thr).mean()
        tam_recall = (scores["clean"][tamp_m] >= thr).mean()
        rep.append("\n## Transfer gap\n")
        rep.append(f"| task | recall @ same threshold |\n|---|---|")
        rep.append(f"| fully synthetic (trained) | {syn_recall:.4f} |")
        rep.append(f"| locally tampered (unseen task) | {tam_recall:.4f} |")
        rep.append(f"\nGap: **{syn_recall - tam_recall:+.4f}**. This is the honest cost of "
                   "scoping to image-level detection.")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "tampered_probe.md").write_text("\n".join(rep))
    np.savez(out / "probe_scores.npz", y=y, **scores)
    print("\n".join(rep))
    print(f"\n[save] {out}/tampered_probe.md")


if __name__ == "__main__":
    main()