"""
CLIP feature extraction. The frozen half of the system.

Design note that matters more than it looks: CLIP resizes everything to
224x224 before the patch embedding. That throws away the high-frequency
generator fingerprints that pixel-space detectors (CNNSpot, NPR, frequency
methods) rely on. It is exactly why CLIP features survive JPEG and blur so
well, and exactly why they cannot see subtle resampling traces.

`--preproc nativecrop` gives you the other side of that trade: crop 224x224
windows at native resolution so high-frequency content reaches the encoder
intact. Run both, report both. The comparison is a real finding.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

import numpy as np
import torch
from PIL import Image
from torch.utils.data import DataLoader, Dataset

import augment as A

Image.MAX_IMAGE_PIXELS = None

CLIP_MEAN = (0.48145466, 0.45782750, 0.40821073)
CLIP_STD = (0.26862954, 0.26130258, 0.27577711)
RES = 224

IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


@dataclass
class Sample:
    path: str
    label: int
    group: str


def scan_dir(root: str | Path) -> list[Sample]:
    root = Path(root)
    out: list[Sample] = []
    for label_name, label in (("real", 0), ("fake", 1)):
        base = root / label_name
        if not base.is_dir():
            raise FileNotFoundError(f"missing {base}")
        for p in sorted(base.rglob("*")):
            if p.suffix.lower() not in IMG_EXT or not p.is_file():
                continue
            rel = p.relative_to(base).parts
            group = rel[0] if len(rel) > 1 else f"{label_name}_root"
            out.append(Sample(str(p), label, group))
    if not out:
        raise RuntimeError(f"no images found under {root}")
    return out


_MEAN_T = torch.tensor(CLIP_MEAN).view(3, 1, 1)
_STD_T = torch.tensor(CLIP_STD).view(3, 1, 1)


def to_clip_tensor(img: Image.Image, preproc: str, py_rng: random.Random) -> torch.Tensor:
    if preproc == "resize":
        w, h = img.size
        s = RES / min(w, h)
        img = img.resize((max(RES, int(round(w * s))), max(RES, int(round(h * s)))), Image.Resampling.BICUBIC)
        w, h = img.size
        left, top = (w - RES) // 2, (h - RES) // 2
        img = img.crop((left, top, left + RES, top + RES))
    elif preproc == "nativecrop":
        w, h = img.size
        if min(w, h) < RES:
            s = RES / min(w, h)
            img = img.resize((max(RES, int(round(w * s))), max(RES, int(round(h * s)))), Image.Resampling.BICUBIC)
            w, h = img.size
        left = py_rng.randint(0, w - RES)
        top = py_rng.randint(0, h - RES)
        img = img.crop((left, top, left + RES, top + RES))
    else:
        raise ValueError(f"unknown preproc {preproc!r}")

    arr = torch.from_numpy(np.asarray(img, dtype=np.uint8).copy()).permute(2, 0, 1).float().div_(255.0)
    return (arr - _MEAN_T) / _STD_T


ViewFn = Callable[[Image.Image, np.random.Generator, random.Random], Image.Image]


class RandomView:
    def __init__(self, max_ops: int = 3, p_clean: float = 0.15) -> None:
        self.max_ops = max_ops
        self.p_clean = p_clean

    def __call__(self, img, rng, py_rng):
        return A.sample_chain(img, rng, py_rng, max_ops=self.max_ops, p_clean=self.p_clean)[0]


class CleanView:
    def __call__(self, img, rng, py_rng):
        return img


class ConditionView:
    def __init__(self, cond: A.NamedCondition) -> None:
        self.cond = cond

    def __call__(self, img, rng, py_rng):
        return self.cond.fn(img, rng)


def random_view(max_ops: int = 3, p_clean: float = 0.15) -> ViewFn:
    return RandomView(max_ops=max_ops, p_clean=p_clean)


def clean_view() -> ViewFn:
    return CleanView()


def condition_view(cond: A.NamedCondition) -> ViewFn:
    return ConditionView(cond)


class ViewDataset(Dataset):
    MAX_PIXELS = 200_000_000

    def __init__(self, samples, views, preproc: str = "resize", renormalise: bool = True, seed: int = 0) -> None:
        self.samples = list(samples)
        self.views = list(views)
        self.preproc = preproc
        self.renormalise = renormalise
        self.seed = seed

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, idx: int):
        s = self.samples[idx]
        seed = (self.seed * 1_000_003 + idx) % (2**31 - 1)
        rng = np.random.default_rng(seed)
        py_rng = random.Random(seed)

        try:
            raw = Image.open(s.path)
            w, h = raw.size
            if w * h > self.MAX_PIXELS:
                print(f"[skip] {s.path}: {w}x{h} = {w*h/1e6:.0f}MP exceeds the "
                     f"{self.MAX_PIXELS/1e6:.0f}MP cap, likely a corrupt file — skipping")
                return torch.zeros(len(self.views), 3, RES, RES), -1
            img = raw.convert("RGB")
        except Exception as e:
            print(f"[skip] {s.path}: failed to load ({type(e).__name__}: {e})")
            return torch.zeros(len(self.views), 3, RES, RES), -1

        if self.renormalise:
            img = A.normalise_source(img, rng, py_rng)

        out = [to_clip_tensor(v(img, rng, py_rng), self.preproc, py_rng) for v in self.views]
        return torch.stack(out, 0), s.label


def load_clip(model_name: str = "openai/clip-vit-large-patch14", device: str | None = None):
    from transformers import CLIPVisionModelWithProjection

    device = device or ("cuda" if torch.cuda.is_available() else "cpu")
    model = CLIPVisionModelWithProjection.from_pretrained(  # type: ignore[arg-type]
        model_name, low_cpu_mem_usage=True
    )
    model.eval().to(device)
    for p in model.parameters():
        p.requires_grad_(False)
    n = sum(p.numel() for p in model.parameters())
    print(f"[clip] {model_name}  params={n/1e6:.1f}M  device={device}")
    if n > 2e9:
        raise RuntimeError(f"{n/1e9:.2f}B params exceeds the 2B limit")
    return model, device


def export_clip_bundle(model, path: str) -> None:
    """
    Save CLIP as PLAIN DATA ONLY -- a config dict (numbers/strings, no code)
    and a state_dict (tensors, no code). This is what makes it safe to load
    with weights_only=True later: that safe mode refuses anything that isn't
    primitive data, which is exactly why torch.save(model) (the whole live
    nn.Module, with its class reference baked into the pickle) is the risky
    version and this plain-data version is not. Deliberately does NOT save
    the whole model object.
    """
    torch.save({
        "config": model.config.to_dict(),
        "state_dict": model.state_dict(),
    }, path)
    import os
    print(f"[clip] exported bundle to {path} ({os.path.getsize(path)/1e6:.0f} MB, plain data only)")


def load_clip_from_bundle(path: str, device: str | None = None):
    """
    Rebuild CLIP from a bundle written by export_clip_bundle() -- NO network
    call, NO HuggingFace cache dependency. Architecture is reconstructed from
    the saved config (pure Python object construction), then weights are
    loaded from the saved state_dict. Use this for a fully offline-portable
    checkpoint; use load_clip() for the normal cached-download path.
    """
    from transformers import CLIPVisionConfig, CLIPVisionModelWithProjection

    device = device or ("cuda" if torch.cuda.is_available() else "cpu")
    bundle = torch.load(path, map_location="cpu", weights_only=True)
    config = CLIPVisionConfig(**bundle["config"])
    model = CLIPVisionModelWithProjection(config)
    model.load_state_dict(bundle["state_dict"])
    model.eval().to(device)
    for p in model.parameters():
        p.requires_grad_(False)
    n = sum(p.numel() for p in model.parameters())
    print(f"[clip] loaded from bundle {path}  params={n/1e6:.1f}M  device={device}  (no network used)")
    return model, device


@torch.no_grad()
def embed(
    model,
    device: str,
    dataset: ViewDataset,
    feature: str = "proj",
    batch_size: int = 32,
    num_workers: int = 8,
    max_forward_batch: int = 128,
    amp: bool | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """
    amp: None (default) auto-enables fp16 autocast whenever device=="cuda".
    Pass amp=False to force full fp32 even on GPU -- this is the diagnostic
    knob for the exact failure mode where GPU embeddings collapse toward
    chance-level separation while the identical CPU/fp32 run works fine.
    Two live causes produce that signature: fp16 genuinely destroying a
    faint signal, or a still-rough fp16 kernel on very new GPU architectures
    (e.g. sm_120/Blackwell, whose PyTorch support is recent). amp=False
    isolates precision as a variable so you can tell which you're looking at.
    """
    dl = DataLoader(
        dataset,
        batch_size=batch_size,
        shuffle=False,
        num_workers=num_workers,
        pin_memory=(device == "cuda"),
        persistent_workers=num_workers > 0,
    )
    n_views = len(dataset.views)
    use_amp = (device == "cuda") if amp is None else amp

    if amp is False and device == "cuda":
        # --no-amp turning off torch.autocast is NOT the same as genuine fp32.
        # PyTorch defaults TF32 (reduced ~10-bit mantissa) ON for CUDA matmul
        # and cuDNN convolutions on any tensor-core GPU, independent of
        # autocast entirely. If the caller explicitly asked for full
        # precision, honour that literally -- otherwise "--no-amp" is a lie
        # on any Ampere-or-later card, this one (Blackwell/sm_120) included.
        prev_mm = torch.backends.cuda.matmul.allow_tf32
        prev_cudnn = torch.backends.cudnn.allow_tf32
        torch.backends.cuda.matmul.allow_tf32 = False
        torch.backends.cudnn.allow_tf32 = False
        print("[embed] amp=False: also disabled TF32 (was on by default) for genuine fp32")
    else:
        prev_mm = prev_cudnn = None

    feats, labels = [], []
    for i, (x, y) in enumerate(dl):
        b = x.shape[0]
        x = x.view(b * n_views, 3, RES, RES)
        chunks = []
        for j in range(0, x.shape[0], max_forward_batch):
            xc = x[j:j + max_forward_batch].to(device, non_blocking=True)
            with torch.autocast("cuda", dtype=torch.float16, enabled=use_amp):
                vout = model.vision_model(pixel_values=xc)
                pooled = vout.pooler_output
                if feature == "pooled":
                    f = pooled
                elif feature == "proj":
                    f = model.visual_projection(pooled)
                elif feature == "both":
                    f = torch.cat([pooled, model.visual_projection(pooled)], dim=-1)
                else:
                    raise ValueError(f"unknown feature {feature!r}")
            chunks.append(f.float().cpu())
        f_all = torch.cat(chunks, 0)
        feats.append(f_all.view(b, n_views, -1).numpy().astype(np.float16))
        labels.append(y.numpy())
        if i % 20 == 0:
            print(f"  batch {i}/{len(dl)}", flush=True)

    if prev_mm is not None:
        torch.backends.cuda.matmul.allow_tf32 = prev_mm
        torch.backends.cudnn.allow_tf32 = prev_cudnn

    return np.concatenate(feats, 0), np.concatenate(labels, 0)