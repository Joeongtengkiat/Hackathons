"""Ensemble of CLIP-head bundles that share one frozen tower.

Several bundles from the same training pipeline carry the *same* frozen CLIP
tower and differ only in the trained head, its feature standardisation and its
Platt calibration. So an ensemble does not need to load or run the tower N
times: it embeds each image once and runs every head on that single embedding,
then averages the calibrated probabilities. The cost over a single model is a
few small matrix multiplies, not another 300M-parameter forward pass.

    python detect.py <dir> --detector ensemble --weights models/bundle.pt,models/bundle_cvar.pt
    python robustness.py <dir> --detector ensemble --weights models/a.pt,models/b.pt

Averaging calibrated probabilities (not logits) is deliberate: each bundle's
Platt fit maps its own logits onto a probability, and only after that are the
members on a common scale worth averaging. The ensemble's own operating point
is the mean of the members' - a starting point, not a calibrated one; run
calibrate.py on held-out data to set a threshold for the averaged score.

All members must share the tower config and the feature/preproc/l2 settings of
the first bundle; a member that disagrees is skipped with a warning rather than
silently mixing incompatible embeddings.
"""

from __future__ import annotations

import os

import numpy as np

from .base import register
from .clip_head import ClipHeadDetector


def _parse_paths(weights) -> list:
    """Split a comma-separated --weights into individual bundle paths."""
    if not weights:
        return []
    return [p.strip() for p in str(weights).split(",") if p.strip()]


@register
class EnsembleDetector(ClipHeadDetector):
    name = "ensemble"                           # --detector ensemble
    display_name = "CLIP tower + head ensemble"
    description = (
        "Averages several CLIP-head bundles that share the frozen tower: one "
        "embedding per image, every head scored on it, calibrated probabilities "
        "averaged. Point --weights at a comma-separated list of bundles."
    )
    requires_weights = True
    # No sensible default file: an ensemble is a set of bundles the caller
    # names. Empty means the picker shows it as "needs weights" until then.
    default_weights = ""

    def __init__(self):
        super().__init__()
        self.members = []          # [{head, mu, sd, platt_a, platt_b}]

    # -- readiness ---------------------------------------------------------
    @classmethod
    def is_ready(cls, weights: str = None) -> bool:
        """Ready when every named bundle exists. Needs an explicit --weights."""
        paths = _parse_paths(weights if weights is not None else cls.default_weights)
        return bool(paths) and all(os.path.isfile(p) for p in paths)

    # -- lifecycle ---------------------------------------------------------
    def load(self) -> None:
        """Build the shared tower from the first bundle, then every head.

        Deliberately does not call ClipHeadDetector.load: that loads a single
        bundle into one head, and the whole point here is many heads on one
        tower.
        """
        paths = _parse_paths(self.weights)
        if not paths:
            raise SystemExit(
                "error: the ensemble needs bundles - pass --weights with a "
                "comma-separated list, e.g. models/bundle.pt,models/bundle_cvar.pt")
        missing = [p for p in paths if not os.path.isfile(p)]
        if missing:
            raise SystemExit("error: ensemble bundle(s) not found: "
                             + ", ".join(os.path.abspath(p) for p in missing))

        try:
            import torch
        except ImportError:                       # pragma: no cover - env-dependent
            raise SystemExit("error: torch is required to run this detector "
                             "(pip install -r requirements.txt)")
        try:
            from transformers import CLIPVisionConfig, CLIPVisionModelWithProjection
        except ImportError:                       # pragma: no cover - env-dependent
            raise SystemExit("error: transformers is required to run this detector "
                             "(pip install -r requirements.txt)")
        from .clip_head import _head_class

        self._torch = torch
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.note(f"ensemble of {len(paths)} bundles · device "
                  f"{str(self.device).upper()}")

        # -- the shared tower, from the first bundle --------------------------
        first = torch.load(paths[0], map_location="cpu", weights_only=False)
        self.feature = first.get("feature", "proj")
        self.preproc = first.get("preproc", "resize")
        self.l2 = bool(first.get("l2", True))

        cfg = CLIPVisionConfig(**first["clip_config"])
        clip = CLIPVisionModelWithProjection(cfg)
        clip.load_state_dict(first["clip_state_dict"], strict=True)
        clip.eval().to(self.device)
        for p in clip.parameters():
            p.requires_grad_(False)
        self.clip = clip
        self.note(f"shared tower ready · feature={self.feature}")

        # -- one head per bundle, all reading the same embedding --------------
        thresholds = []
        for path in paths:
            # the first bundle is already in memory; re-read the rest
            bundle = first if path == paths[0] else torch.load(
                path, map_location="cpu", weights_only=False)

            # a member trained on a different embedding cannot share this tower's
            # features - skip it loudly rather than average nonsense
            if (bundle.get("feature", "proj") != self.feature
                    or bool(bundle.get("l2", True)) != self.l2):
                self.note(f"! skipping {os.path.basename(path)}: feature/l2 "
                          f"differs from the first bundle")
                continue

            hc = bundle.get("head_config") or {}
            hidden = int(hc.get("hidden", 512))
            head = _head_class(torch)(
                int(bundle["head_dim"]), hc.get("head", "mlp"),
                hidden, float(hc.get("dropout", 0.3)))
            head.load_state_dict(bundle["head_state_dict"], strict=True)
            head.eval().to(self.device)

            platt_a = float(bundle.get("platt_a", 1.0))
            platt_b = float(bundle.get("platt_b", 0.0))
            thr_logit = float(bundle.get("threshold", 0.0))
            self.members.append({
                "head": head,
                "mu": torch.as_tensor(bundle["mu"], dtype=torch.float32).to(self.device),
                "sd": torch.as_tensor(bundle["sd"], dtype=torch.float32).to(self.device),
                "platt_a": platt_a,
                "platt_b": platt_b,
            })
            thresholds.append(1.0 / (1.0 + np.exp(-(platt_a * thr_logit + platt_b))))
            self.note(f"loaded head from {os.path.basename(path)}")

        if not self.members:
            raise SystemExit("error: no compatible bundles in the ensemble - "
                             "every member disagreed with the first on feature/l2.")

        # a starting operating point only: the mean of the members'. The averaged
        # score has its own distribution, so recalibrate with calibrate.py.
        self.default_threshold = float(np.mean(thresholds))
        self.note(f"ensemble ready · {len(self.members)} heads · "
                  f"operating point {self.default_threshold:.3f} (recalibrate me)")

    def unload(self) -> None:
        self.clip = None
        self.members = []

    # -- inference ---------------------------------------------------------
    def predict_images(self, images: list) -> list:
        """Embed each image once, score every head on it, average the probs."""
        if not images:
            return []
        self.ensure_loaded()
        torch = self._torch

        arrays = [self._to_clip_array(img) for img in images]
        bs = max(1, int(self.batch_size))
        out = []
        for start in range(0, len(arrays), bs):
            batch = torch.from_numpy(
                np.stack(arrays[start:start + bs])).to(self.device)
            with torch.no_grad():
                feats = self._embed(batch)
                if self.l2:
                    feats = feats / (feats.norm(dim=-1, keepdim=True) + 1e-8)
                # standardise per member (each has its own mu/sd), score, average
                stack = None
                for mem in self.members:
                    f = (feats - mem["mu"]) / mem["sd"]
                    logit = mem["head"](f)
                    p = torch.sigmoid(mem["platt_a"] * logit + mem["platt_b"])
                    stack = p if stack is None else stack + p
                avg = stack / len(self.members)
            out.extend(round(float(p), 6) for p in avg.float().cpu())
        return out
