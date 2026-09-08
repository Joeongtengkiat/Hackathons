"""Detector registry.

clip_head.py is the real model: a frozen CLIP ViT-L/14 tower plus the trained
MLP head, loaded from the bundle at models/bundle.pt. ensemble.py averages
several such bundles over one shared tower. trained.py stays as the generic slot
for any other checkpoint dropped at models/model.pt.

Importing a detector module is what registers it - the @register decorator runs
at import time - so a new backend has to be imported here to exist. The imports
below are therefore load-bearing, not unused, which is what the noqa marks say.
Importing a module must stay cheap: torch is imported inside load(), so that
`--list-detectors` does not pay for a framework it will not use.
"""

from .base import (  # noqa: F401
    Detector, register, available_detectors, get_detector, weights_detectors
)

from . import clip_head  # noqa: F401
from . import ensemble  # noqa: F401
from . import trained  # noqa: F401

__all__ = ["Detector", "register", "available_detectors", "get_detector",
           "weights_detectors"]
