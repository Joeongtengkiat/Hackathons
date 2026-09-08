"""
Degradation bank for AIGC detection.

Two separate things live here, and keeping them separate is the whole point:

  EVAL_GRID   - the exact discrete settings from the brief. Never train on these.
  sample_chain- continuous ranges + random composition, used for training.

Training on continuous ranges and evaluating on the discrete grid means the
grid numbers measure interpolation, not memorisation. HELDOUT_GRID goes one
step further: settings deliberately outside the training ranges, so you can
report extrapolation separately.
"""
from __future__ import annotations

import io
import random
from dataclasses import dataclass
from functools import partial
from typing import Callable

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter

# --------------------------------------------------------------------------
# primitive operations. all take and return a PIL RGB image.
# all operate at NATIVE resolution, before any CLIP preprocessing.
# --------------------------------------------------------------------------


def op_jpeg(img: Image.Image, quality: int) -> Image.Image:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=int(quality), subsampling=2)
    buf.seek(0)
    out = Image.open(buf)
    out.load()
    return out.convert("RGB")


def op_blur(img: Image.Image, sigma: float) -> Image.Image:
    if sigma <= 0:
        return img
    return img.filter(ImageFilter.GaussianBlur(radius=float(sigma)))


def op_resize_cycle(img: Image.Image, scale: float) -> Image.Image:
    """Downscale then back up. Destroys high-frequency content irreversibly."""
    w, h = img.size
    sw, sh = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    return img.resize((sw, sh), Image.Resampling.BICUBIC).resize((w, h), Image.Resampling.BICUBIC)


def op_noise(img: Image.Image, sigma: float, rng: np.random.Generator) -> Image.Image:
    if sigma <= 0:
        return img
    arr = np.asarray(img, dtype=np.float32) / 255.0
    arr = arr + rng.normal(0.0, float(sigma), arr.shape).astype(np.float32)
    return Image.fromarray((np.clip(arr, 0.0, 1.0) * 255.0).astype(np.uint8))


def op_color(img: Image.Image, brightness: float, contrast: float, saturation: float) -> Image.Image:
    img = ImageEnhance.Brightness(img).enhance(brightness)
    img = ImageEnhance.Contrast(img).enhance(contrast)
    img = ImageEnhance.Color(img).enhance(saturation)
    return img


def op_center_crop(img: Image.Image, frac: float) -> Image.Image:
    w, h = img.size
    cw, ch = max(1, int(round(w * frac))), max(1, int(round(h * frac)))
    left, top = (w - cw) // 2, (h - ch) // 2
    return img.crop((left, top, left + cw, top + ch))


def op_webp(img: Image.Image, quality: int) -> Image.Image:
    """Not in the brief. Held out as an unseen codec to test generalisation."""
    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=int(quality))
    buf.seek(0)
    out = Image.open(buf)
    out.load()
    return out.convert("RGB")


# --------------------------------------------------------------------------
# eval grid: exactly the brief's settings, plus held-out severities
#
# PICKLING NOTE: every condition here must survive being sent to a worker
# process. On Linux/Mac, DataLoader workers are created with fork, which
# just copies memory — closures and lambdas work fine there. On WINDOWS,
# workers are created with spawn, which PICKLES the Dataset (and everything
# it references) to hand to a new process. Lambdas and nested closures
# cannot be pickled, so anything using `lambda im, r: ...` here breaks
# ONLY on Windows with num_workers > 0 -- and works everywhere else, which
# is exactly why this class of bug is easy to ship without noticing.
#
# functools.partial bound to a top-level, module-level function pickles by
# reference (module path + function name), never by value, so it survives
# spawn correctly on every platform. Every condition below uses this pattern
# instead of a lambda.
# --------------------------------------------------------------------------

Condition = Callable[[Image.Image, np.random.Generator], Image.Image]


@dataclass(frozen=True)
class NamedCondition:
    name: str
    family: str
    fn: Condition


def _nc(name: str, family: str, fn: Condition) -> NamedCondition:
    return NamedCondition(name=name, family=family, fn=fn)


def _identity(im: Image.Image, r: np.random.Generator) -> Image.Image:
    return im


def _jpeg_op(im: Image.Image, r: np.random.Generator, q: int) -> Image.Image:
    return op_jpeg(im, q)


def _blur_op(im: Image.Image, r: np.random.Generator, sigma: float) -> Image.Image:
    return op_blur(im, sigma)


def _resize_op(im: Image.Image, r: np.random.Generator, scale: float) -> Image.Image:
    return op_resize_cycle(im, scale)


def _noise_op(im: Image.Image, r: np.random.Generator, sigma: float) -> Image.Image:
    return op_noise(im, sigma, r)


def _color_op(im: Image.Image, r: np.random.Generator,
             b: float, c: float, s: float) -> Image.Image:
    return op_color(im, b, c, s)


def _crop_op(im: Image.Image, r: np.random.Generator, frac: float) -> Image.Image:
    return op_center_crop(im, frac)


def _webp_op(im: Image.Image, r: np.random.Generator, q: int) -> Image.Image:
    return op_webp(im, q)


def _chain_social_repost(im: Image.Image, r: np.random.Generator) -> Image.Image:
    return op_jpeg(op_resize_cycle(op_jpeg(im, 90), 0.5), 70)


def _chain_filtered_share(im: Image.Image, r: np.random.Generator) -> Image.Image:
    return op_jpeg(op_color(im, 1.15, 1.1, 1.2), 60)


def _chain_thumbnail_crop(im: Image.Image, r: np.random.Generator) -> Image.Image:
    return op_jpeg(op_center_crop(op_resize_cycle(im, 0.25), 0.8), 70)


def _chain_lowlight_msg(im: Image.Image, r: np.random.Generator) -> Image.Image:
    return op_jpeg(op_blur(op_noise(im, 0.05, r), 0.5), 50)


def _chain_screenshot(im: Image.Image, r: np.random.Generator) -> Image.Image:
    return op_jpeg(op_resize_cycle(op_jpeg(op_color(im, 1.1, 1.05, 0.95), 80), 0.5), 40)


EVAL_GRID: list[NamedCondition] = [
    _nc("clean", "clean", _identity),
    # JPEG
    *[_nc(f"jpeg_q{q}", "jpeg", partial(_jpeg_op, q=q)) for q in (90, 70, 50, 30)],
    # Gaussian blur
    *[_nc(f"blur_s{s}", "blur", partial(_blur_op, sigma=s)) for s in (0.5, 1.0, 2.0)],
    # Resize cycle
    *[_nc(f"resize_{s}x", "resize", partial(_resize_op, scale=s)) for s in (0.5, 0.25)],
    # Gaussian noise
    *[_nc(f"noise_s{s}", "noise", partial(_noise_op, sigma=s)) for s in (0.02, 0.05, 0.10)],
    # Colour jitter, at the +/-20% corners (worst case, not a random draw)
    _nc("color_up", "color", partial(_color_op, b=1.2, c=1.2, s=1.2)),
    _nc("color_down", "color", partial(_color_op, b=0.8, c=0.8, s=0.8)),
    _nc("color_mixed", "color", partial(_color_op, b=1.2, c=0.8, s=1.2)),
    # Centre crop
    _nc("crop_80", "crop", partial(_crop_op, frac=0.80)),
]

# Severities and codecs deliberately outside the training ranges.
# Report these separately as "extrapolation" — they are your honest number.
HELDOUT_GRID: list[NamedCondition] = [
    _nc("jpeg_q20", "jpeg_ood", partial(_jpeg_op, q=20)),
    _nc("blur_s3.0", "blur_ood", partial(_blur_op, sigma=3.0)),
    _nc("resize_0.125x", "resize_ood", partial(_resize_op, scale=0.125)),
    _nc("noise_s0.15", "noise_ood", partial(_noise_op, sigma=0.15)),
    _nc("crop_50", "crop_ood", partial(_crop_op, frac=0.50)),
    _nc("webp_q50", "codec_ood", partial(_webp_op, q=50)),
]

# Realistic redistribution chains. Single transforms are the easy case; a real
# image that has been through a phone filter, a thumbnailer and two re-encodes
# is what actually shows up. These are usually where detectors fall over.
CHAIN_GRID: list[NamedCondition] = [
    _nc("social_repost", "chain", _chain_social_repost),
    _nc("filtered_share", "chain", _chain_filtered_share),
    _nc("thumbnail_crop", "chain", _chain_thumbnail_crop),
    _nc("lowlight_msg", "chain", _chain_lowlight_msg),
    _nc("screenshot_chain", "chain", _chain_screenshot),
]


# --------------------------------------------------------------------------
# training-time sampler: continuous ranges, random composition
# --------------------------------------------------------------------------

TRAIN_RANGES = {
    "jpeg": (30, 95),          # quality
    "blur": (0.0, 2.0),        # sigma
    "resize": (0.25, 1.0),     # scale
    "noise": (0.0, 0.10),      # sigma
    "color": (0.80, 1.20),     # enhance factor
    "crop": (0.75, 1.0),       # keep fraction
}

# Applied in a plausible physical order: geometry -> photometry -> sensor -> optics -> codec.
_ORDER = ["crop", "resize", "color", "noise", "blur", "jpeg"]


def sample_chain(
    img: Image.Image,
    rng: np.random.Generator,
    py_rng: random.Random,
    max_ops: int = 3,
    p_clean: float = 0.15,
) -> tuple[Image.Image, dict]:
    """Draw a random degradation chain. Returns (image, params_used)."""
    if py_rng.random() < p_clean:
        return img, {}

    n = py_rng.randint(1, max_ops)
    chosen = set(py_rng.sample(_ORDER, k=n))
    used: dict = {}

    for op in _ORDER:
        if op not in chosen:
            continue
        lo, hi = TRAIN_RANGES[op]
        if op == "crop":
            f = py_rng.uniform(lo, hi)
            img, used["crop"] = op_center_crop(img, f), round(f, 3)
        elif op == "resize":
            f = py_rng.uniform(lo, hi)
            img, used["resize"] = op_resize_cycle(img, f), round(f, 3)
        elif op == "color":
            b, c, s = (py_rng.uniform(lo, hi) for _ in range(3))
            img = op_color(img, b, c, s)
            used["color"] = [round(b, 3), round(c, 3), round(s, 3)]
        elif op == "noise":
            f = py_rng.uniform(lo, hi)
            img, used["noise"] = op_noise(img, f, rng), round(f, 4)
        elif op == "blur":
            f = py_rng.uniform(lo, hi)
            img, used["blur"] = op_blur(img, f), round(f, 3)
        elif op == "jpeg":
            q = py_rng.randint(int(lo), int(hi))
            img, used["jpeg"] = op_jpeg(img, q), q

    return img, used


def normalise_source(img: Image.Image, rng: np.random.Generator, py_rng: random.Random) -> Image.Image:
    """
    Kill the format shortcut before anything else touches the image.

    Most public benchmarks store real photos as JPEG and generated images as
    PNG. A detector will happily learn "is it JPEG?" and score 99% on the
    val set while being worthless. Re-encoding EVERY image at a random quality
    removes that channel. Run this on both classes, always, train and test.
    """
    q = py_rng.randint(85, 98)
    return op_jpeg(img.convert("RGB"), q)


ALL_CONDITIONS = {c.name: c for c in EVAL_GRID + HELDOUT_GRID + CHAIN_GRID}