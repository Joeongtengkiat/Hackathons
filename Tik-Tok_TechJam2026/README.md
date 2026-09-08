# AIGC Image Detector

A tool that detects AI-generated images and measures how well that detection holds up after common post-processing — JPEG compression, blurring, resizing, and social media reposts.

---

## About this project

### How it works

We use **CLIP ViT-L/14** as a frozen feature extractor and train a small MLP head on top of its embeddings. The key insight is that before extracting features, every image is re-encoded as a JPEG — this removes format-based shortcuts where the model might learn "real photos are JPEG, AI images are PNG" instead of learning actual detection.

Training uses augmented images (JPEG compression, blur, noise, downscaling, social repost chains) so the model is not fooled by post-processing. The model is penalised on the worst-case augmented view during training, making it robust to the kinds of transformations images go through online.

We report **AUC** as our primary metric rather than accuracy because the best decision threshold shifts between different image sets. AUC measures how well the model separates real from AI images regardless of threshold, which is a more honest measure of model quality when the operating threshold is not fixed.

### Development tools

- Visual Studio Code

### Models and APIs

- **CLIP ViT-L/14** (`openai/clip-vit-large-patch14`) — frozen vision tower, bundled inside `bundle.pt`

### Libraries and frameworks

| Library | Purpose |
| --- | --- |
| PyTorch | Model training and inference |
| Hugging Face Transformers | CLIP model loading |
| Hugging Face Datasets | Downloading training datasets |
| Pillow | Image loading and processing |
| scikit-learn | AUC and other metrics |
| NumPy | Numerical operations |
| PyQt6 | Desktop GUI |
| matplotlib | Charts and visualisations |

### Datasets used

**Training**

| Dataset | Link |
| --- | --- |
| SID_Set | [HuggingFace](https://huggingface.co/datasets/saberzl/SID_Set) |
| MS COCOAI / Defactify | [HuggingFace](https://huggingface.co/datasets/Rajarshi-Roy-research/Defactify_Image_Dataset) |
| AI vs Human Generated | [Kaggle](https://www.kaggle.com/datasets/alessandrasala79/ai-vs-human-generated-dataset) |
| 130k Real vs Fake Faces | [Kaggle](https://www.kaggle.com/datasets/shreyanshpatel1/130k-real-vs-fake-face) |
| Unsplash Lite | [github.com/unsplash/datasets](https://github.com/unsplash/datasets) |

**Benchmark / Validation**

| Dataset | Notes |
| --- | --- |
| MS COCO val2017 | Held-out real images, never used in training |
| DALL·E Advanced | Organiser-provided synthetic benchmark images |

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

Requires Python 3.9+. A GPU is used automatically if available; CPU works fine too.

### 2. Download the model

The trained model (`bundle.pt`, ~1.1 GB) is available on the [Releases page](https://github.com/BruhClient/Tik-Tok-Jam-2026/releases/tag/Model_for_tiktokTechJam2026).

1. Download `bundle.pt` from the Releases page
2. Place it in the `models/` folder in this repo

### 3. Run

```bash
python detect.py sample_data        # scores a folder, outputs predictions.json
python robustness.py sample_data    # tests the model under various transforms
python gui.py                       # opens the desktop app
```

---

## Results

Tested on a sample set of 100 real and 100 AI-generated images:

| Metric | Score |
| --- | --- |
| AUC | **0.921** |
| Recall (AI images caught) | **78%** |
| False positive rate | **6%** |

The model is calibrated to minimise false positives — incorrectly flagging a real photo as AI is the more costly mistake.

---

## Usage

### Scoring images

```bash
python detect.py <folder>
python detect.py <folder> --best-threshold    # also shows the best F1 threshold
```

Output is written to `predictions.json`:

```json
[
  {"image_path": "img_0001.jpg", "pred": 0.8731},
  {"image_path": "img_0002.png", "pred": 0.0412}
]
```

`pred` is the probability the image is AI-generated (0 = real, 1 = AI).

**Labeled folders** (with `real/` and `ai/` subfolders) also get accuracy, AUC, F1 and a confusion matrix printed to the terminal.

### Robustness sweep

```bash
python robustness.py <folder>
```

Tests the model at 5 severity levels across 10 post-processing transforms (JPEG, blur, rescale, crop, noise, etc.) and writes a report to `robustness_report.json`.

### Desktop app

```bash
python gui.py
```

Upload a folder and get score distributions, ROC curves, a confusion matrix, per-image predictions, and the robustness sweep — all in one window.

---

## Repository layout

```
detect.py          score a folder -> predictions.json
robustness.py      robustness sweep -> robustness_report.json
gui.py             desktop app
requirements.txt
models/            place bundle.pt here (gitignored)
app/               inference, metrics, transforms, GUI backend
training_process/  training pipeline (data prep, embedding, head training)
```

---

## Troubleshooting

**Model not found error** — make sure `bundle.pt` is in the `models/` folder. Download it from the [Releases page](https://github.com/BruhClient/Tik-Tok-Jam-2026/releases/tag/Model_for_tiktokTechJam2026).

**Everything scores as AI / everything scores as real** — the model's decision threshold is not 0.5. Run with `--best-threshold` to find the optimal threshold for your data.

**No labels found** — for metrics, your folder needs `real/` and `ai/` subfolders. Without them the tool still scores images, just without accuracy metrics.
