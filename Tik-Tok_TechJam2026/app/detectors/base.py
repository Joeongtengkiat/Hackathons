"""Detector plugin interface.

This is the ONLY seam the real model needs to touch. Usually there is nothing
to write at all: drop a checkpoint at models/model.pt and trained.py picks it
up. Write a module here only when the model needs its own preprocessing or
architecture code:

    from .base import Detector, register

    @register
    class MyModel(Detector):
        name = "my_model"
        display_name = "EfficientNet-B0 + FFT head"
        description = "Trained on ..."

        def load(self):
            self.model = torch.load(...)

        def predict_batch(self, paths):
            return [float(p) for p in ...]   # 0.0 = authentic, 1.0 = AI

...then import it in app/detectors/__init__.py. It appears in the toolbar
picker automatically.

The contract in full:

    predict_batch(paths) -> [float]   required. One score per path, in order,
                                      NaN for a file that could not be read.
    predict_images(imgs) -> [float]   optional. Same, for decoded PIL images.
    prepare_source(img)  -> img       optional. Condition a source image the way
                                      training did, before any degradation.
    load() / unload()                 optional lifecycle, called off the GUI
                                      thread; do the expensive work in load().
    default_threshold                 where "AI" starts for this backend.

Scores are P(AI) in [0, 1] and must not depend on the threshold, on batch
composition, or on how many times the detector has been called.
"""

from __future__ import annotations

import os
import tempfile
from abc import ABC, abstractmethod

from PIL import Image

_REGISTRY: dict = {}


class Detector(ABC):
    """Base class for all detection backends.

    Scores are confidences in [0, 1] that the image is AI-generated.
    """

    name: str = "base"            # the --detector key, stable and lowercase
    display_name: str = "Base detector"   # shown in the picker and the header
    description: str = ""         # one paragraph, shown as the picker tooltip
    # Recommended images per predict_batch call; smaller = smoother progress.
    batch_size: int = 16

    # Where "AI" starts. 0.5 only makes sense for a backend whose scores are
    # centred there; a calibrated model usually carries its own operating point
    # in the checkpoint, so load() may overwrite this on the instance. Nothing
    # about the scores changes - this is the decision boundary, not the score.
    default_threshold: float = 0.5

    # A backend that loads a trained checkpoint sets these. `weights` is per
    # instance so --weights can point at a file other than the default.
    requires_weights: bool = False
    default_weights: str = ""
    weights: str | None = None

    #: optional callback(str), set by the runner. Lets a slow load() say what
    #: it is doing instead of blocking silently - loading a gigabyte-plus
    #: bundle is long enough that a caller with a UI needs to show something.
    progress_cb = None

    def __init__(self):
        # loading is deferred to ensure_loaded() so constructing a detector -
        # which the picker does for every registered backend - stays free
        self._loaded = False

    def note(self, message: str) -> None:
        """Narrate a slow step. Does nothing unless someone is listening."""
        if self.progress_cb is not None:
            self.progress_cb(message)

    # -- readiness ---------------------------------------------------------
    @classmethod
    def resolve_weights(cls, weights: str = None) -> str:
        """The checkpoint this backend would actually load: override or default."""
        return weights or cls.default_weights

    @classmethod
    def is_ready(cls, weights: str = None) -> bool:
        """False when a required checkpoint is missing.

        Lets the picker show the backend and say why it can't run yet, instead
        of hiding it or failing deep inside load().
        """
        if not cls.requires_weights:
            return True
        path = cls.resolve_weights(weights)
        return bool(path) and os.path.isfile(path)

    # -- lifecycle ---------------------------------------------------------
    def load(self) -> None:
        """Load weights / warm up. Called once, lazily, off the GUI thread."""

    def unload(self) -> None:
        """Release resources."""

    def ensure_loaded(self) -> None:
        """Load once, on first use. Safe to call before every batch."""
        if not self._loaded:
            self.load()
            self._loaded = True

    # -- inference ---------------------------------------------------------
    @abstractmethod
    def predict_batch(self, paths: list) -> list:
        """Return one float in [0, 1] per input path."""
        raise NotImplementedError

    def predict_images(self, images: list) -> list:
        """Score already-decoded PIL images (used by the robustness sweep).

        The default round-trips through temp files so a path-only detector
        still works. Override this for anything real - it avoids the disk I/O.
        """
        tmp_paths = []
        try:
            for img in images:
                # PNG, not JPEG: a lossy round-trip here would silently add a
                # degradation the sweep did not ask for
                fd, p = tempfile.mkstemp(suffix=".png")
                os.close(fd)
                img.convert("RGB").save(p)
                tmp_paths.append(p)
            return self.predict_batch(tmp_paths)
        finally:
            for p in tmp_paths:
                try:
                    os.unlink(p)
                except OSError:
                    pass

    # -- helpers -----------------------------------------------------------
    def prepare_source(self, img: Image.Image) -> Image.Image:
        """Normalise a freshly decoded image before any degradation is applied.

        Identity by default. A detector whose training pipeline conditioned the
        source - e.g. a JPEG re-encode to kill the format shortcut, where the
        real photos arrive as JPEG and the generated ones as PNG - overrides
        this. The robustness sweep calls it before its own transforms, so the
        ordering matches the one the model was trained under.
        """
        return img

    @staticmethod
    def open_image(path: str, max_side: int = 1024) -> Image.Image:
        """Decode with a size cap; JPEG draft mode keeps big files cheap."""
        img = Image.open(path)
        try:
            img.draft("RGB", (max_side, max_side))
        except Exception:
            pass
        img = img.convert("RGB")
        if max(img.size) > max_side:
            scale = max_side / max(img.size)
            img = img.resize(
                (max(1, int(img.width * scale)), max(1, int(img.height * scale))),
                Image.BILINEAR,
            )
        return img


def register(cls):
    """Class decorator that adds a detector to the registry.

    Keyed by `name`, so a second class with the same name replaces the first.
    """
    _REGISTRY[cls.name] = cls
    return cls


def available_detectors() -> list:
    """Registered detector classes, best-first.

    A backend whose checkpoint is present outranks one whose checkpoint is
    missing, so an empty slot sits at the bottom until weights exist and then
    becomes usable everywhere with no flag to flip.
    """
    # (not is_ready, display_name): False sorts before True, so runnable
    # backends come first and the rest are alphabetical within each group
    return sorted(_REGISTRY.values(),
                  key=lambda c: (not c.is_ready(), c.display_name))


def weights_detectors() -> list:
    """Backends that load a checkpoint - used to resolve a bare --weights."""
    return [c for c in available_detectors() if c.requires_weights]


def get_detector(name: str, weights: str = None) -> Detector:
    """Construct a registered detector. Raises KeyError on an unknown name.

    Returns a fresh instance every time: `weights` and `progress_cb` are set per
    instance, so two runs with different checkpoints cannot interfere.
    """
    cls = _REGISTRY.get(name)
    if cls is None:
        raise KeyError(f"No detector registered under {name!r}")
    det = cls()
    if weights:
        det.weights = weights
    return det
