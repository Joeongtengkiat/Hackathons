"""
Evaluate on the official validation benchmark (COCO val2017 + DALL-E Advanced).

WHAT THIS IS FOR. The organisers' benchmark, plus whatever hidden test set
the judges use, both draw real images from corpora this model never trained
on. Prior experiments here found that the REAL-image distribution dominates
transfer far more than the synthetic side does -- four external datasets
(pool, COCOAI x2, SID_Set) all failed to transfer, and all four differed from
the training data on the real side. COCO val2017 reals are the same corpus
that scored 0.787 in the COCOAI experiment.

So treat this benchmark as a preview of that exact risk. A weak result here
is a genuine early warning about the hidden test set, not a scoring quirk.

TWO THINGS THIS HANDLES THAT evaluate.py DOES NOT:

1. CLASS IMBALANCE. The benchmark is ~4998 real vs ~8843 fake -- roughly
   1:1.77. Raw accuracy is misleading on an imbalanced set (calling
   everything fake scores 64%), so balanced accuracy is reported alongside
   it and should be the number you quote.

2. THRESHOLD SENSITIVITY. --sweep re-derives the optimal threshold ON this
   benchmark and reports what you WOULD have scored with it. That is a
   diagnostic, not a submission tactic: it separates "the model cannot
   separate these classes" (bad, AUROC is low) from "the model separates
   them but the threshold is mis-set for this distribution" (fixable, AUROC
   is high). Tuning a threshold on a test set and then reporting that number
   as your result would be dishonest -- report the deployed-threshold number.

Expected folder layout (rename the organisers' folders to match):

    bench/real/coco_val2017/*.jpg
    bench/fake/dalle_advanced/*.jpg

    python benchmark_official.py --data bench --ckpt runs/merged/head_balanced.pt
    python benchmark_official.py --data bench --ckpt runs/merged/head_balanced.pt --sweep
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import torch
from sklearn.metrics import average_precision_score, roc_auc_score

import augment as A
import clipfeat as CF
from train import Head, fpr_at_tpr


def metrics_at(y: np.ndarray, s: np.ndarray, thr: float) -> dict:
    pred = (s >= thr).astype(int)
    real, fake = y == 0, y == 1
    tpr = float(pred[fake].mean()) if fake.any() else float("nan")
    fpr = float(pred[real].mean()) if real.any() else float("nan")
    return {
        "threshold": float(thr),
        "accuracy": float((pred == y).mean()),
        "balanced_accuracy": float(0.5 * (tpr + (1.0 - fpr))),
        "tpr_recall_on_fakes": tpr,
        "fpr_on_reals": fpr,
    }


def best_balanced_threshold(y: np.ndarray, s: np.ndarray) -> float:
    order = np.argsort(s)
    ys = y[order]
    n_pos, n_neg = int((y == 1).sum()), int((y == 0).sum())
    # sweeping thresholds from low to high: everything >= thr predicted fake
    tp = n_pos
    fp = n_neg
    best_val, best_thr = -1.0, float(s.min() - 1.0)
    for i in range(len(ys)):
        if ys[i] == 1:
            tp -= 1
        else:
            fp -= 1
        tpr = tp / max(n_pos, 1)
        tnr = 1.0 - fp / max(n_neg, 1)
        val = 0.5 * (tpr + tnr)
        if val > best_val:
            best_val = val
            best_thr = float(s[order[i]])
    return best_thr


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="root with real/ and fake/ subdirs")
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--out", default="reports/benchmark")
    ap.add_argument("--batch-size", type=int, default=8)
    ap.add_argument("--num-workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0, help="cap per class, for a quick check")
    ap.add_argument("--robustness", action="store_true",
                    help="also score the full degradation grid (much slower)")
    ap.add_argument("--sweep", action="store_true",
                    help="diagnostic: report what the best-possible threshold on THIS "
                         "set would have scored. Do not report that as your result.")
    args = ap.parse_args()

    ck = torch.load(args.ckpt, map_location="cpu", weights_only=False)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    head = Head(ck["dim"], ck["config"]["head"], ck["config"]["hidden"], ck["config"]["dropout"])
    head.load_state_dict(ck["state_dict"])
    head.eval().to(device)
    mu, sd, thr = ck["mu"], ck["sd"], ck["threshold"]
    pa, pb = ck.get("platt_a", 1.0), ck.get("platt_b", 0.0)
    print(f"[ckpt] {args.ckpt}  dim={ck['dim']} deployed_threshold={thr:.4f}")

    samples = CF.scan_dir(args.data)
    if args.limit:
        real = [s for s in samples if s.label == 0][: args.limit]
        fake = [s for s in samples if s.label == 1][: args.limit]
        samples = real + fake

    n_real = sum(1 for s in samples if s.label == 0)
    n_fake = len(samples) - n_real
    print(f"[data] {len(samples)} images  real={n_real}  fake={n_fake}  "
         f"(ratio 1:{n_fake/max(n_real,1):.2f})")

    conds = (A.EVAL_GRID + A.HELDOUT_GRID + A.CHAIN_GRID) if args.robustness \
        else [c for c in A.EVAL_GRID if c.name == "clean"]

    ds = CF.ViewDataset(samples, [CF.condition_view(c) for c in conds],
                        preproc=ck["preproc"], renormalise=True, seed=4242)
    model, _ = CF.load_clip(ck["clip_model"], device)
    feats, labels = CF.embed(model, device, ds, feature=ck["feature"],
                             batch_size=args.batch_size, num_workers=args.num_workers)

    ok = labels >= 0
    y = labels[ok]
    X = feats[ok].astype(np.float32)
    if ck["l2"]:
        X = X / (np.linalg.norm(X, axis=-1, keepdims=True) + 1e-8)
    X = (X - mu) / sd

    lines: list[str] = ["# Official validation benchmark\n",
                       f"checkpoint: `{args.ckpt}`\n",
                       f"images: {len(y)} (real={int((y==0).sum())}, "
                       f"fake={int((y==1).sum())})\n"]

    all_rows = []
    with torch.no_grad():
        for j, c in enumerate(conds):
            s = np.concatenate([
                head(torch.from_numpy(X[i:i + 4096, j]).to(device)).cpu().numpy()
                for i in range(0, X.shape[0], 4096)
            ])
            auroc = float(roc_auc_score(y, s)) if len(set(y)) > 1 else float("nan")
            ap_score = float(average_precision_score(y, s)) if len(set(y)) > 1 else float("nan")
            m = metrics_at(y, s, thr)
            row = {"condition": c.name, "auroc": auroc, "ap": ap_score,
                   "fpr@95tpr": fpr_at_tpr(y, s, 0.95), **m}
            if args.sweep:
                sw_thr = best_balanced_threshold(y, s)
                sw = metrics_at(y, s, sw_thr)
                row["swept_threshold"] = sw["threshold"]
                row["swept_balanced_accuracy"] = sw["balanced_accuracy"]
                row["swept_accuracy"] = sw["accuracy"]
            all_rows.append(row)

            if c.name == "clean":
                p_cal = 1.0 / (1.0 + np.exp(-(pa * s + pb)))
                lines.append("\n## Headline (clean images, deployed threshold)\n")
                lines.append(f"- **AUROC**: {auroc:.4f}")
                lines.append(f"- **Balanced accuracy**: {m['balanced_accuracy']:.4f}  "
                            "<- quote this, not raw accuracy (the set is imbalanced)")
                lines.append(f"- Raw accuracy: {m['accuracy']:.4f}")
                lines.append(f"- Recall on fakes (TPR): {m['tpr_recall_on_fakes']:.4f}")
                lines.append(f"- False positives on reals (FPR): {m['fpr_on_reals']:.4f}")
                lines.append(f"- Average precision: {ap_score:.4f}")
                lines.append(f"\nmean P(fake): real={p_cal[y==0].mean():.3f}  "
                            f"fake={p_cal[y==1].mean():.3f}")
                lines.append(f"\nmean score: real={s[y==0].mean():.3f}  "
                            f"fake={s[y==1].mean():.3f}  "
                            f"margin={s[y==1].mean()-s[y==0].mean():.3f}")
                if args.sweep:
                    lines.append(f"\n### Threshold diagnostic (NOT your reportable score)\n")
                    lines.append(f"Best achievable balanced accuracy on this set: "
                                f"**{row['swept_balanced_accuracy']:.4f}** "
                                f"at threshold {row['swept_threshold']:.4f} "
                                f"(deployed threshold is {thr:.4f}).")
                    gap = row["swept_balanced_accuracy"] - m["balanced_accuracy"]
                    if gap > 0.05:
                        lines.append(f"\nGap of {gap:.4f} means the model SEPARATES these "
                                    "classes better than the deployed threshold captures. "
                                    "The threshold is mis-set for this distribution. "
                                    "Recalibrating on data resembling this benchmark "
                                    "would legitimately recover most of that gap.")
                    else:
                        lines.append(f"\nGap of only {gap:.4f} means the threshold is close "
                                    "to optimal here -- the limit is the model's separation "
                                    "(AUROC), not calibration.")

    if args.robustness and len(all_rows) > 1:
        lines.append("\n## Robustness grid\n")
        lines.append("| condition | AUROC | balanced acc | acc | TPR | FPR |")
        lines.append("|---|---|---|---|---|---|")
        for r in all_rows:
            lines.append(f"| {r['condition']} | {r['auroc']:.4f} | "
                        f"{r['balanced_accuracy']:.4f} | {r['accuracy']:.4f} | "
                        f"{r['tpr_recall_on_fakes']:.4f} | {r['fpr_on_reals']:.4f} |")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "benchmark.md").write_text("\n".join(lines), encoding="utf-8")
    (out / "metrics.json").write_text(json.dumps(all_rows, indent=2))

    print("\n".join(lines))
    print(f"\n[save] {out}/benchmark.md")


if __name__ == "__main__":
    main()