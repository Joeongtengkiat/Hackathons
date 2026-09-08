"""
The data loader. One entry point: point it at your raw downloaded dataset,
get back ready-to-use PyTorch DataLoaders for train/val/test.

Everything here is a thin wrapper around two files that already exist —
prepare_custom.py (raw folder -> real/fake layout on disk, with an optional
geometry-shortcut fix) and clipfeat.py (that layout -> multi-view CLIP-ready
tensors -> a torch DataLoader). Nothing is duplicated; this just chains them
so you don't have to run three separate CLI commands and remember the flags
in the right order.

Two ways to use it:

  1. As a library, in your own script or notebook:

        from data_loader import build_dataloaders

        loaders = build_dataloaders(
            train_root="path/to/downloaded/train",   # has ai/ and real/
            test_root="path/to/downloaded/test",      # has ai/ and real/
        )
        for x, y in loaders.train:
            x: torch.Tensor   # (batch, views, 3, 224, 224)
            y: torch.Tensor   # (batch,) int64, 0=real 1=fake, -1=failed to load
            ...

  2. As a CLI, to sanity-check your data before spending GPU time on it:

        python data_loader.py --train-root path/to/downloaded/train \
                              --test-root  path/to/downloaded/test

     This lays the data out (if not already done), builds all three loaders,
     pulls exactly one batch from each, and prints shapes + label balance —
     the same kind of check make_smoke_data.py exists for, but against your
     REAL data instead of a synthetic stand-in.

Batching note: what you get back here is NOT the same as running embed.py.
These loaders yield raw (un-encoded) image tensors — CLIP hasn't touched them
yet. That's the correct thing for training-time random augmentation (a new
random degradation chain every epoch), and it's also exactly what embed.py
does internally to build the one-time feature cache. If you're precomputing
embeddings for repeated head-training experiments (the recommended path —
see README), use embed.py directly; it already calls into these same pieces.
Reach for this module when you want the loaders themselves, not the cache.
"""
from __future__ import annotations

import argparse
import random
from dataclasses import dataclass
from pathlib import Path

import torch
from torch.utils.data import DataLoader

import augment as A
import clipfeat as CF
import prepare_custom as PC


@dataclass
class DataLoaders:
    train: DataLoader
    val: DataLoader
    test: DataLoader
    train_samples: list[CF.Sample]
    val_samples: list[CF.Sample]
    test_samples: list[CF.Sample]


def _is_laid_out(root: Path) -> bool:
    """True if root already has the real/ and fake/ structure scan_dir expects."""
    return (root / "real").is_dir() and (root / "fake").is_dir()


def ensure_layout(
    train_root: Path,
    test_root: Path,
    out: Path,
    val_frac: float = 0.15,
    mode: str = "hardlink",
    normalise_geometry: bool = False,
    quality: int = 92,
    seed: int = 0,
) -> None:
    """
    Idempotent: if out/train, out/val, out/test already look laid out, does
    nothing. Otherwise calls prepare_custom.layout() to build them from your
    raw train_root/{ai,real} and test_root/{ai,real} folders.
    """
    if all(_is_laid_out(out / split) for split in ("train", "val", "test")):
        print(f"[data_loader] {out} already laid out — skipping prepare_custom.layout()")
        return
    print(f"[data_loader] {out} not laid out yet — building it from {train_root} / {test_root}")
    PC.layout(
        train_root=train_root, test_root=test_root, out=out, val_frac=val_frac,
        mode=mode, normalise_geometry=normalise_geometry, quality=quality, seed=seed,
    )


def _make_dataset(
    samples: list[CF.Sample],
    split: str,
    views: int,
    max_ops: int,
    eval_conditions: list[A.NamedCondition] | None,
    preproc: str,
    renormalise: bool,
    seed: int,
) -> CF.ViewDataset:
    """
    train/val: view 0 is clean, the rest are random degradation chains — this
    is what makes the worst-case-view training in train.py possible.

    test: one view PER CONDITION in eval_conditions (defaults to the brief's
    EVAL_GRID) so evaluate.py-style robustness scoring works directly off
    these loaders too, not just off a pre-built cache.
    """
    if split == "test":
        conds = eval_conditions if eval_conditions is not None else A.EVAL_GRID
        view_fns = [CF.condition_view(c) for c in conds]
    else:
        view_fns = [CF.clean_view()] + [CF.random_view(max_ops=max_ops) for _ in range(views - 1)]

    return CF.ViewDataset(
        samples, view_fns, preproc=preproc, renormalise=renormalise, seed=seed,
    )


def build_dataloaders(
    train_root: str | Path,
    test_root: str | Path,
    out: str | Path = "data",
    val_frac: float = 0.15,
    views: int = 6,
    max_ops: int = 3,
    eval_conditions: list[A.NamedCondition] | None = None,
    preproc: str = "resize",
    batch_size: int = 32,
    num_workers: int = 8,
    normalise_geometry: bool = False,
    layout_mode: str = "hardlink",
    seed: int = 0,
    shuffle_train: bool = True,
) -> DataLoaders:
    """
    The one function this file exists for. Lays out your raw data if needed,
    scans it, wraps it in ViewDatasets, and returns train/val/test DataLoaders.

    train_root, test_root: your DOWNLOADED folders, each containing ai/ and
        real/ subfolders (per what you described: 4k/4k train, 1k/1k test).
    out: where the real/fake-structured copy gets written. Reused on repeat
        calls (idempotent — see ensure_layout).
    views: view 0 is always clean; the rest are random degradation chains,
        used for train and val. Not used for test (see eval_conditions).
    eval_conditions: which conditions to build test views from. Defaults to
        augment.EVAL_GRID (the brief's exact settings). Pass
        augment.EVAL_GRID + augment.HELDOUT_GRID + augment.CHAIN_GRID for the
        full robustness sweep evaluate.py runs by default.
    normalise_geometry: only set this to True if `prepare_custom.py audit`
        on train_root flagged a shape/size shortcut. Leave False otherwise —
        it costs image quality you don't need to spend.
    """
    train_root, test_root, out = Path(train_root), Path(test_root), Path(out)

    ensure_layout(
        train_root, test_root, out, val_frac=val_frac, mode=layout_mode,
        normalise_geometry=normalise_geometry, seed=seed,
    )

    train_samples = CF.scan_dir(out / "train")
    val_samples = CF.scan_dir(out / "val")
    test_samples = CF.scan_dir(out / "test")

    train_ds = _make_dataset(train_samples, "train", views, max_ops, None,
                             preproc, renormalise=True, seed=seed)
    val_ds = _make_dataset(val_samples, "val", views, max_ops, None,
                           preproc, renormalise=True, seed=seed + 1)
    test_ds = _make_dataset(test_samples, "test", views, max_ops, eval_conditions,
                            preproc, renormalise=True, seed=seed + 2)

    common = dict(batch_size=batch_size, num_workers=num_workers,
                 pin_memory=torch.cuda.is_available(),
                 persistent_workers=num_workers > 0)
    train_dl = DataLoader(train_ds, shuffle=shuffle_train, **common)
    val_dl = DataLoader(val_ds, shuffle=False, **common)
    test_dl = DataLoader(test_ds, shuffle=False, **common)

    print(f"[data_loader] train={len(train_samples)}  val={len(val_samples)}  "
         f"test={len(test_samples)}  views(train/val)={views}  "
         f"conditions(test)={len(eval_conditions or A.EVAL_GRID)}  preproc={preproc}")

    return DataLoaders(train_dl, val_dl, test_dl, train_samples, val_samples, test_samples)


# --------------------------------------------------------------------------
# CLI: sanity-check your real data before spending GPU time on it
# --------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train-root", required=True, type=Path)
    ap.add_argument("--test-root", required=True, type=Path)
    ap.add_argument("--out", default=Path("data"), type=Path)
    ap.add_argument("--val-frac", type=float, default=0.15)
    ap.add_argument("--views", type=int, default=6)
    ap.add_argument("--batch-size", type=int, default=8,
                    help="small on purpose — this CLI only pulls one batch per split")
    ap.add_argument("--num-workers", type=int, default=0,
                    help="0 for the CLI check, so any error prints in the main process")
    ap.add_argument("--preproc", default="resize", choices=["resize", "nativecrop"])
    ap.add_argument("--normalise-geometry", action="store_true")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    loaders = build_dataloaders(
        train_root=args.train_root, test_root=args.test_root, out=args.out,
        val_frac=args.val_frac, views=args.views, batch_size=args.batch_size,
        num_workers=args.num_workers, preproc=args.preproc,
        normalise_geometry=args.normalise_geometry, seed=args.seed,
    )

    for name, dl in [("train", loaders.train), ("val", loaders.val), ("test", loaders.test)]:
        x, y = next(iter(dl))
        n_real, n_fake, n_failed = int((y == 0).sum()), int((y == 1).sum()), int((y < 0).sum())
        print(f"\n[{name}] one batch: x.shape={tuple(x.shape)} dtype={x.dtype}  "
             f"y={y.tolist()}")
        print(f"[{name}] batch label balance: real={n_real} fake={n_fake} failed={n_failed}")

    print("\nPASS — all three loaders yield batches with the expected shape and both classes present.")
    print("Next: either train directly against these loaders, or run embed.py to cache")
    print("CLIP features once and iterate on the head for free (recommended — see README).")


if __name__ == "__main__":
    main()