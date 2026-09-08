"""
Robustness evaluation.

Two metrics, deliberately. AUROC tells you whether the score still separates
the classes. Accuracy at a FIXED threshold tells you whether the deployed
system still works. These diverge badly under compression: the score
distribution slides down, AUROC barely moves, and a threshold calibrated on
clean data starts calling everything real. Reporting only AUROC hides this,
and it is the single most common flaw in hackathon detector write-ups.

    python evaluate.py --data data/test --ckpt runs/v1/head.pt --out reports/v1
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import torch
from sklearn.metrics import average_precision_score, roc_auc_score

import augment as A
import clipfeat as CF
from train import Head, fpr_at_tpr


def metrics(y: np.ndarray, s: np.ndarray, thr: float) -> dict:
    pred = (s >= thr).astype(int)
    real, fake = y == 0, y == 1
    return {
        "auroc": float(roc_auc_score(y, s)) if len(set(y)) > 1 else float("nan"),
        "ap": float(average_precision_score(y, s)) if len(set(y)) > 1 else float("nan"),
        "acc": float((pred == y).mean()),
        "tpr": float(pred[fake].mean()) if fake.any() else float("nan"),   # fakes caught
        "fpr": float(pred[real].mean()) if real.any() else float("nan"),   # reals wrongly accused
        "fpr@95tpr": fpr_at_tpr(y, s, 0.95),
        "score_mean_real": float(s[real].mean()) if real.any() else float("nan"),
        "score_mean_fake": float(s[fake].mean()) if fake.any() else float("nan"),
    }


def table(rows: list[dict], cols: list[str]) -> str:
    hdr = "| condition | " + " | ".join(cols) + " |"
    sep = "|---" * (len(cols) + 1) + "|"
    out = [hdr, sep]
    for r in rows:
        cells = []
        for c in cols:
            val = r.get(c)
            cells.append("-" if val is None or (isinstance(val, float) and np.isnan(val)) else f"{val:.4f}")
        out.append(f"| {r['condition']} | " + " | ".join(cells) + " |")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--batch-size", type=int, default=4,
                    help="DataLoader batch size, in SAMPLES. Each condition in --groups is "
                         "an extra view of every sample, so actual images-per-batch is "
                         "batch_size * n_conditions -- with --groups all (28 conditions), "
                         "even --batch-size 4 already means 112 images per loader batch. "
                         "Kept low by default (unlike embed.py's 32) because this is a "
                         "different memory shape: few conditions x this batch_size vs "
                         "embed.py's many views x its batch_size. Raise it if you have "
                         "VRAM to spare, especially with --groups grid/held/chain (fewer "
                         "conditions than 'all').")
    ap.add_argument("--num-workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--tta", type=int, default=0,
                    help="average the score over N native-resolution crops at test time")
    ap.add_argument("--groups", choices=["all", "held", "chain", "grid"], default="all")
    ap.add_argument("--no-amp", action="store_true",
                    help="DIAGNOSTIC: force full fp32 even on GPU (see embed.py --no-amp)")
    args = ap.parse_args()

    ck = torch.load(args.ckpt, map_location="cpu", weights_only=False)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    head = Head(ck["dim"], ck["config"]["head"], ck["config"]["hidden"], ck["config"]["dropout"])
    head.load_state_dict(ck["state_dict"])
    head.eval().to(device)
    mu, sd, thr = ck["mu"], ck["sd"], ck["threshold"]
    print(f"[ckpt] dim={ck['dim']} feature={ck['feature']} preproc={ck['preproc']} thr={thr:.4f}")

    conds = {"all": A.EVAL_GRID + A.HELDOUT_GRID + A.CHAIN_GRID,
             "grid": A.EVAL_GRID,
             "held": A.HELDOUT_GRID,
             "chain": A.CHAIN_GRID}[args.groups]

    samples = CF.scan_dir(args.data)
    if args.limit:
        real = [s for s in samples if s.label == 0][: args.limit]
        fake = [s for s in samples if s.label == 1][: args.limit]
        samples = real + fake
    y = np.array([s.label for s in samples])
    groups = np.array([s.group for s in samples])
    print(f"[data] {len(samples)} images across {len(set(groups))} groups")

    ds = CF.ViewDataset(
        samples,
        [CF.condition_view(c) for c in conds],
        preproc=ck["preproc"],
        renormalise=True,
        seed=12345,
    )
    model, _ = CF.load_clip(ck["clip_model"], device)
    feats, labels = CF.embed(model, device, ds, feature=ck["feature"],
                             batch_size=args.batch_size, num_workers=args.num_workers)

    ok = labels >= 0
    feats, y, groups = feats[ok], y[ok], groups[ok]
    X = feats.astype(np.float32)
    if ck["l2"]:
        X = X / (np.linalg.norm(X, axis=-1, keepdims=True) + 1e-8)
    X = (X - mu) / sd

    # score every condition
    scores = {}
    with torch.no_grad():
        for j, c in enumerate(conds):
            chunks = [head(torch.from_numpy(X[i:i + 4096, j]).to(device)).cpu().numpy()
                      for i in range(0, X.shape[0], 4096)]
            scores[c.name] = np.concatenate(chunks)

    rows, per_family = [], defaultdict(list)
    for c in conds:
        m = metrics(y, scores[c.name], thr)
        rows.append({"condition": c.name, "family": c.family, **m})
        per_family[c.family].append(m)

    cols = ["auroc", "acc", "tpr", "fpr", "fpr@95tpr"]
    clean = next(r for r in rows if r["condition"] == "clean")
    grid_rows = [r for r in rows if r["family"] in
                 {"jpeg", "blur", "resize", "noise", "color", "crop"}]

    report = []
    report.append("## Robustness grid (transforms named in the brief)\n")
    report.append(table(grid_rows, cols))
    report.append(f"\n**clean baseline**: AUROC {clean['auroc']:.4f}, acc {clean['acc']:.4f}, "
                  f"FPR {clean['fpr']:.4f}")
    if grid_rows:
        drop = clean["auroc"] - min(r["auroc"] for r in grid_rows)
        worst = min(grid_rows, key=lambda r: r["auroc"])
        report.append(f"**worst in-grid condition**: {worst['condition']} "
                      f"(AUROC {worst['auroc']:.4f}, drop {drop:.4f})")

    held = [r for r in rows if r["family"].endswith("_ood")]
    if held:
        report.append("\n## Extrapolation (severities/codecs never seen in training)\n")
        report.append(table(held, cols))

    chain = [r for r in rows if r["family"] == "chain"]
    if chain:
        report.append("\n## Composed redistribution chains\n")
        report.append(table(chain, cols))

    # per-generator, averaged over all conditions — where generalisation shows up
    report.append("\n## Per-generator (mean over all conditions)\n")
    gl = ["| group | n | mean AUROC-vs-real | mean recall@thr |", "|---|---|---|---|"]
    real_mask = y == 0
    for g in sorted(set(groups[y == 1])):
        gm = groups == g
        aucs, recs = [], []
        for c in conds:
            s = scores[c.name]
            sub_y = np.concatenate([np.zeros(real_mask.sum()), np.ones(gm.sum())])
            sub_s = np.concatenate([s[real_mask], s[gm]])
            aucs.append(roc_auc_score(sub_y, sub_s))
            recs.append((s[gm] >= thr).mean())
        gl.append(f"| {g} | {int(gm.sum())} | {np.mean(aucs):.4f} | {np.mean(recs):.4f} |")
    report.append("\n".join(gl))

    # calibration: does the displayed probability mean anything?
    pa, pb = ck.get("platt_a", 1.0), ck.get("platt_b", 0.0)
    from train import expected_calibration_error
    report.append("\n## Calibration (Platt-scaled probabilities fitted on val)\n")
    cl = ["| condition | ECE raw | ECE calibrated | mean P(fake) real | mean P(fake) fake |",
          "|---|---|---|---|---|"]
    for c in conds:
        s = scores[c.name]
        p_raw = 1.0 / (1.0 + np.exp(-s))
        p_cal = 1.0 / (1.0 + np.exp(-(pa * s + pb)))
        cl.append(f"| {c.name} | {expected_calibration_error(p_raw, y):.4f} | "
                  f"{expected_calibration_error(p_cal, y):.4f} | "
                  f"{p_cal[y==0].mean():.3f} | {p_cal[y==1].mean():.3f} |")
    report.append("\n".join(cl))

    # threshold drift: the diagnostic judges rarely see
    report.append("\n## Score drift under degradation\n")
    dl = ["| condition | mean score (real) | mean score (fake) | margin |", "|---|---|---|---|"]
    for r in rows:
        margin = r["score_mean_fake"] - r["score_mean_real"]
        dl.append(f"| {r['condition']} | {r['score_mean_real']:.3f} | "
                  f"{r['score_mean_fake']:.3f} | {margin:.3f} |")
    report.append("\n".join(dl))

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text("\n".join(report))
    (out / "metrics.json").write_text(json.dumps(rows, indent=2))
    np.savez(out / "scores.npz", y=y, groups=groups, **scores)

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        names = [r["condition"] for r in rows]
        mat = np.array([[r["auroc"], r["acc"], r["tpr"], 1 - r["fpr"]] for r in rows])
        fig, ax = plt.subplots(figsize=(6, 0.32 * len(names) + 1.5))
        im = ax.imshow(mat, vmin=0.5, vmax=1.0, cmap="RdYlGn", aspect="auto")
        ax.set_xticks(range(4), ["AUROC", "acc", "TPR", "1-FPR"])
        ax.set_yticks(range(len(names)), names, fontsize=7)
        for i in range(mat.shape[0]):
            for j in range(mat.shape[1]):
                ax.text(j, i, f"{mat[i,j]:.2f}", ha="center", va="center", fontsize=6)
        fig.colorbar(im, ax=ax, shrink=0.6)
        fig.tight_layout()
        fig.savefig(out / "robustness_grid.png", dpi=160)
        print(f"[plot] {out}/robustness_grid.png")
    except Exception as e:
        print(f"[plot] skipped ({e})")

    print("\n".join(report))
    print(f"\n[save] {out}/report.md")


if __name__ == "__main__":
    main()