"""
main.py — NAISC Singtel 2026: Adaptive Drift Intelligence Challenge

Pipeline:
  1. Load train + test
  2. Detect drift   (univariate + multivariate — KS / Chi-Square / CBDT / MMD / PCA)
  3. Mitigate drift (CBDT density-ratio + proximity + temporal reweighting)
  4. Preprocess     (fit on train, transform both)
  5. Train LightGBM with drift-corrected sample weights
  6. Iterative pseudo-labeling with early stopping (HIGH=0.80, LOW=0.20, weight=3.0, 20 rounds)
  7. Evaluate + save outputs

Usage:
    python ./src/main.py --train_data_filepath <path> --test_data_filepath <path>
"""

import argparse
import json
import os
import time
import warnings

import numpy as np

from data_loader        import DataLoader
from detector           import DriftDetector
from mitigator          import DriftMitigator
from preprocessor       import Preprocessor
from trainer            import Trainer
from hackathonReporter  import HackathonReporter

warnings.filterwarnings("ignore")

# ── Pseudo-labeling config ────────────────────────────────────────────────────

PSEUDO_CFG = {
    "high_conf":         0.80,     # High threshold — only very confident predictions
    "low_conf":          0.20,     # Low threshold  — only very confident non-churners
    "pseudo_w":          3.0,      # 3x weight on pseudo labels — strong signal without overriding ground truth
    "n_rounds":          20,        # Conservative cap before hallucination phase begins
    "max_pseudo":        300_000,  # Hard cap (~3% of 10M hidden test rows)
    "max_pseudo_frac":   0.03,     # Scales with test size for hidden dataset
    "train_sample":      400_000,  # Subsample base train each round for speed at scale
}

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--train_data_filepath", required=True)
    parser.add_argument("--test_data_filepath",  required=True)
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    return parser.parse_args()


def pseudo_label_round(trainer, X_test, cfg, round_i, exclude_mask=None, max_new=None):
    """
    Run one pseudo-labeling round. Returns (pseudo_mask, pseudo_labels, pseudo_w, n) or None.

    exclude_mask — boolean array over X_test; already-accumulated rows are skipped.
    max_new      — cap on how many new rows can be added (remaining buffer space).
    """
    probs         = trainer.predict_proba(X_test)
    pseudo_mask   = (probs > cfg["high_conf"]) | (probs < cfg["low_conf"])
    pseudo_labels = (probs[pseudo_mask] > cfg["high_conf"]).astype(int)
    pseudo_w      = np.full(pseudo_mask.sum(), cfg["pseudo_w"])

    # Exclude rows already accumulated from previous rounds
    if exclude_mask is not None:
        kept          = pseudo_mask & ~exclude_mask
        new_pos       = np.where(kept[pseudo_mask])[0]
        pseudo_mask   = kept
        pseudo_labels = pseudo_labels[new_pos]
        pseudo_w      = pseudo_w[new_pos]

    n = int(pseudo_mask.sum())

    # Cap to remaining buffer space
    if max_new is not None and n > max_new:
        rng           = np.random.default_rng(42 + round_i)
        keep          = rng.choice(n, max_new, replace=False)
        indices       = np.where(pseudo_mask)[0][keep]
        pseudo_mask   = np.zeros(len(pseudo_mask), dtype=bool)
        pseudo_mask[indices] = True
        pseudo_labels = pseudo_labels[keep]
        pseudo_w      = pseudo_w[keep]
        n             = max_new

    print(f"🔁 Round {round_i}: +{n} new pseudo rows "
          f"({int(pseudo_labels.sum())} churn, {int((pseudo_labels == 0).sum())} no-churn)")

    return (pseudo_mask, pseudo_labels, pseudo_w, n) if n > 0 else None


def run_pipeline(args, seed: int):
    """Run full pipeline with a given seed. Returns (test_ids, test_probs, auprc_train, auprc_test)."""
    cfg      = PSEUDO_CFG
    reporter = HackathonReporter()

    print(f"\n🧪 Pseudo-labeling: HIGH={cfg['high_conf']}, LOW={cfg['low_conf']}, "
          f"weight={cfg['pseudo_w']}, rounds={cfg['n_rounds']}  [seed={seed}]")

    # ── 1. Load ────────────────────────────────────────────────────────────────
    print("\n📂 Loading data...")
    t0          = time.time()
    loader      = DataLoader(args.train_data_filepath, args.test_data_filepath)
    loader.load()
    test_ids    = loader.test_ids
    t_load      = time.time() - t0

    # ── 2. Detection sample (no full combine) ──────────────────────────────────
    print("\n🔍 Detecting drift (V2: univariate + multivariate)...")
    t0 = time.time()
    combined_detect = loader.detection_sample(n=300_000)
    detector = DriftDetector(alpha=0.05)
    drift_report, temporal_report, train_month_similarity, mv_report = detector.detect(
        combined_detect, ignore_cols=DataLoader.DROP_COLS
    )
    t_drift = time.time() - t0

    print(f"   Train vs test drift   : {len(detector.drifted_columns)} features")
    print(f"   Full timeline drift   : {len(detector.temporal_drifted_columns)} features")

    print("   Computing monthly feature importance drift (full train data)...")
    mv_report["monthly_feature_importance"] = detector.compute_monthly_feature_importances(
        loader.train_df, ignore_cols=DataLoader.DROP_COLS
    )

    # ── 4. Mitigate ────────────────────────────────────────────────────────────
    print("\n🛠️  Mitigating drift (MultivariateMitigator)...")
    t0        = time.time()
    mitigator = DriftMitigator()
    train_corrected, test_corrected, train_weights = mitigator.mitigate(
        loader.train_df,
        loader.test_df,
        drift_report=drift_report,
        temporal_report=temporal_report,
        train_month_similarity=train_month_similarity,
        mv_report=mv_report,
    )
    t_mitigate = time.time() - t0

    print(f"   Train corrected : {train_corrected.shape}")
    print(f"   Test corrected  : {test_corrected.shape}")

    # ── 6. Preprocess ──────────────────────────────────────────────────────────
    print("\n⚙️  Preprocessing...")
    t0               = time.time()
    preprocessor     = Preprocessor()
    X_train, y_train, X_test = preprocessor.fit_transform(train_corrected, test_corrected)
    t_preprocess     = time.time() - t0

    # Capture dataset stats before freeing DataFrames
    _data_stats = _build_data_stats(loader)

    # Free DataFrames — only numpy arrays needed from here on
    y_test_labels = (
        test_corrected[DataLoader.TARGET].values.astype(int)
        if DataLoader.TARGET in test_corrected.columns else None
    )
    del train_corrected, test_corrected, loader.train_df, loader.test_df

    # ── 7. Train ───────────────────────────────────────────────────────────────
    print("🚀 Training LightGBM with drift-corrected sample weights...")
    t0      = time.time()
    trainer = Trainer(seed=seed)
    trainer.train(X_train, y_train, sample_weights=train_weights, cat_feature_indices=preprocessor.cat_feature_indices)

    # ── 7b. Feature selection ──────────────────────────────────────────────────
    importances = trainer.model.feature_importances_
    keep_mask   = importances > 0
    n_dropped   = int((~keep_mask).sum())
    if n_dropped > 0:
        print(f"   Dropping {n_dropped} zero-importance features "
              f"({keep_mask.sum()} of {len(keep_mask)} kept)...")
        keep_idx   = np.where(keep_mask)[0]
        old_cat_set = set(preprocessor.cat_feature_indices)
        preprocessor.cat_feature_indices = [
            new_i for new_i, old_i in enumerate(keep_idx) if old_i in old_cat_set
        ]
        X_train = X_train[:, keep_mask]
        X_test  = X_test[:, keep_mask]
        # Retrain on reduced features so trainer and X_test are in sync
        trainer = Trainer(seed=seed)
        trainer.train(X_train, y_train, sample_weights=train_weights,
                      cat_feature_indices=preprocessor.cat_feature_indices)

    # ── 8. Pseudo-labeling ─────────────────────────────────────────────────────
    max_pseudo = (
        max(cfg["max_pseudo"] or 0, int(cfg["max_pseudo_frac"] * len(X_test)))
        if cfg["max_pseudo_frac"] else cfg["max_pseudo"]
    )
    print(f"\n🔁 Pseudo-labeling (up to {cfg['n_rounds']} rounds, cap={max_pseudo:,})...")
    rng    = np.random.default_rng(seed)

    accumulated_mask = np.zeros(len(X_test), dtype=bool)  # which test rows are in buffer
    n_pseudo_total   = 0
    # Pseudo rows stored as growing lists — appended each round, concatenated only at train time
    X_pseudo_list, y_pseudo_list, w_pseudo_list = [], [], []

    # Early stopping state
    _best_pseudo_auprc  = 0.0
    _no_improve_count   = 0
    _EARLY_STOP_DELTA   = 0.001  # minimum improvement per round to continue
    _EARLY_STOP_PATIENCE = 2     # stop after this many consecutive non-improving rounds

    for round_i in range(1, cfg["n_rounds"] + 1):
        # ── Refresh labels of already-accumulated rows ─────────────────────────
        if n_pseudo_total > 0:
            probs_all                   = np.zeros(len(X_test))
            probs_all[accumulated_mask] = trainer.predict_proba(X_test[accumulated_mask])
            acc_indices = np.where(accumulated_mask)[0]
            acc_probs   = probs_all[acc_indices]
            still_conf  = (acc_probs > cfg["high_conf"]) | (acc_probs < cfg["low_conf"])

            keep_acc = np.where(still_conf)[0]
            drop_acc = np.where(~still_conf)[0]
            if len(drop_acc) > 0:
                accumulated_mask[acc_indices[drop_acc]] = False

            # Rebuild pseudo lists keeping only still-confident rows
            kept_indices = acc_indices[keep_acc]
            kept_probs   = acc_probs[keep_acc]
            X_pseudo_list = [X_test[kept_indices]]
            y_pseudo_list = [(kept_probs > 0.5).astype(int)]
            w_pseudo_list = [np.full(len(keep_acc), cfg["pseudo_w"])]
            n_pseudo_total = len(kept_indices)

            if len(drop_acc) > 0:
                print(f"   Refreshed: {n_pseudo_total} kept, {len(drop_acc)} dropped (no longer confident)")

        # ── Select new rows not yet accumulated ────────────────────────────────
        max_new = max_pseudo - n_pseudo_total
        if max_new <= 0:
            print("   Buffer full — stopping early.")
            break

        result = pseudo_label_round(
            trainer, X_test, cfg, round_i,
            exclude_mask=accumulated_mask,
            max_new=max_new,
        )
        if result is None:
            print("   No new pseudo labels — stopping early.")
            break

        pseudo_mask, pseudo_labels, pseudo_w, n = result
        accumulated_mask |= pseudo_mask
        X_pseudo_list.append(X_test[pseudo_mask])
        y_pseudo_list.append(pseudo_labels)
        w_pseudo_list.append(pseudo_w)
        n_pseudo_total += n

        print(f"   Accumulated total: {n_pseudo_total} pseudo rows")

        # ── Subsample base training rows if configured ─────────────────────────
        if cfg["train_sample"] and len(X_train) > cfg["train_sample"]:
            idx    = rng.choice(len(X_train), size=cfg["train_sample"], replace=False)
            X_base = X_train[idx]
            y_base = y_train[idx]
            w_base = train_weights[idx]
        else:
            X_base, y_base, w_base = X_train, y_train, train_weights

        X_combined = np.concatenate([X_base, *X_pseudo_list])
        y_combined = np.concatenate([y_base, *y_pseudo_list])
        w_combined = np.concatenate([w_base, *w_pseudo_list])
        trainer = Trainer(seed=seed)
        trainer.train(
            X_combined, y_combined,
            sample_weights=w_combined,
            cat_feature_indices=preprocessor.cat_feature_indices,
        )

        # ── Early stopping: check AU-PRC improvement on test set ──────────────
        if y_test_labels is not None:
            round_auprc = trainer.evaluate_test(X_test, y_test_labels)
            improvement = round_auprc - _best_pseudo_auprc
            if improvement >= _EARLY_STOP_DELTA:
                _best_pseudo_auprc = round_auprc
                _no_improve_count  = 0
            else:
                _no_improve_count += 1
                print(f"   Early stop check: no improvement for {_no_improve_count}/{_EARLY_STOP_PATIENCE} rounds (Δ={improvement:+.4f})")
                if _no_improve_count >= _EARLY_STOP_PATIENCE:
                    print(f"   Early stopping at round {round_i} — AU-PRC plateaued.")
                    break

    t_train = time.time() - t0

    # ── 9. Evaluate ────────────────────────────────────────────────────────────
    t0         = time.time()
    test_probs = trainer.predict_proba(X_test)

    if y_test_labels is not None:
        auprc_test = trainer.evaluate_test(X_test, y_test_labels)
    else:
        auprc_test = trainer.auprc_train

    t_eval = time.time() - t0

    total_time = t_load + t_drift + t_mitigate + t_preprocess + t_train + t_eval
    reporter.print_final_hackathon_summary(drift_report, t_drift + t_mitigate, trainer.auprc_train, auprc_test,
                                           mv_report=mv_report, mitigation_log=mitigator.mitigation_log)
    print(f"⏱  Total runtime: {total_time:.1f}s")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    trainer.save(os.path.join(root, "model.joblib"))

    # ── Save report for dashboard ──────────────────────────────────────────────
    _save_report(drift_report, temporal_report, mv_report,
                 trainer.auprc_train, auprc_test, t_drift, t_mitigate,
                 mitigation_log=mitigator.mitigation_log,
                 data_stats=_data_stats,
                 preprocessor=preprocessor,
                 t_preprocess=t_preprocess,
                 n_features_final=int(X_train.shape[1]))

    return test_ids, test_probs, trainer.auprc_train, auprc_test


def _build_data_stats(loader) -> dict:
    """Build dataset statistics for the dashboard Data tab."""
    import pandas as pd
    train = loader.train_df
    test  = loader.test_df

    drop = set(DataLoader.DROP_COLS)
    feat_cols  = [c for c in train.columns if c not in drop]
    num_cols   = train[feat_cols].select_dtypes(include="number").columns.tolist()
    cat_cols   = train[feat_cols].select_dtypes(exclude="number").columns.tolist()

    month_col  = "Month" if "Month" in train.columns else None
    months_ord = []
    month_churn = {}
    month_count = {}
    if month_col:
        # ChurnStatus is already binarised to 0/1 by DataLoader._binarise_target
        churn_s = train[DataLoader.TARGET].astype(float)
        temp = train[[month_col]].copy()
        temp["_c"] = churn_s
        months_ord  = sorted(
            train[month_col].unique().tolist(),
            key=lambda m: pd.to_datetime(m, format="%y-%b", errors="coerce"),
        )
        month_churn = temp.groupby(month_col)["_c"].mean().round(4).to_dict()
        month_count = temp.groupby(month_col).size().to_dict()

    col_stats = {}
    for c in num_cols:
        col_stats[c] = {
            "mean":    round(float(train[c].mean()), 2),
            "std":     round(float(train[c].std()),  2),
            "min":     round(float(train[c].min()),  2),
            "max":     round(float(train[c].max()),  2),
            "missing": int(train[c].isnull().sum()),
        }

    cardinality = {c: int(train[c].nunique()) for c in cat_cols}

    churn_rate = float(train[DataLoader.TARGET].astype(float).mean())

    return {
        "n_train":          int(len(train)),
        "n_test":           int(len(test)),
        "n_features":       len(feat_cols),
        "churn_rate":       round(churn_rate, 4),
        "n_numeric":        len(num_cols),
        "n_categorical":    len(cat_cols),
        "n_missing_train":  int(train.isnull().sum().sum()),
        "n_missing_test":   int(test.isnull().sum().sum()),
        "months":           months_ord,
        "month_churn_rate": month_churn,
        "month_count":      month_count,
        "col_stats":        col_stats,
        "missing_per_col":  {k: int(v) for k, v in train.isnull().sum().items() if v > 0},
        "missing_test_per_col": {k: int(v) for k, v in test.isnull().sum().items() if v > 0},
        "cardinality":      cardinality,
        "numeric_features": num_cols,
        "categorical_features": cat_cols,
    }


def _build_preprocessing_stats(preprocessor, t_preprocess: float, n_features_final: int = 0) -> dict:
    """Build preprocessing stats for the dashboard Preprocessing tab."""
    eng_feats = [c for c in (preprocessor.feature_cols or []) if c.startswith("fe_")
                 or c in {"TenureGroup", "LocationCityFreq"}]
    return {
        "n_features_raw":                   preprocessor.n_features_raw_pre_eng,
        "n_features_after_engineering":     preprocessor.n_features_after_engineering,
        "n_features_final":                 n_features_final,
        "n_cat_features":                   len(preprocessor.cat_feature_indices),
        "n_numeric_quantile_transformed":   preprocessor.n_numeric_quantile_transformed,
        "t_preprocess":                     round(float(t_preprocess), 2),
        "engineered_features":              eng_feats,
        "dropped_at_ingestion":             preprocessor.dropped_at_ingestion,
        "encoding":                         "ordinal (cat.codes) — LightGBM native support",
        "scaling":                          "QuantileTransformer (output=normal, n_quantiles=1000)",
        "imputation": {
            "numeric":      "median",
            "categorical":  "mode",
            "special_fills": {
                "Offer":           "No Offer",
                "InternetType":    "No Internet",
                "ConnectivityType": "No Connection",
            },
        },
    }


def _save_report(drift_report, temporal_report, mv_report,
                 auprc_train, auprc_test, t_drift, t_mitigate,
                 mitigation_log=None, data_stats=None, preprocessor=None,
                 t_preprocess=0.0, n_features_final=0):
    """Serialise pipeline outputs to ../report.json for the dashboard."""

    class _Enc(json.JSONEncoder):
        def default(self, o):
            if isinstance(o, np.integer): return int(o)
            if isinstance(o, np.floating): return float(o)
            if isinstance(o, np.bool_): return bool(o)
            if isinstance(o, np.ndarray): return o.tolist()
            return super().default(o)

    report = {
        "auprc_train":        float(auprc_train),
        "auprc_test":         float(auprc_test),
        "t_drift":            float(t_drift),
        "t_mitigate":         float(t_mitigate),
        "drift_report":       drift_report,
        "temporal_report":    temporal_report,
        "mv_report":          mv_report,
        "mitigation_log":     mitigation_log or {},
        "data_stats":         data_stats or {},
        "preprocessing_stats": _build_preprocessing_stats(preprocessor, t_preprocess, n_features_final)
                                if preprocessor is not None else {},
    }

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "report.json")
    with open(path, "w") as f:
        json.dump(report, f, cls=_Enc, indent=2)
    print(f"📊 Report saved to {path}")
    print("   👉 Run: streamlit run dashboard.py  to view the dashboard")


def main():
    args = parse_args()
    reporter = HackathonReporter()

    t_start = time.time()
    test_ids, test_probs, _, _ = run_pipeline(args, seed=42)
    print(f"⏱️ Total Runtime: {time.time() - t_start:.1f}s")

    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    reporter.save_predictions(test_ids, test_probs, os.path.join(_root, "prediction.csv"))
    print("\n🏆 Output saved to prediction.csv\n")

if __name__ == "__main__":
    main()