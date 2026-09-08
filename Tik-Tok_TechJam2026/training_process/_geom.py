"""Shared geometry helpers used by the prepare_*.py and split_pool.py scripts."""
from __future__ import annotations

import random
from pathlib import Path

from PIL import Image

IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


def list_images(d: Path) -> list[Path]:
    return sorted(p for p in d.rglob("*") if p.is_file() and p.suffix.lower() in IMG_EXT)


def geometry_params(w: int, h: int, rng: random.Random,
                    lo: int = 384, hi: int = 1024) -> tuple[int, int, int, int]:
    """Random square crop box + a target side drawn from a class-independent range."""
    s = min(w, h)
    left = rng.randint(0, w - s)
    top = rng.randint(0, h - s)
    side = rng.randint(lo, hi)
    return left, top, s, side


def apply_geometry(img: Image.Image, params,
                   resample=Image.Resampling.BICUBIC) -> Image.Image:
    left, top, s, side = params
    return img.crop((left, top, left + s, top + s)).resize((side, side), resample)
