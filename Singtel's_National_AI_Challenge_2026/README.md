# Shaun-Coders — NAISC Singtel 2026

Customer churn prediction pipeline with adaptive drift detection and mitigation.

## How to Run

```bash
python ./src/main.py --train_data_filepath <path_to_train> --test_data_filepath <path_to_test>
```

Output: `prediction.csv` in the project root with columns `CustomerID` and `probability_score`.

---

## Pipeline Overview

### 1. Drift Detection (`detector.py`)

Drift is detected across two axes:

**Univariate** — each feature is tested individually between the training and test distributions, and across training months using a fixed first-month baseline.
- Numerical features: Kolmogorov-Smirnov test + PSI + Wasserstein distance
- Categorical features: Chi-square / TVD depending on cardinality
- Benjamini-Hochberg correction applied for multiple comparisons

**Multivariate** — the joint distribution is analysed as a whole.
- CBDT (Classifier-Based Drift Test): a Random Forest domain classifier distinguishes train vs test; AUROC and H-divergence measure separation
- MMD (Maximum Mean Discrepancy) with RBF kernel
- PCA-based KS test across principal components
- Covariance matrix shift (Frobenius norm)
- Concept drift: leave-one-month-out AU-PRC to detect P(Y|X) instability
- Correlation drift: pairwise Fisher z-test for structural relationship changes
- Missing value drift: null-rate shifts across months and train vs test

### 2. Drift Mitigation (`mitigator.py`)

Sample weights are computed for every training row to rebalance the training distribution toward the test distribution.

- **Proximity reweighting**: numerical features are weighted by proximity to the test median, scaled by PSI severity
- **Frequency reweighting**: categorical features are weighted by the test/train frequency ratio
- **Temporal similarity weighting**: training months that resemble the test period are upweighted using `exp(-3 * avg_psi)`
- **CBDT density-ratio correction**: a domain classifier estimates `p(test|x) / p(train|x)` for each training row — the theoretically optimal covariate-shift correction
- **Concept drift weighting**: months with low leave-one-out AU-PRC are downweighted proportionally
- **Missing value indicators**: binary flags added as features for columns with drifting null rates
- **Drift-robust features**: within-month z-scores and ratio features for the top CBDT drift drivers

All component weights are combined, log-compressed, and normalised to mean = 1.

### 3. Preprocessing (`preprocessor.py`)

- Categorical encoding and numerical imputation
- Feature engineering on raw columns
- Fit on training set only; transformation applied to both train and test

### 4. Model Training (`trainer.py`)

- LightGBM binary classifier optimised for AU-PRC
- Trained with drift-corrected sample weights from the mitigation step
- Zero-importance feature pruning after the first fit

### 5. Pseudo-Labelling

Iterative self-training loop using high-confidence test predictions as additional training signal.
- High-confidence threshold: 0.80 (churn) / 0.20 (no churn)
- Pseudo-sample weight: 3.0
- Up to 20 rounds with early stopping (patience = 2, delta = 0.001)
- Labels are refreshed each round; rows that fall below confidence thresholds are dropped

---

---

## Dashboard

An interactive Streamlit dashboard visualises the drift analysis and model performance. Run the pipeline first to generate `report.json`, then launch the dashboard:

```bash
pip install -r requirements.txt
python ./src/main.py --train_data_filepath <path_to_train> --test_data_filepath <path_to_test>
streamlit run src/dashboard.py
```

### Dashboard Tabs

| Tab | Contents |
|-----|----------|
| **Data** | Dataset overview: row counts, churn rate, missing values, feature type breakdown, train vs test comparison |
| **Drift** | Sub-tabs: Feature Drift (PSI bar chart, severity pie), Multivariate (CBDT AUROC, H-divergence, impact scores), Concept Drift (monthly AU-PRC timeline), Label & Volume (churn rate / record count drift flags), Feature Importance (normalised gain heatmap across months) |
| **Mitigation** | Per-feature drift type, description, and mitigation strategy; global mitigation strategies applied |
| **Preprocessing** | Feature count funnel, engineered/dropped features, imputation strategy, encoding, and scaling details |
| **Results** | Train vs test AU-PRC, generalisation gap, total runtime, pipeline configuration table |

---

## Project Structure

```
├── requirements.txt     # Python dependencies
src/
├── dashboard.py         # Streamlit dashboard
├── main.py              # Pipeline entry point
├── data_loader.py       # Data loading and train/test splitting
├── detector.py          # Univariate + multivariate drift detection
├── mitigator.py         # Sample weight computation and feature augmentation
├── preprocessor.py      # Feature engineering and encoding
├── trainer.py           # LightGBM training and evaluation
└── hackathonReporter.py # Console reporting and prediction export
```

## Metric

Primary metric: **AU-PRC** (Area Under the Precision-Recall Curve)
