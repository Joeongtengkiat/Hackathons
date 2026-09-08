"""CLIP ViT-L/14 + adversarially-trained MLP head - the real detector.

The model is two halves. A frozen CLIP vision tower turns an image into a
768-d embedding, and a small MLP trained on top of those embeddings decides
whether the image is AI-generated. Only the head was trained; the tower is
stock `openai/clip-vit-large-patch14`.

Everything travels in one file, `models/bundle.pt`, so nothing is downloaded
at run time and nothing has to match a config kept somewhere else:

    clip_config / clip_state_dict   the frozen tower, its config and weights
    head_state_dict / head_config   the trained MLP
    mu, sd                          per-dimension feature standardisation
    platt_a, platt_b                logit -> calibrated probability
    threshold                       the 1%-FPR operating point, in logit space
    feature, preproc, l2            the preprocessing the head was trained for

Scoring reproduces the training pipeline exactly, in this order:

    JPEG re-encode at q92                                  (SOURCE_JPEG_Q)
    resize shortest side to 224 (bicubic) -> centre crop 224
    CLIP normalisation (not ImageNet - the constants differ)
    vision tower -> pooler_output -> visual_projection      (feature="proj")
    L2-normalise -> (x - mu) / sd
    head -> logit -> sigmoid(platt_a * logit + platt_b)

That first step is easy to mistake for an optimisation and delete. Training
re-encoded every image, of both classes, before it was ever seen - so mu, sd,
the Platt coefficients and the threshold were all fitted on re-encoded
features. A pristine PNG scored without it is off-distribution.

That last step matters. Training used pos_weight and a CVaR objective, both of
which distort the output scale, so a raw sigmoid(logit) is a confidence number
and not a probability. The Platt coefficients were fitted on pooled augmented
validation scores, which is what makes `pred` safe to show as "% likely AI".
"""

from __future__ import annotations

import io
import os

import numpy as np
from PIL import Image

from .base import Detector, register

# --------------------------------------------------------------------------- #
# Constants that must match the training pipeline (clipfeat.py).
# --------------------------------------------------------------------------- #

#: CLIP ViT-L/14's input resolution. Fixed by the patch embedding, not a knob.
RES = 224
#: CLIP's own normalisation constants. These are NOT ImageNet's - the numbers
#: are close enough to look like a typo and far enough to move every score.
CLIP_MEAN = (0.48145466, 0.45782750, 0.40821073)
CLIP_STD = (0.26862954, 0.26130258, 0.27577711)

# Training re-encoded every image at a random JPEG quality in 85-98
# (augment.normalise_source) so the head could not learn "real photos are
# JPEG, generated ones are PNG" instead of learning the actual task. That
# means every feature the head ever saw came from a re-encoded image, so
# scoring has to do it too. The midpoint is used rather than a random draw:
# inference has to be deterministic and not depend on file ordering.
SOURCE_JPEG_Q = 92

# PIL opens anything; a corrupt header can claim gigapixels. Same cap the
# training loader used, so the two agree on what counts as unreadable.
# PIL's own decompression-bomb guard trips at 178M px, well under that cap,
# and _open would turn it into a silent NaN - so disable it (as clipfeat.py
# does) and let the explicit check be the one that decides.
MAX_PIXELS = 200_000_000
Image.MAX_IMAGE_PIXELS = None

#: the bundle. Present -> this backend becomes the default everywhere.
DEFAULT_WEIGHTS = os.path.join("models", "bundle.pt")


#: built on first use inside _head_class, so importing this module needs no torch
_HEAD_CLS = None


def _head_class(torch):
    """The trained Head class, defined lazily so importing this module is free.

    Nothing here may drift from training: the checkpoint keys are net.0/1/4/5/8,
    which pins both the attribute name `net` and the layer indices - the ReLU
    and Dropout have to stay in place even though they hold no weights. Dropout
    is inert in eval() anyway.
    """
    global _HEAD_CLS
    if _HEAD_CLS is not None:
        return _HEAD_CLS

    nn = torch.nn

    class Head(nn.Module):
        def __init__(self, dim, kind="mlp", hidden=512, dropout=0.3):
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
                raise ValueError(f"unknown head kind {kind!r}")

        def forward(self, x):
            return self.net(x).squeeze(-1)        # (B, D) -> (B,)

    _HEAD_CLS = Head
    return Head


def _center_zoom(img: Image.Image, keep: float) -> Image.Image:
    """Crop the central `keep` fraction - a slight zoom used as a TTA view.

    _to_clip_array resizes back to 224 afterwards, so this reframes the image
    rather than shrinking it: the detector sees the same subject a little
    closer, which is a fair second opinion, not an added degradation.
    """
    w, h = img.size
    nw, nh = max(1, int(w * keep)), max(1, int(h * keep))
    left, top = (w - nw) // 2, (h - nh) // 2
    return img.crop((left, top, left + nw, top + nh))


@register
class ClipHeadDetector(Detector):
    name = "clip_head"                          # --detector clip_head
    display_name = "CLIP ViT-L/14 + MLP head"
    description = (
        "Frozen CLIP ViT-L/14 embeddings scored by an MLP head trained "
        "adversarially against post-processing (JPEG, blur, resize, noise). "
        f"Loads the self-contained bundle at {DEFAULT_WEIGHTS}; the output is "
        "Platt-calibrated, so it reads as a probability."
    )
    requires_weights = True
    default_weights = DEFAULT_WEIGHTS
    # CLIP ViT-L/14 is ~300M params. 16 keeps CPU memory sane and the progress
    # bar moving; a GPU is nowhere near saturated, but it is not the bottleneck.
    batch_size = 16

    # Test-time augmentation: score N views of each image and average the
    # calibrated probabilities. 1 = off, and off is the default so the deployed
    # numbers stay deterministic and unchanged. 2 adds a horizontal flip (the
    # classic, always-safe view); 3-4 add a mild centre zoom and its flip.
    # Averaging smooths the per-image variance that post-processing introduces,
    # which is where a robustness sweep tends to show the gain - at N x the cost.
    tta_views = 1
    #: the views TTA can draw on, in priority order; tta_views takes the first N
    MAX_TTA_VIEWS = 4

    def __init__(self):
        super().__init__()
        self.clip = None          # the frozen vision tower
        self.head = None          # the trained MLP
        self.device = None
        self._torch = None        # the module, held so inference need not re-import
        # filled from the bundle at load()
        self.mu = self.sd = None
        self.platt_a, self.platt_b = 1.0, 0.0
        self.threshold_logit = 0.0
        self.feature = "proj"
        self.preproc = "resize"
        self.l2 = True

    # -- lifecycle ---------------------------------------------------------
    def load(self) -> None:
        """Read the bundle and build both halves of the model.

        Every failure here raises SystemExit with a message a user can act on,
        because there is nothing this class can do about a missing file or a
        missing dependency and a traceback would only bury the reason.
        """
        path = self.resolve_weights(self.weights)
        if not path or not os.path.isfile(path):
            raise SystemExit(
                f"error: no bundle at {os.path.abspath(path or DEFAULT_WEIGHTS)}\n"
                "       put bundle.pt there, or pass --weights <file>.")

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

        self._torch = torch
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.note(f"torch {torch.__version__} · device {str(self.device).upper()}"
                  + ("" if self.device.type == "cuda" else " (no CUDA device)"))

        # By far the longest step, and torch.load gives no callback to hang a
        # percentage on - so say how much is coming instead of pretending.
        size_gb = os.path.getsize(path) / 1e9
        self.note(f"reading {os.path.basename(path)} ({size_gb:.2f} GB) from disk")
        bundle = torch.load(path, map_location="cpu", weights_only=False)
        # fail on the file, not on the first missing key three steps later
        missing = {"clip_config", "clip_state_dict", "head_state_dict",
                   "head_dim", "mu", "sd"} - set(bundle)
        if missing:
            raise SystemExit(
                f"error: {path} is not a detector bundle - missing "
                f"{', '.join(sorted(missing))}")

        # the preprocessing and calibration the head was trained under. Defaults
        # match the current training config; a bundle that disagrees wins.
        self.feature = bundle.get("feature", "proj")
        self.preproc = bundle.get("preproc", "resize")
        self.l2 = bool(bundle.get("l2", True))
        self.platt_a = float(bundle.get("platt_a", 1.0))
        self.platt_b = float(bundle.get("platt_b", 0.0))
        self.threshold_logit = float(bundle.get("threshold", 0.0))

        # Build the tower from the bundled config rather than by name: no
        # network access, and no chance of drifting from the weights.
        name = bundle.get("clip_model_name", "CLIP vision tower")
        self.note(f"building {name}")
        cfg = CLIPVisionConfig(**bundle["clip_config"])
        clip = CLIPVisionModelWithProjection(cfg)
        self.note(f"loading {len(bundle['clip_state_dict']):,} tower tensors")
        clip.load_state_dict(bundle["clip_state_dict"], strict=True)
        n_params = sum(p.numel() for p in clip.parameters())
        self.note(f"tower ready · {n_params / 1e6:.0f}M frozen parameters"
                  f" · moving to {str(self.device).upper()}")
        clip.eval().to(self.device)
        for p in clip.parameters():
            p.requires_grad_(False)   # inference only; also keeps memory down
        self.clip = clip

        hc = bundle.get("head_config") or {}
        hidden = int(hc.get("hidden", 512))
        self.note(f"loading {hc.get('head', 'mlp')} head "
                  f"{int(bundle['head_dim'])} -> {hidden} -> {hidden // 2} -> 1")
        head = _head_class(torch)(
            int(bundle["head_dim"]), hc.get("head", "mlp"),
            hidden, float(hc.get("dropout", 0.3)))
        head.load_state_dict(bundle["head_state_dict"], strict=True)
        head.eval().to(self.device)
        self.head = head

        # per-dimension feature standardisation, fitted on the training features.
        # On-device so the normalisation does not bounce back to the CPU.
        self.mu = torch.as_tensor(bundle["mu"], dtype=torch.float32).to(self.device)
        self.sd = torch.as_tensor(bundle["sd"], dtype=torch.float32).to(self.device)

        # The bundle's threshold lives in logit space; the scores we hand back
        # are Platt-calibrated, so put the operating point in the same units.
        # It is not 0.5 and it is not supposed to be: it was chosen for 1% FPR
        # on real images, which pushes it well up the scale.
        self.default_threshold = 1.0 / (
            1.0 + np.exp(-(self.platt_a * self.threshold_logit + self.platt_b)))
        self.note(f"ready · feature={self.feature} · preproc={self.preproc}"
                  f" · operating point {self.default_threshold:.3f}")

    def unload(self) -> None:
        """Drop the tower and the head. ~1.2 GB of the process goes with them."""
        self.clip = None
        self.head = None
        self.mu = self.sd = None

    # -- inference ---------------------------------------------------------
    def predict_batch(self, paths: list) -> list:
        """Score files. Unreadable ones come back NaN, in their original slot.

        `keep` carries the mapping: the failures are dropped before the forward
        pass and their scores are written back by index, so the returned list is
        always the same length as `paths` and never shifted.
        """
        images, keep = [], []
        for i, p in enumerate(paths):
            img = self._open(p)
            if img is not None:
                images.append(img)
                keep.append(i)

        scores = [float("nan")] * len(paths)      # unreadable files stay NaN
        for i, s in zip(keep, self.predict_images(images)):
            scores[i] = s
        return scores

    def predict_images(self, images: list) -> list:
        """Score decoded PIL images. The robustness sweep calls this directly.

        With tta_views == 1 (the default) this is one calibrated probability per
        image. Above that, each image is scored under several views and the
        probabilities are averaged - see _tta_view_fns for the views.
        """
        if not images:
            return []
        self.ensure_loaded()

        views = self._tta_view_fns()
        # one CLIP array per (image, view), grouped by image so the flat result
        # reshapes to (n_images, n_views) and averages back per image
        arrays = [self._to_clip_array(view(img))
                  for img in images for view in views]
        probs = self._calibrated_probs(arrays)                # len = n_img * n_view

        v = len(views)
        averaged = [sum(probs[i * v:(i + 1) * v]) / v for i in range(len(images))]
        return [round(float(p), 6) for p in averaged]

    def _tta_view_fns(self) -> list:
        """The view functions TTA averages over, `tta_views` of them.

        Identity is always first, so tta_views == 1 reproduces the plain,
        deterministic scoring exactly. The flip is the standard safe view; the
        zoom reframes by dropping a 10% border, which _to_clip_array then scales
        back to 224 - a legitimate second look, not a new degradation.
        """
        n = max(1, min(int(getattr(self, "tta_views", 1)), self.MAX_TTA_VIEWS))
        flip = Image.Transpose.FLIP_LEFT_RIGHT
        views = [
            lambda im: im,                                     # identity
            lambda im: im.transpose(flip),                    # horizontal flip
            lambda im: _center_zoom(im, 0.9),                 # mild zoom
            lambda im: _center_zoom(im, 0.9).transpose(flip),  # zoom + flip
        ]
        return views[:n]

    def _calibrated_probs(self, arrays: list) -> list:
        """Tower -> head -> Platt for a list of CLIP arrays, as plain floats.

        Forwards in chunks of batch_size so TTA (which multiplies the array
        count by the number of views) cannot blow up peak memory on CPU.
        """
        torch = self._torch
        bs = max(1, int(self.batch_size))
        out = []
        for start in range(0, len(arrays), bs):
            chunk = np.stack(arrays[start:start + bs])
            batch = torch.from_numpy(chunk).to(self.device)
            # no_grad: nothing here is trained, and the graph would cost memory
            with torch.no_grad():
                feats = self._embed(batch)
                if self.l2:                   # 1e-8 guards a zero-norm feature
                    feats = feats / (feats.norm(dim=-1, keepdim=True) + 1e-8)
                # exactly the training order: L2-normalise, then standardise.
                # Swapping the two changes every feature the head sees.
                feats = (feats - self.mu) / self.sd
                logits = self.head(feats)         # (B,), the head squeezes
                probs = torch.sigmoid(self.platt_a * logits + self.platt_b)
            out.extend(float(p) for p in probs.float().cpu())
        return out

    def _embed(self, batch):
        """Tower forward, honouring whichever feature the head was trained on."""
        out = self.clip.vision_model(pixel_values=batch)
        pooled = out.pooler_output
        if self.feature == "pooled":
            return pooled.float()
        proj = self.clip.visual_projection(pooled)
        if self.feature == "proj":
            return proj.float()
        if self.feature == "both":
            return self._torch.cat([pooled, proj], dim=-1).float()
        raise ValueError(f"unknown feature {self.feature!r}")

    # -- preprocessing -----------------------------------------------------
    def prepare_source(self, img: Image.Image) -> Image.Image:
        """Re-encode as JPEG, exactly as training did to every image.

        Mirrors augment.op_jpeg. subsampling=2 (4:2:0) is not incidental -
        it is what the training re-encode used, and chroma subsampling is
        precisely the kind of high-frequency damage this model is asked to
        see through. The sweep calls this before its own transforms, so a
        degraded image is re-encoded first and degraded second, as in training.
        """
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=SOURCE_JPEG_Q,
                                subsampling=2)
        buf.seek(0)
        out = Image.open(buf)
        out.load()
        return out.convert("RGB")

    def _open(self, path: str):
        """Decode at full resolution - no size cap.

        Deliberately not Detector.open_image: that pre-shrinks to 1024 with a
        bilinear filter, and the model was trained on a bicubic resize straight
        from the source. Two resamples are not the same as one.
        """
        try:
            img = Image.open(path)
            w, h = img.size
            if w * h > MAX_PIXELS:
                return None
            return self.prepare_source(img.convert("RGB"))
        except Exception:
            return None

    def _to_clip_array(self, img: Image.Image) -> np.ndarray:
        """Shortest side to 224 (bicubic), centre crop, CLIP normalisation."""
        img = img.convert("RGB")
        w, h = img.size
        s = RES / min(w, h)
        img = img.resize((max(RES, int(round(w * s))), max(RES, int(round(h * s)))),
                         Image.Resampling.BICUBIC)
        w, h = img.size
        left, top = (w - RES) // 2, (h - RES) // 2
        img = img.crop((left, top, left + RES, top + RES))

        arr = np.asarray(img, dtype=np.float32).transpose(2, 0, 1) / 255.0
        arr = arr - np.array(CLIP_MEAN, dtype=np.float32).reshape(3, 1, 1)
        arr /= np.array(CLIP_STD, dtype=np.float32).reshape(3, 1, 1)
        return arr
