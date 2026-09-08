# training_process

The pipeline that **produces** the detector. Everything in the repo root
(`detect.py`, `gui.py`, `app/`) only ever *consumes* a finished `bundle.pt`;
this directory is where that bundle is made — data prep, CLIP feature caching,
head training, evaluation, and packaging.

It is deliberately self-contained and flat: modules import each other by bare
name (`import clipfeat as CF`, `from train import Head`), and `run_all.py`
invokes the scripts as subprocesses. Run everything **from inside this folder**.

```
cd training_process
python run_all.py --day 1        # plumbing + the shortcut audit
python run_all.py --day 2        # the real model + ablations
python run_all.py --day 3        # bonus probe + error analysis
```

`run_all.py` is unattended convenience; the intended use is day by day, reading
each stage's output before the next (Day 1's audit in particular should change
what you do). `smoke_test.py` runs the whole path on a tiny synthetic set first.

## The flow

```
prepare_*.py   raw dataset  ->  data/<split>/{real,ai}/        (binary, geometry-normalised)
embed.py       images       ->  cache/<name>/                  (CLIP features, N augmented views)
train.py       cached feats ->  runs/<name>/head.pt            (only the MLP head is trained)
export_bundle  head.pt      ->  bundle.pt                      (tower + head + calibration, one file)
                                     |
                                     v
                              ../models/bundle.pt  ->  consumed by app/detectors/clip_head.py
```

Only the head is trained; the CLIP ViT-L/14 tower is frozen. Features are cached
once (`embed.py`) so the head can be re-trained for free — which is what makes
the ablation sweep in `run_all.py` cheap.

## Files by role

**Data preparation** — each writes a `real/` + `ai/` split
- `prepare_sid.py` — SID_Set (`saberzl/SID_Set`); the primary source. `--audit`
  measures the "square ⇒ fake" geometry shortcut; normalisation is on by default.
- `prepare_cocoai.py` — MS COCOAI / Defactify
- `prepare_kaggle.py` — Kaggle "AI vs Human generated"
- `prepare_custom.py` — adapter for a local train/test split you already have
- `setup_coco.py` — COCO as training reals **and** a held-out benchmark
- `fetch_unsplash.py` — extra Unsplash reals to broaden the real distribution
- `split_pool.py` — split a flat, generator-separated pool into train/val/test
- `make_smoke_data.py` — a tiny synthetic set so the pipeline can be smoke-tested
- `_geom.py` — shared geometry helpers (`geometry_params`, `apply_geometry`, `list_images`, `IMG_EXT`) used by the prepare_* scripts and split_pool

**Features & training**
- `augment.py` — the degradation bank (JPEG/blur/resize/noise/…) + `normalise_source`
- `clipfeat.py` — the frozen CLIP half: preprocessing, tower, bundle load/save
- `embed.py` — precompute and cache CLIP embeddings (multi-view)
- `data_loader.py` — one entry point from a raw download to a loader
- `train.py` — trains the head on cached features (`Head`, the min–max/CVaR objective)

**Packaging & inference**
- `export_bundle.py` — combine a trained `head.pt` + CLIP into one portable `bundle.pt`
- `load_bundle_example.py` — minimal consumer: load a bundle, score a folder
- `detector.py` — the inference API (`Detector.predict_folder`) the GUI builds on

**Evaluation & analysis**
- `benchmark_official.py` — the official validation benchmark (COCO val2017 + DALL·E Advanced)
- `evaluate.py` — the robustness grid (clean vs each transform × severity)
- `error_analysis.py` — surface every misclassified image to actually look at
- `probe_tampered.py` — bonus probe on tampered images (SID_Set label 2)
- `recalibrate.py` — re-pick a head's **threshold** without retraining
- `diagnose_weights.py` / `gpu_testing.py` — CUDA/weights sanity checks

**Orchestration**
- `run_all.py` — the day 1/2/3 sequence
- `smoke_test.py` — end-to-end on `make_smoke_data.py` output

## Constants that must not drift

`augment.py` and `clipfeat.py` define the source re-encode (JPEG q85–98), the
`RES=224` bicubic preprocessing, the CLIP normalisation constants and the
200 MP decode cap. The inference repo's `app/detectors/clip_head.py` **mirrors
these deliberately** — a bundle is only valid with the exact preprocessing it
was trained under, so changing one side without the other silently corrupts
every score. See the pin table in the root `README.md`.

## Note on calibration

`recalibrate.py` here re-picks a head's threshold upstream (before export);
the inference repo's `calibrate.py` does the equivalent downstream on a finished
bundle. They should agree on method (threshold only, scores untouched) — pick
one place to do it so a shipped operating point has a single source of truth.
