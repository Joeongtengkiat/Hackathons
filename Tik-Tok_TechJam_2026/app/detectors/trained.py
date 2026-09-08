"""The trained model: loads a checkpoint from disk and scores images with it.

This is the generic backend. It ships wired but with no weights - drop a
checkpoint at models/model.pt (or pass --weights) and it is ready to run.
clip_head sorts ahead of it while models/bundle.pt is there, so ask for this
one by name:

    python detect.py <dir> --detector trained                     models/model.pt
    python detect.py <dir> --detector trained -w runs/best.pt     that file

Two checkpoint shapes load with no code changes:

  * TorchScript  - torch.jit.save(torch.jit.script(model), "models/model.pt")
    Self-contained: architecture travels with the weights. Preferred.
  * Pickled nn.Module - torch.save(model, "models/model.pt")
    Needs the defining class importable at load time.

A bare state_dict cannot be loaded on its own - nothing in it says what
architecture to build - so build_model() below is where you say. The
preprocessing and output constants at the top are the other things to check
against how the model was actually trained; they are the common defaults, not
a guess that is guaranteed right.
"""

from __future__ import annotations

import os

import numpy as np
from PIL import Image

from .base import Detector, register

# --------------------------------------------------------------------------- #
# Match these to the training pipeline.
# --------------------------------------------------------------------------- #

# These four are the usual defaults, not a guess that is guaranteed right for
# your checkpoint. A model trained at 256, or on CLIP's constants rather than
# ImageNet's, will still run and quietly score badly - which is the failure mode
# to look for first if a known-good model gives you AUC near 0.5.
INPUT_SIZE = 224                                  # square resize before the model
MEAN = (0.485, 0.456, 0.406)                      # ImageNet normalisation
STD = (0.229, 0.224, 0.225)
AI_CLASS_INDEX = 1                                # which logit means "AI-generated"

DEFAULT_WEIGHTS = os.path.join("models", "model.pt")


def build_model(state_dict):
    """Rebuild the architecture for a bare state_dict checkpoint.

    Only called when the checkpoint is a plain tensor dict. Fill this in with
    the model the weights came from, e.g.:

        import torchvision
        model = torchvision.models.efficientnet_b0(num_classes=2)
        model.load_state_dict(state_dict)
        return model
    """
    raise NotImplementedError(
        "this checkpoint is a bare state_dict, which does not say what "
        "architecture to build.\n"
        "       Either re-save as TorchScript:\n"
        "           torch.jit.save(torch.jit.script(model), 'models/model.pt')\n"
        "       or fill in build_model() in app/detectors/trained.py."
    )


@register
class TrainedDetector(Detector):
    name = "trained"
    display_name = "Trained model"
    description = (
        "Loads a trained checkpoint (TorchScript or a pickled nn.Module) and "
        "scores images with it. Point --weights at a file, or leave it at "
        f"{DEFAULT_WEIGHTS}."
    )
    requires_weights = True
    default_weights = DEFAULT_WEIGHTS
    batch_size = 32

    def __init__(self):
        super().__init__()
        self.model = None
        self.device = None
        self._torch = None    # the module, held so inference need not re-import

    # -- lifecycle ---------------------------------------------------------
    def load(self) -> None:
        """Read the checkpoint, move it to the best device, put it in eval mode."""
        path = self.resolve_weights(self.weights)
        if not path or not os.path.isfile(path):
            raise SystemExit(
                f"error: no checkpoint at {os.path.abspath(path or DEFAULT_WEIGHTS)}\n"
                "       put a trained model there, or pass --weights <file>."
            )

        try:
            import torch
        except ImportError:                       # pragma: no cover - env-dependent
            raise SystemExit("error: torch is required to run the trained model "
                             "(pip install -r requirements.txt)")
        self._torch = torch
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        model = self._load_checkpoint(torch, path)
        model.eval()
        self.model = model.to(self.device)

    def _load_checkpoint(self, torch, path: str):
        """TorchScript first, then a pickled module, then build_model().

        In that order because it goes from most self-contained to least: a
        TorchScript file needs nothing from this project, a pickle needs its
        defining class importable, and a bare state_dict needs code written.
        """
        try:
            return torch.jit.load(path, map_location="cpu")
        except Exception:
            pass                                  # not TorchScript - try a pickle

        try:
            obj = torch.load(path, map_location="cpu", weights_only=False)
        except Exception as exc:
            hint = ""
            if "get attribute" in str(exc) or "No module named" in str(exc):
                # torch.save(model) stores a reference to the defining class,
                # not the class itself, so unpickling needs it importable here
                hint = ("\n       This checkpoint is a pickled nn.Module, so the "
                        "class that defined it\n"
                        "       must be importable from this project. Re-saving "
                        "as TorchScript avoids that:\n"
                        "           torch.jit.save(torch.jit.script(model), "
                        "'models/model.pt')")
            raise SystemExit(
                f"error: could not read the checkpoint {path}: {exc}{hint}")

        if isinstance(obj, torch.nn.Module):
            return obj
        if isinstance(obj, dict):
            # training checkpoints usually nest the tensors under a key
            for key in ("model", "state_dict", "model_state_dict", "net"):
                inner = obj.get(key)
                if isinstance(inner, torch.nn.Module):
                    return inner
                if isinstance(inner, dict):
                    obj = inner
                    break
            try:
                return build_model(obj)
            except NotImplementedError as exc:
                raise SystemExit(f"error: {exc}")
        raise SystemExit(f"error: {path} holds a {type(obj).__name__}, "
                         "which is not a model")

    def unload(self) -> None:
        """Drop the model and let the allocator reclaim it."""
        self.model = None

    # -- inference ---------------------------------------------------------
    def predict_batch(self, paths: list) -> list:
        images, keep = [], []
        for i, p in enumerate(paths):
            try:
                images.append(self.open_image(p, max_side=max(INPUT_SIZE * 2, 512)))
                keep.append(i)
            except Exception:
                pass                              # unreadable file -> stays NaN

        scores = [float("nan")] * len(paths)
        for i, s in zip(keep, self.predict_images(images)):
            scores[i] = s
        return scores

    def predict_images(self, images: list) -> list:
        """Score decoded PIL images. The sweep calls this - no disk round-trip."""
        if not images:
            return []
        self.ensure_loaded()
        torch = self._torch

        batch = torch.from_numpy(
            np.stack([self._to_array(img) for img in images])).to(self.device)
        with torch.no_grad():
            out = self.model(batch)
        return self._to_scores(out)

    def _to_array(self, img: Image.Image) -> np.ndarray:
        """PIL image -> normalised CHW float array, per the constants above.

        A square resize, not resize-shortest-side plus centre crop: it distorts
        the aspect ratio but keeps the whole frame, which is what most
        classification training pipelines do. Change it if yours did not.
        """
        img = img.convert("RGB").resize((INPUT_SIZE, INPUT_SIZE), Image.BILINEAR)
        arr = np.asarray(img, dtype=np.float32) / 255.0
        arr = (arr - np.array(MEAN, dtype=np.float32)) / np.array(STD, dtype=np.float32)
        return arr.transpose(2, 0, 1)             # HWC -> CHW

    def _to_scores(self, out) -> list:
        """Squash whatever the head emits into P(AI) per image.

        Covers the three shapes a classifier head actually returns: (B,) and
        (B, 1) are one logit through a sigmoid, (B, C) is class logits through a
        softmax read at AI_CLASS_INDEX.
        """
        torch = self._torch
        if isinstance(out, (tuple, list)):
            out = out[0]              # models that also return features/aux heads
        out = out.detach().float().cpu()
        if out.ndim == 1:                         # one logit per image
            probs = torch.sigmoid(out)
        elif out.shape[1] == 1:
            probs = torch.sigmoid(out[:, 0])
        else:                                     # class logits
            probs = torch.softmax(out, dim=1)[:, AI_CLASS_INDEX]
        return [round(float(p), 6) for p in probs]
