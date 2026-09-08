"""
Train the detection head on cached CLIP features.

The "adversarial" part of this system, done honestly. There is no generator
network: you are not synthesising images, so a GAN has nothing to generate.
What you have instead is a min-max game over the degradation space —

    min_head  max_{t in T}  L(head(CLIP(t(x))), y)

The inner max is approximated by sampling k cached views per image and
backpropagating through the worst one. That is real adversarial training
against the threat model you were actually given (post-processing), and it is
cheap because the views are precomputed.

    python train.py --train cache/train --val cache/val --out runs/v1
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
from sklearn.metrics import roc_auc_score


def set_seed(seed: int) -> None:
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    np.random.seed(seed)


# --------------------------------------------------------------------------
# head
# --------------------------------------------------------------------------


class Head(nn.Module):
    def __init__(self, dim: int, kind: str = "mlp", hidden: int = 512, dropout: float = 0.3) -> None:
        super().__init__()
        if kind == "linear":
            self.net = nn.Linear(dim, 1)
        elif kind == "mlp":
            self.net = nn.Sequential(
                nn.Linear(dim, hidden),
                nn.BatchNorm1d(hidden),
                nn.ReLU(inplace=False),
                nn.Dropout(dropout),
                nn.Linear(hidden, hidden // 2),
                nn.BatchNorm1d(hidden // 2),
                nn.ReLU(inplace=False),
                nn.Dropout(dropout),
                nn.Linear(hidden // 2, 1),
            )
        else:
            raise ValueError(kind)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: (B, D) -> (B,)
        return self.net(x).squeeze(-1)


# --------------------------------------------------------------------------
# feature prep
# --------------------------------------------------------------------------


def load_cache(path: str) -> tuple[np.ndarray, np.ndarray, dict]:
    p = Path(path)
    feats = np.load(p / "feats.npy").astype(np.float32)   # (N, V, D)
    labels = np.load(p / "labels.npy")
    meta = json.loads((p / "meta.json").read_text())
    keep = labels >= 0
    return feats[keep], labels[keep], {**meta, "keep": keep.tolist()}


def prep(feats: np.ndarray, l2: bool, mu=None, sd=None):
    if l2:
        feats = feats / (np.linalg.norm(feats, axis=-1, keepdims=True) + 1e-8)
    if mu is None:
        flat = feats.reshape(-1, feats.shape[-1])
        mu, sd = flat.mean(0), flat.std(0) + 1e-6
    return (feats - mu) / sd, mu, sd


# --------------------------------------------------------------------------
# metrics
# --------------------------------------------------------------------------


def fpr_at_tpr(y: np.ndarray, s: np.ndarray, target_tpr: float = 0.95) -> float:
    """False-positive rate (real images called fake) at a given detection rate."""
    pos, neg = np.sort(s[y == 1]), s[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    thr = np.quantile(pos, 1.0 - target_tpr)
    return float((neg >= thr).mean())


@torch.no_grad()
def eval_views(head: nn.Module, X: torch.Tensor, y: np.ndarray, device: str) -> dict:
    """Score every cached view separately. X: (N, V, D)."""
    head.eval()
    n, v, d = X.shape
    scores = []
    for j in range(v):
        out = []
        for i in range(0, n, 4096):
            out.append(head(X[i:i + 4096, j].to(device)).cpu())
        scores.append(torch.cat(out).numpy())
    aucs = np.array([roc_auc_score(y, s) for s in scores], dtype=np.float64)
    pooled = np.concatenate(scores)
    ypool = np.tile(y, v)
    return {
        "auc_clean": float(aucs[0]),
        "auc_mean": float(aucs.mean()),
        "auc_worst": float(aucs.min()),
        "auc_pooled": float(roc_auc_score(ypool, pooled)),
        "fpr95_pooled": fpr_at_tpr(ypool, pooled, 0.95),
        "per_view_auc": [float(a) for a in aucs],
        "_scores": scores,
    }


def pick_threshold(scores: np.ndarray, y: np.ndarray, max_fpr: float) -> float:
    """Highest-recall threshold that keeps FPR on real images under max_fpr."""
    neg = np.sort(scores[y == 0])
    if len(neg) == 0:
        return 0.0
    return float(np.quantile(neg, 1.0 - max_fpr))


def fit_platt(scores: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """
    Fit sigmoid(a*logit + b) on validation scores.

    Needed because two things in training deliberately distort the output
    scale: pos_weight (class imbalance) and the CVaR objective (over-weights
    hard views). Raw sigmoid(logit) is a confidence score, not a probability.
    After this, sigmoid(a*logit + b) is safe to show a user as "% likely AI".
    """
    s = torch.from_numpy(scores.astype(np.float32))
    t = torch.from_numpy(y.astype(np.float32))
    a = torch.ones(1, requires_grad=True)
    b = torch.zeros(1, requires_grad=True)
    opt = torch.optim.LBFGS([a, b], lr=0.1, max_iter=200)

    def closure():
        opt.zero_grad()
        loss = nn.functional.binary_cross_entropy_with_logits(a * s + b, t)
        loss.backward()
        return loss

    opt.step(closure)
    return float(a.detach()), float(b.detach())


def expected_calibration_error(prob: np.ndarray, y: np.ndarray, bins: int = 15) -> float:
    edges = np.linspace(0.0, 1.0, bins + 1)
    idx = np.clip(np.digitize(prob, edges[1:-1]), 0, bins - 1)
    ece = 0.0
    for b in range(bins):
        m = idx == b
        if m.sum() == 0:
            continue
        ece += (m.mean()) * abs(prob[m].mean() - y[m].mean())
    return float(ece)


# --------------------------------------------------------------------------
# train
# --------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", required=True)
    ap.add_argument("--val", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--head", default="mlp", choices=["linear", "mlp"])
    ap.add_argument("--hidden", type=int, default=512)
    ap.add_argument("--dropout", type=float, default=0.3)
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--batch-size", type=int, default=256)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--wd", type=float, default=1e-4)
    ap.add_argument("--adv-views", type=int, default=3, help="k views sampled per image per step")
    ap.add_argument("--adv-mode", default="cvar", choices=["mean", "max", "cvar"],
                    help="mean = plain augmentation; max = worst-case; cvar = mean of worst half")
    ap.add_argument("--consistency", type=float, default=0.5,
                    help="weight on pulling degraded-view logits toward the clean-view logit")
    ap.add_argument("--no-l2", action="store_true")
    ap.add_argument("--max-fpr", type=float, default=0.01, help="operating point for threshold calibration")
    ap.add_argument("--holdout-group", default="", help="drop this generator from train (LOGO eval)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--init-from", default="",
                    help="warm-start from a previous head.pt instead of random init. "
                         "Use this when adding more data later rather than retraining from "
                         "scratch. CRITICAL: also reuses that checkpoint's mu/sd feature "
                         "normalisation -- the freshly computed mu/sd from THIS run's data "
                         "is discarded. Loading old weights but normalising with new "
                         "statistics would silently feed the warm-started head data in a "
                         "different space than it learned on, which is worse than starting "
                         "fresh. --head/--hidden/--dropout must match the checkpoint's "
                         "config, or the state_dict won't load.")
    args = ap.parse_args()

    set_seed(args.seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"

    init_ck = None
    if args.init_from:
        init_ck = torch.load(args.init_from, map_location="cpu", weights_only=False)
        print(f"[resume] warm-starting from {args.init_from} "
             f"(previous best val auc_worst not stored directly, see its history.json)")

    Xtr, ytr, mtr = load_cache(args.train)
    Xva, yva, mva = load_cache(args.val)

    if args.holdout_group:
        g = np.array([g for g, k in zip(mtr["group"], mtr["keep"]) if k])
        keep = g != args.holdout_group
        print(f"[logo] dropping group {args.holdout_group!r}: {len(g)} -> {int(keep.sum())}")
        Xtr, ytr = Xtr[keep], ytr[keep]

    if init_ck is not None:
        # Reuse the OLD normalisation, not fresh statistics from this run's
        # data -- see the --init-from help text for why this is not optional.
        mu, sd = init_ck["mu"], init_ck["sd"]
        Xtr, _, _ = prep(Xtr, l2=not args.no_l2, mu=mu, sd=sd)
        Xva, _, _ = prep(Xva, l2=not args.no_l2, mu=mu, sd=sd)
        print(f"[resume] reusing checkpoint's mu/sd (shape {mu.shape}) -- "
             "NOT recomputing from this run's data")
    else:
        Xtr, mu, sd = prep(Xtr, l2=not args.no_l2)
        Xva, _, _ = prep(Xva, l2=not args.no_l2, mu=mu, sd=sd)
    n, v, d = Xtr.shape
    print(f"[train] N={n} views={v} dim={d}  fake_frac={ytr.mean():.3f}")

    Xtr_t = torch.from_numpy(Xtr)
    Xva_t = torch.from_numpy(Xva)
    ytr_t = torch.from_numpy(ytr).float()

    head = Head(d, args.head, args.hidden, args.dropout).to(device)
    if init_ck is not None:
        head.load_state_dict(init_ck["state_dict"])
        print(f"[resume] loaded weights from {args.init_from}")
    opt = torch.optim.AdamW(head.parameters(), lr=args.lr, weight_decay=args.wd)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=args.epochs)
    pos_w = torch.tensor([(ytr == 0).sum() / max((ytr == 1).sum(), 1)], device=device)
    bce = nn.BCEWithLogitsLoss(reduction="none", pos_weight=pos_w)

    k = min(args.adv_views, v)
    best = -1.0
    best_state: dict[str, torch.Tensor] | None = None
    history: list[dict] = []

    for ep in range(args.epochs):
        head.train()
        perm = torch.randperm(n)
        tot = 0.0
        for i in range(0, n, args.batch_size):
            idx = perm[i:i + args.batch_size]
            if len(idx) < 2:                       # BatchNorm needs >1
                continue
            b = len(idx)
            xb = Xtr_t[idx].to(device)             # (b, V, D)
            yb = ytr_t[idx].to(device)             # (b,)

            vsel = torch.randint(0, v, (b, k), device=device)
            xs = torch.gather(xb, 1, vsel.unsqueeze(-1).expand(b, k, d))   # (b, k, D)
            # clean anchor rides in the same forward: one BatchNorm update per step
            xall = torch.cat([xb[:, :1], xs], dim=1)                       # (b, k+1, D)
            lall = head(xall.reshape(b * (k + 1), d)).view(b, k + 1)
            clean, logits = lall[:, 0], lall[:, 1:]                        # (b,), (b, k)

            per = bce(logits, yb.unsqueeze(1).expand(b, k))                # (b, k)
            if args.adv_mode == "mean":
                loss = per.mean()
            elif args.adv_mode == "max":
                loss = per.max(dim=1).values.mean()
            else:                                                          # cvar
                q = max(1, k // 2)
                loss = per.topk(q, dim=1).values.mean()

            if args.consistency > 0:
                anchor = clean.detach().unsqueeze(1)                       # (b, 1)
                loss = loss + args.consistency * ((logits - anchor) ** 2).mean()

            opt.zero_grad(set_to_none=True)
            loss.backward()
            nn.utils.clip_grad_norm_(head.parameters(), 1.0)
            opt.step()
            tot += loss.item() * b
        sched.step()

        m = eval_views(head, Xva_t, yva, device)
        history.append({"epoch": ep, "loss": tot / n, **{k2: m[k2] for k2 in
                        ("auc_clean", "auc_mean", "auc_worst", "fpr95_pooled")}})
        # Model selection on WORST view, not clean. This is the whole point.
        if m["auc_worst"] > best:
            best = m["auc_worst"]
            best_state = {kk: vv.detach().cpu().clone() for kk, vv in head.state_dict().items()}
        print(f"ep{ep:02d} loss={tot/n:.4f} clean={m['auc_clean']:.4f} "
              f"mean={m['auc_mean']:.4f} worst={m['auc_worst']:.4f} fpr@95={m['fpr95_pooled']:.4f}")

    if best_state is None:
        raise RuntimeError(
            f"no checkpoint was ever saved — --epochs was {args.epochs}, need >= 1"
        )
    head.load_state_dict(best_state)
    m = eval_views(head, Xva_t, yva, device)

    # Calibrate on POOLED augmented val scores, not clean ones. A threshold set
    # on clean data drifts as compression shifts the score distribution, which
    # is how detectors keep their AUROC and lose their accuracy.
    pooled = np.concatenate(m["_scores"])
    ypool = np.tile(yva, len(m["_scores"]))
    thr = pick_threshold(pooled, ypool, args.max_fpr)
    thr_clean = pick_threshold(m["_scores"][0], yva, args.max_fpr)
    print(f"\n[calib] threshold@{args.max_fpr:.1%}FPR  pooled={thr:.4f}  clean-only={thr_clean:.4f}  "
          f"(drift={thr-thr_clean:+.4f})")

    platt_a, platt_b = fit_platt(pooled, ypool)
    ece_raw = expected_calibration_error(1 / (1 + np.exp(-pooled)), ypool)
    ece_cal = expected_calibration_error(1 / (1 + np.exp(-(platt_a * pooled + platt_b))), ypool)
    print(f"[calib] platt a={platt_a:.4f} b={platt_b:.4f}   ECE {ece_raw:.4f} -> {ece_cal:.4f}")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    torch.save({
        "state_dict": head.state_dict(),
        "mu": mu, "sd": sd,
        "dim": d,
        "config": vars(args),
        "threshold": thr,
        "threshold_clean_only": thr_clean,
        "platt_a": platt_a,
        "platt_b": platt_b,
        "clip_model": mtr["model"],
        "feature": mtr["feature"],
        "preproc": mtr["preproc"],
        "l2": not args.no_l2,
    }, out / "head.pt")
    (out / "history.json").write_text(json.dumps(
        {"history": history, "best_val": {k2: m[k2] for k2 in m if k2 != "_scores"}}, indent=2))
    print(f"[save] {out}/head.pt   best val auc_worst={best:.4f}")


if __name__ == "__main__":
    main()