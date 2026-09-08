"""
Recalibrate an existing head.pt's THRESHOLD without retraining anything.

Why this exists: train.py defaults to --max-fpr 0.01, which picks the
threshold that keeps false accusations of real images under 1%. That's a
deliberate, defensible choice for some deployment contexts (moderation
queues, anything where a false accusation is expensive) -- but it directly
trades away recall on fakes to protect that budget. If your evaluation
report shows high AUROC (the scores rank real vs fake well) alongside low
accuracy and low TPR with near-zero FPR, that's this exact tradeoff showing
up, not a modelling failure. The fix is a different threshold on the SAME
scores, not a different model.

Three modes:
  acc      maximise raw accuracy on the pooled (augmented) val scores
  balanced maximise balanced accuracy, 0.5*(TPR+TNR) -- use this if your
           real/fake counts are imbalanced, since raw accuracy on an
           imbalanced set is a bit of a cheat metric otherwise
  fpr      the original train.py behaviour, for comparison (--max-fpr still
           applies here)

Never overwrites the original checkpoint -- always writes to --out.

    python recalibrate.py --val cache/val --ckpt runs/cvar_v2/head.pt \
                          --out runs/cvar_v2/head_balanced.pt --mode balanced
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import torch

from train import Head, eval_views, load_cache, pick_threshold, prep


def best_threshold(scores: np.ndarray, y: np.ndarray, mode: str, max_fpr: float) -> float:
    if mode == "fpr":
        return pick_threshold(scores, y, max_fpr)

    # Scan every distinct score as a candidate threshold. With val-set sizes
    # in the thousands this is milliseconds -- no need for anything fancier.
    candidates = np.unique(scores)
    best_val, best_thr = -1.0, candidates[0]
    for thr in candidates:
        pred = scores >= thr
        tp = (pred & (y == 1)).sum()
        tn = ((~pred) & (y == 0)).sum()
        fp = (pred & (y == 0)).sum()
        fn = ((~pred) & (y == 1)).sum()
        if mode == "acc":
            val = (tp + tn) / len(y)
        elif mode == "balanced":
            tpr = tp / max(tp + fn, 1)
            tnr = tn / max(tn + fp, 1)
            val = 0.5 * (tpr + tnr)
        else:
            raise ValueError(f"unknown mode {mode!r}")
        if val > best_val:
            best_val, best_thr = val, thr
    return float(best_thr)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--val", required=True)
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--mode", default="balanced", choices=["acc", "balanced", "fpr"])
    ap.add_argument("--max-fpr", type=float, default=0.01, help="only used if --mode fpr")
    args = ap.parse_args()

    ck = torch.load(args.ckpt, map_location="cpu", weights_only=False)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    head = Head(ck["dim"], ck["config"]["head"], ck["config"]["hidden"], ck["config"]["dropout"])
    head.load_state_dict(ck["state_dict"])
    head.eval().to(device)

    Xva, yva, _ = load_cache(args.val)
    Xva, _, _ = prep(Xva, l2=ck["l2"], mu=ck["mu"], sd=ck["sd"])
    Xva_t = torch.from_numpy(Xva)

    m = eval_views(head, Xva_t, yva, device)
    pooled = np.concatenate(m["_scores"])
    ypool = np.tile(yva, len(m["_scores"]))

    old_thr = ck["threshold"]
    new_thr = best_threshold(pooled, ypool, args.mode, args.max_fpr)

    def report(thr: float, label: str) -> None:
        pred = pooled >= thr
        acc = (pred == ypool).mean()
        tpr = pred[ypool == 1].mean()
        fpr = pred[ypool == 0].mean()
        bal = 0.5 * (tpr + (1 - fpr))
        print(f"  {label:10s} thr={thr:+.4f}  acc={acc:.4f}  balanced_acc={bal:.4f}  "
             f"TPR={tpr:.4f}  FPR={fpr:.4f}")

    print(f"[recalibrate] pooled val: {len(ypool)} scored views "
         f"({ypool.mean():.1%} fake)\n")
    report(old_thr, "old (fpr)")
    report(new_thr, f"new ({args.mode})")

    ck["threshold"] = new_thr
    ck["threshold_clean_only"] = best_threshold(m["_scores"][0], yva, args.mode, args.max_fpr)
    ck["threshold_mode"] = args.mode
    # NOTE: Platt scaling (platt_a/platt_b) is a global fit of P(fake) vs
    # score and does not depend on where the decision threshold sits -- it
    # stays valid unchanged. Only the threshold itself needed recomputing.

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    torch.save(ck, out)
    print(f"\n[save] {out}  (threshold {old_thr:+.4f} -> {new_thr:+.4f}, mode={args.mode})")
    print("Nothing about the model's weights changed -- rerun evaluate.py "
         "with this checkpoint to see the corrected accuracy/TPR/FPR numbers.")


if __name__ == "__main__":
    main()