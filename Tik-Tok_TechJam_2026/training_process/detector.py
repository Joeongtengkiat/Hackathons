"""
Inference API for the GUI. Import this; don't reimplement scoring.

Designed for the folder-scanning use case: load the model ONCE when the app
starts, then call predict_folder() (or predict_batch()) as many times as you
like. Batched on the GPU, so a folder of a few hundred images takes seconds
rather than one-at-a-time minutes.

    from detector import Detector

    det = Detector("runs/merged/bundle.pt")        # once, at app startup
    results = det.predict_folder("C:/some/folder")

    for r in results:
        print(r["path"], r["verdict"], r["probability_fake"])

Each result is a dict:
    path              str    absolute path to the image
    verdict           str    "fake" or "real"
    probability_fake  float  0..1, Platt-calibrated
    score             float  raw logit (threshold is applied to THIS)
    error             str    present ONLY if the image failed to load

A note on the probability. It is calibrated (Platt-scaled on validation
data), so it is meaningful to display -- but it is a confidence estimate on
data resembling the training distribution, not a guarantee. Images unlike
anything trained on (very different content types, unusual processing) can
produce confident-looking numbers that are not trustworthy. Consider showing
the verdict prominently and the probability as secondary detail.

Progress reporting for a GUI: pass a callback to predict_folder(), e.g.

    det.predict_folder(folder, progress=lambda done, total: bar.set(done/total))
"""
from __future__ import annotations

import random
from pathlib import Path
from typing import Callable, Iterable, Sequence

import numpy as np
import torch
from PIL import Image
from transformers import CLIPVisionConfig, CLIPVisionModelWithProjection

import augment as A
import clipfeat as CF
from train import Head

IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}

# Same cap as clipfeat.ViewDataset: reject absurd dimensions before decoding,
# since Pillow's decompression-bomb guard is disabled for this pipeline.
MAX_PIXELS = 200_000_000


class Detector:
    """Loads a bundle once; scores images in batches."""

    def __init__(self, bundle_path: str, device: str | None = None,
                 renormalise: bool = True) -> None:
        """
        renormalise: apply augment.normalise_source() -- a JPEG re-encode at
        random quality 85-98 -- before scoring, matching how the model was
        TRAINED and BENCHMARKED (clipfeat.ViewDataset does this with
        renormalise=True, which every training and evaluation run used).

        Default True so inference matches training. Setting it False scores
        the file exactly as given, which sounds more "honest" but introduces
        a train/inference mismatch: the model never saw un-re-encoded inputs
        during training, and every reported metric was measured with this
        step applied. If you turn it off, your GUI's behaviour no longer
        corresponds to any number you have measured.
        """
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.renormalise = renormalise
        b = torch.load(bundle_path, map_location="cpu", weights_only=True)

        clip_config = CLIPVisionConfig(**b["clip_config"])
        self.clip = CLIPVisionModelWithProjection(clip_config)
        self.clip.load_state_dict(b["clip_state_dict"])
        self.clip.eval().to(self.device)
        for p in self.clip.parameters():
            p.requires_grad_(False)

        self.head = Head(b["head_dim"], b["head_config"]["head"],
                        b["head_config"]["hidden"], b["head_config"]["dropout"])
        self.head.load_state_dict(b["head_state_dict"])
        self.head.eval().to(self.device)

        self.mu = b["mu"].numpy()
        self.sd = b["sd"].numpy()
        self.threshold = float(b["threshold"])
        self.platt_a = float(b.get("platt_a", 1.0))
        self.platt_b = float(b.get("platt_b", 0.0))
        self.feature = b["feature"]
        self.preproc = b["preproc"]
        self.l2 = b["l2"]
        self._rng = random.Random(0)

    # ----------------------------------------------------------------
    # internals
    # ----------------------------------------------------------------

    def _load_tensor(self, path: str) -> torch.Tensor | None:
        try:
            raw = Image.open(path)            # header only
            w, h = raw.size
            if w * h > MAX_PIXELS:
                return None
            img = raw.convert("RGB")          # decode
            if self.renormalise:
                # Seeded from the path so the same file always scores the
                # same -- normalise_source picks a random JPEG quality, and
                # an unseeded draw would make the GUI give slightly
                # different answers for the same image on repeat scans.
                seed = abs(hash(Path(path).name)) % (2**31 - 1)
                img = A.normalise_source(img, np.random.default_rng(seed),
                                         random.Random(seed))
            return CF.to_clip_tensor(img, self.preproc, self._rng)
        except Exception:
            return None

    @torch.no_grad()
    def _score_tensors(self, batch: torch.Tensor) -> np.ndarray:
        x = batch.to(self.device)
        pooled = self.clip.vision_model(pixel_values=x).pooler_output
        if self.feature == "pooled":
            feat = pooled
        elif self.feature == "proj":
            feat = self.clip.visual_projection(pooled)
        else:
            feat = torch.cat([pooled, self.clip.visual_projection(pooled)], dim=-1)

        f = feat.float().cpu().numpy()
        if self.l2:
            f = f / (np.linalg.norm(f, axis=-1, keepdims=True) + 1e-8)
        f = (f - self.mu) / self.sd
        return self.head(torch.from_numpy(f).float().to(self.device)).cpu().numpy()

    def _finalise(self, path: str, score: float) -> dict:
        prob = 1.0 / (1.0 + np.exp(-(self.platt_a * score + self.platt_b)))
        return {
            "path": path,
            "verdict": "fake" if score >= self.threshold else "real",
            "probability_fake": float(prob),
            "score": float(score),
        }

    # ----------------------------------------------------------------
    # public API
    # ----------------------------------------------------------------

    def predict_batch(self, paths: Sequence[str], batch_size: int = 16) -> list[dict]:
        """Score an explicit list of image paths. Order of results matches input."""
        results: list[dict | None] = [None] * len(paths)
        pending_idx: list[int] = []
        pending_t: list[torch.Tensor] = []

        def flush() -> None:
            if not pending_t:
                return
            scores = self._score_tensors(torch.stack(pending_t, 0))
            for i, s in zip(pending_idx, scores):
                results[i] = self._finalise(str(paths[i]), float(s))
            pending_idx.clear()
            pending_t.clear()

        for i, p in enumerate(paths):
            t = self._load_tensor(str(p))
            if t is None:
                results[i] = {"path": str(p), "verdict": "error",
                             "probability_fake": float("nan"), "score": float("nan"),
                             "error": "could not read image"}
                continue
            pending_idx.append(i)
            pending_t.append(t)
            if len(pending_t) >= batch_size:
                flush()
        flush()

        return [r for r in results if r is not None]

    def predict_folder(
        self,
        folder: str,
        batch_size: int = 16,
        recursive: bool = True,
        progress: Callable[[int, int], None] | None = None,
    ) -> list[dict]:
        """
        Score every image in a folder.

        progress: optional callback(done, total) -- call it from the GUI to
        drive a progress bar. Runs on the calling thread, so if the GUI is
        single-threaded, run this in a worker thread to avoid freezing the UI.
        """
        root = Path(folder)
        it: Iterable[Path] = root.rglob("*") if recursive else root.glob("*")
        paths = sorted(p for p in it if p.is_file() and p.suffix.lower() in IMG_EXT)

        total = len(paths)
        if total == 0:
            return []

        out: list[dict] = []
        for start in range(0, total, batch_size):
            chunk = paths[start:start + batch_size]
            out.extend(self.predict_batch(chunk, batch_size=batch_size))
            if progress:
                progress(min(start + batch_size, total), total)
        return out

    def summary(self, results: Sequence[dict]) -> dict:
        """Counts for a GUI status line."""
        fake = sum(1 for r in results if r["verdict"] == "fake")
        real = sum(1 for r in results if r["verdict"] == "real")
        err = sum(1 for r in results if r["verdict"] == "error")
        return {"total": len(results), "fake": fake, "real": real, "errors": err}


def main() -> None:
    import argparse
    import json

    ap = argparse.ArgumentParser(description="Score a folder of images.")
    ap.add_argument("--bundle", required=True)
    ap.add_argument("--folder", required=True)
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--json-out", default="")
    ap.add_argument("--no-renormalise", action="store_true",
                    help="score files exactly as given, WITHOUT the JPEG re-encode "
                         "the model was trained and benchmarked with. Creates a "
                         "train/inference mismatch -- your measured metrics no "
                         "longer describe this configuration.")
    args = ap.parse_args()

    det = Detector(args.bundle, renormalise=not args.no_renormalise)
    print(f"[detector] loaded, device={det.device}, threshold={det.threshold:.4f}, "
         f"renormalise={det.renormalise}")

    results = det.predict_folder(
        args.folder, batch_size=args.batch_size,
        progress=lambda d, t: print(f"  {d}/{t}", end="\r", flush=True),
    )
    print()
    for r in results:
        if r["verdict"] == "error":
            print(f"  ERROR  {Path(r['path']).name}: {r.get('error')}")
        else:
            print(f"  {r['verdict']:5s}  p={r['probability_fake']:.3f}  "
                 f"{Path(r['path']).name}")

    s = det.summary(results)
    print(f"\n{s['total']} images: {s['fake']} fake, {s['real']} real, "
         f"{s['errors']} errors")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(results, indent=2))
        print(f"[save] {args.json_out}")


if __name__ == "__main__":
    main()