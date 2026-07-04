"""
mitigator.py — Drift mitigation: computes drift-corrected sample weights and
adds drift-robust features to train and test DataFrames.

Steps (all in one mitigate() call):
  1. Univariate proximity reweighting  — per-feature alignment on drifted cols
  2. Temporal similarity weighting     — upweight months resembling test
  3. CBDT density-ratio weights        — p(test|x)/p(train|x) covariate correction
  4. Drift-robust features             — z-score and ratio features for top drift drivers
  5. Concept drift weighting           — downweight months with poor LOO AUPRC
  6. Missing value indicators          — binary flags for features with drifting null rates
  7. Combine + normalise               — log-compress, clip, normalise to mean=1
"""

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler


class DriftMitigator:

    _SPLIT_COL = "__is_test__"

    def __init__(
        self,
        n_estimators: int = 150,
        max_depth:    int = 5,
        random_state: int = 42,
    ):
        self.n_estimators  = n_estimators
        self.max_depth     = max_depth
        self.random_state  = random_state
        self.mitigation_log: dict = {}

    def mitigate(
        self,
        train_df: pd.DataFrame,
        test_df:  pd.DataFrame,
        drift_report:          dict,
        temporal_report:       dict,
        train_month_similarity: dict = None,
        mv_report:             dict = None,
    ):
        """
        Returns (train_df, test_df, sample_weights).
        train_df and test_df may have new feature columns added in-place.
        sample_weights has length len(train_df).
        """
        # ── Step 1 & 2: Univariate + temporal weights ─────────────────────────
        weights = self._univariate_weights(train_df, test_df, drift_report, temporal_report, train_month_similarity)

        # ── Step 3: CBDT density-ratio weights ───────────────────────────────
        print("  [Mitigator] Computing CBDT density-ratio weights...")
        cbdt_dr = self._cbdt_density_weights(train_df, test_df)
        self.mitigation_log["__cbdt_density_ratio__"] = (
            f"CBDT density-ratio weights: range [{cbdt_dr.min():.3f}, {cbdt_dr.max():.3f}]"
        )

        # ── Step 4: Drift-robust features ────────────────────────────────────
        print("  [Mitigator] Creating drift-robust features...")
        train_df, test_df = self._add_drift_robust_features(train_df, test_df, mv_report)

        # ── Step 5: Concept drift weighting ──────────────────────────────────
        concept_mult = np.ones(len(train_df), dtype=float)
        if mv_report:
            concept     = mv_report.get("concept", {})
            month_auprc = concept.get("month_auprc", {})
            avg_auprc   = concept.get("avg_auprc")

            if month_auprc and avg_auprc and avg_auprc > 0 and "Month" in train_df.columns:
                concept_mult = (
                    train_df["Month"]
                    .map(month_auprc)
                    .div(avg_auprc)
                    .clip(0.1, 2.0)
                    .fillna(1.0)
                    .values
                )
                self.mitigation_log["__concept_continuous_weight__"] = (
                    f"Continuous concept weights by AUPRC/avg "
                    f"(avg={avg_auprc:.4f}), "
                    f"range [{concept_mult.min():.3f}, {concept_mult.max():.3f}]"
                )

        # ── Step 5b: Label & volume drift correction ─────────────────────────
        print("  [Mitigator] Correcting for label and volume drift...")
        target_w = self._target_drift_weights(train_df, mv_report)

        # ── Step 6: Missing value indicators ──────────────────────────────────
        train_df, test_df = self._add_missing_indicators(train_df, test_df, mv_report)

        # ── Step 7: Combine, clip, log-compress, normalise ───────────────────
        combined_w  = weights * cbdt_dr * concept_mult * target_w
        combined_w  = np.clip(combined_w, 0.05, 20.0)
        combined_w  = np.log1p(combined_w)
        combined_w /= combined_w.mean()

        return train_df, test_df, combined_w

    # ── Step 1: Univariate proximity reweighting ──────────────────────────────

    def _univariate_weights(self, train_df, test_df, drift_report, temporal_report, train_month_similarity):
        n_train = len(train_df)
        weights = np.ones(n_train)

        all_drifted = set(
            [c for c, i in drift_report.items()    if i["drift"]] +
            [c for c, i in temporal_report.items() if i["drift"]]
        ) - {"month_idx", self._SPLIT_COL}

        for col in all_drifted:
            if col not in train_df.columns or col not in test_df.columns:
                continue

            p_drift    = drift_report.get(col, {}).get("p_value", 1.0)
            p_temporal = temporal_report.get(col, {}).get("p_value", 1.0)
            p_value    = min(p_drift, p_temporal)

            if p_value >= 0.05:
                continue

            psi = (
                drift_report[col].get("psi", 0)
                if col in drift_report
                else temporal_report[col].get("psi", 0)
            )

            if pd.api.types.is_numeric_dtype(train_df[col]):
                col_weights = self._align_numerical(train_df, test_df, col, psi=psi)
            else:
                col_weights = self._align_categorical(train_df, test_df, col)

            strength = 1.0 if p_value < 0.01 else 0.4

            drifted_months = drift_report.get(col, {}).get("drifted_train_months")
            total_months   = drift_report.get(col, {}).get("total_train_months")
            if drifted_months is not None and total_months:
                strength *= drifted_months / total_months

            col_weights = 1 + (col_weights - 1) * strength
            weights *= col_weights

        # ── Step 2: Temporal similarity weighting ─────────────────────────────
        if train_month_similarity and "Month" in train_df.columns:
            def get_sim(v):
                if isinstance(v, dict):
                    psi = v.get("avg_psi", float("nan"))
                    return float(np.exp(-3.0 * psi)) if psi == psi else float("nan")
                return float(v)

            sim_map  = {m: get_sim(v) for m, v in train_month_similarity.items()}
            fallback = float(np.nanmean(list(sim_map.values())))
            raw_sim  = train_df["Month"].map(sim_map).fillna(fallback).values.astype(float)
            raw_sim  = np.where(np.isnan(raw_sim), fallback, raw_sim)
            raw_sim  = raw_sim / raw_sim.mean()
            weights *= raw_sim

            self.mitigation_log["__temporal_similarity__"] = (
                f"applied exp(-3*avg_psi) similarity weights from {list(sim_map.keys())}"
            )

        weights = np.where(np.isfinite(weights), weights, 1.0)
        weights = np.clip(weights, 0.05, 20.0)
        weights = np.log1p(weights)
        weights /= weights.mean()
        return weights

    def _align_numerical(self, train_df, test_df, col, psi=0):
        test_median = test_df[col].median()
        test_std    = test_df[col].std() + 1e-9
        train_vals  = train_df[col].fillna(test_median).values
        distances   = np.abs(train_vals - test_median) / test_std

        if psi >= 0.25:
            proximity_factor = -2.0
        elif psi >= 0.1:
            proximity_factor = -1.5
        else:
            proximity_factor = -1.0

        col_weights = np.exp(proximity_factor * distances)
        col_weights /= col_weights.mean()
        self.mitigation_log[col] = "proximity reweighting"
        return col_weights

    def _align_categorical(self, train_df, test_df, col):
        train_freq = train_df[col].value_counts(normalize=True)
        test_freq  = test_df[col].value_counts(normalize=True)
        all_cats   = set(train_freq.index) | set(test_freq.index)

        ratio_map = {
            cat: test_freq.get(cat, 1e-6) / train_freq.get(cat, 1e-6)
            for cat in all_cats
        }

        col_weights = train_df[col].map(ratio_map).fillna(1.0).values
        col_weights = np.clip(col_weights, 0.5, 2.0)
        col_weights /= col_weights.mean()
        self.mitigation_log[col] = "reweighted by test/train frequency ratio"
        return col_weights

    # ── Step 3: CBDT density-ratio estimation ────────────────────────────────

    def _cbdt_density_weights(self, train_df, test_df):
        """
        Train a domain classifier to distinguish train vs test rows.
        Returns importance weights w(x) = p(test|x) / p(train|x) for every
        train row — the theoretically optimal covariate-shift correction.
        Trains on a 50k subsample for speed, predicts on all train rows.
        """
        num_cols = [
            c for c in train_df.columns
            if pd.api.types.is_numeric_dtype(train_df[c])
            and train_df[c].nunique() > 1
        ]

        if not num_cols:
            return np.ones(len(train_df))

        MAX_CBDT = 50_000
        n_tr_sub = min(MAX_CBDT // 2, len(train_df))
        n_te_sub = min(MAX_CBDT // 2, len(test_df))
        rng      = np.random.default_rng(self.random_state)
        tr_idx   = rng.choice(len(train_df), n_tr_sub, replace=False)
        te_idx   = rng.choice(len(test_df),  n_te_sub, replace=False)

        train_medians = train_df[num_cols].median()
        tr_sub = train_df.iloc[tr_idx][num_cols].fillna(train_medians).values.astype(np.float32)
        te_sub = test_df.iloc[te_idx][num_cols].fillna(train_medians).values.astype(np.float32)

        X_sub    = np.vstack([tr_sub, te_sub])
        y_sub    = np.array([0] * n_tr_sub + [1] * n_te_sub)
        scaler   = StandardScaler()
        X_sub_sc = scaler.fit_transform(X_sub)

        clf = RandomForestClassifier(
            n_estimators=100,
            max_depth=5,
            max_features="sqrt",
            min_samples_leaf=10,
            n_jobs=-1,
            random_state=self.random_state,
        )
        clf.fit(X_sub_sc, y_sub)

        X_train_all = train_df[num_cols].fillna(train_medians).values.astype(np.float32)
        X_train_sc  = scaler.transform(X_train_all)
        BATCH_SIZE  = 500_000
        p_hat       = np.empty(len(X_train_sc), dtype=np.float32)
        for start in range(0, len(X_train_sc), BATCH_SIZE):
            end             = start + BATCH_SIZE
            p_hat[start:end] = clf.predict_proba(X_train_sc[start:end])[:, 1]

        p_hat = np.clip(p_hat, 0.02, 0.98)
        dr    = p_hat / (1.0 - p_hat)
        dr    = np.clip(dr, 0.1, 10.0)
        dr   /= dr.mean()
        return dr

    # ── Step 4: Drift-robust feature engineering ──────────────────────────────

    def _add_drift_robust_features(self, train_df, test_df, mv_report):
        if not mv_report:
            return train_df, test_df

        eps = 1e-6

        top_drivers_raw = (
            mv_report.get("joint", {})
                     .get("cbdt", {})
                     .get("top_drift_drivers", [])
        )
        driver_cols = [
            col for col, _ in top_drivers_raw[:8]
            if col in train_df.columns
            and pd.api.types.is_numeric_dtype(train_df[col])
        ]

        impact = mv_report.get("impact", {})
        anchor_cols = [
            col for col, v in sorted(impact.items(), key=lambda x: x[1].get("psi", 1.0))
            if v.get("psi", 1.0) < 0.10
            and col in train_df.columns
            and pd.api.types.is_numeric_dtype(train_df[col])
            and col not in driver_cols
        ][:3]

        if not driver_cols:
            return train_df, test_df

        # Z-score features: stats from train, applied to both
        if "month_idx" in train_df.columns:
            z_cols        = driver_cols[:5]
            overall_means = train_df[z_cols].mean()
            overall_stds  = train_df[z_cols].std().clip(lower=eps)

            # Compute per-month mean and std using numpy for speed at large scale
            month_idx_arr = train_df["month_idx"].values
            unique_months = np.unique(month_idx_arr)
            month_means   = {}
            month_stds    = {}
            for col in z_cols:
                col_vals = train_df[col].to_numpy(dtype=np.float32, na_value=np.nan)
                means    = {}
                stds     = {}
                for m in unique_months:
                    mask   = month_idx_arr == m
                    vals   = col_vals[mask]
                    finite = vals[np.isfinite(vals)]
                    means[m] = float(np.mean(finite)) if len(finite) > 0 else float(overall_means[col])
                    stds[m]  = max(float(np.std(finite)), eps) if len(finite) > 1 else float(overall_stds[col])
                month_means[col] = means
                month_stds[col]  = stds

            for df in [train_df, test_df]:
                for col in z_cols:
                    row_means = df["month_idx"].map(month_means[col]).fillna(overall_means[col])
                    row_stds  = df["month_idx"].map(month_stds[col]).fillna(overall_stds[col])
                    z = ((df[col] - row_means) / row_stds).fillna(0.0).clip(-5.0, 5.0)
                    df[f"fe_mv_z_{col[:25]}"] = z.astype(np.float32)

        # Ratio features: anchor median from train, applied to both
        n_feats = 0
        for driver in driver_cols[:4]:
            for anchor in anchor_cols[:2]:
                anchor_median = float(train_df[anchor].median())
                key = f"fe_mv_r_{driver[:18]}_{anchor[:12]}"
                for df in [train_df, test_df]:
                    denom = df[anchor].fillna(anchor_median).clip(lower=eps)
                    df[key] = (df[driver].fillna(0.0) / denom).clip(-50.0, 50.0).astype(np.float32)
                n_feats += 1

        z_count = min(len(driver_cols), 5)
        if z_count or n_feats:
            self.mitigation_log["__mv_drift_robust_features__"] = (
                f"Added {z_count + n_feats} drift-robust features "
                f"({z_count} z-scores, {n_feats} ratios) "
                f"for CBDT drivers: {driver_cols[:5]}"
            )

        return train_df, test_df

    # ── Step 5b: Label & volume drift correction ─────────────────────────────

    def _target_drift_weights(self, train_df, mv_report):
        """
        Per-month label and volume drift correction.

        Label correction: for each month whose churn rate deviates >5% from
        the overall prior, reweight positives and negatives independently so
        the month looks like the global class distribution.
          pos_weight = overall_churn / month_churn
          neg_weight = (1 - overall_churn) / (1 - month_churn)

        Volume correction: for each month whose record count deviates >20%
        from the monthly average, scale its samples by avg_records / count
        so high-volume months don't dominate training.
        """
        if (
            not mv_report
            or "Month" not in train_df.columns
            or "ChurnStatus" not in train_df.columns
        ):
            return np.ones(len(train_df))

        target = mv_report.get("target", {})
        if not target or "error" in target:
            return np.ones(len(train_df))

        overall_churn = target.get("overall_churn_rate")
        avg_records   = target.get("avg_records_per_month")
        month_rates   = target.get("month_rates", {})
        month_counts  = target.get("month_counts", {})
        flagged_label = target.get("flagged_months", {})
        flagged_vol   = target.get("flagged_volume_months", {})

        if overall_churn is None or avg_records is None or not month_rates:
            return np.ones(len(train_df))

        weights     = np.ones(len(train_df), dtype=float)
        month_col   = train_df["Month"].values
        is_positive = train_df["ChurnStatus"].values.astype(bool)

        # ── Label correction (flagged months only) ────────────────────────────
        if flagged_label and 0 < overall_churn < 1:
            for month in flagged_label:
                m_churn = month_rates.get(month)
                if m_churn is None or not (0 < m_churn < 1):
                    continue
                mask  = month_col == month
                pos_w = np.clip(overall_churn / m_churn,             0.1, 10.0)
                neg_w = np.clip((1 - overall_churn) / (1 - m_churn), 0.1, 10.0)
                weights[mask & is_positive]  *= pos_w
                weights[mask & ~is_positive] *= neg_w

            self.mitigation_log["__label_drift_correction__"] = (
                f"Per-month class-prior correction applied to "
                f"{len(flagged_label)} flagged months: {list(flagged_label.keys())}"
            )

        # ── Volume correction (flagged months only) ───────────────────────────
        if flagged_vol and avg_records > 0:
            for month in flagged_vol:
                count = month_counts.get(month)
                if not count:
                    continue
                mask  = month_col == month
                vol_w = np.clip(avg_records / count, 0.2, 5.0)
                weights[mask] *= vol_w

            self.mitigation_log["__volume_drift_correction__"] = (
                f"Volume reweighting applied to "
                f"{len(flagged_vol)} flagged months: {list(flagged_vol.keys())}"
            )

        weights = np.where(np.isfinite(weights), weights, 1.0)
        weights /= weights.mean()
        return weights

    # ── Step 6: Missing value indicators ─────────────────────────────────────

    def _add_missing_indicators(self, train_df, test_df, mv_report):
        if not mv_report:
            return train_df, test_df

        missing_info = mv_report.get("missing", {})
        flagged      = missing_info.get("flagged_features", {})

        if not flagged:
            return train_df, test_df

        added = []
        for col in flagged:
            if col not in train_df.columns:
                continue
            feat_name = f"fe_mv_isnull_{col[:30]}"
            train_df[feat_name] = train_df[col].isna().astype(np.int8)
            test_df[feat_name]  = test_df[col].isna().astype(np.int8)
            added.append(col)

        if added:
            self.mitigation_log["__missing_indicators__"] = (
                f"Added {len(added)} is_null indicators for "
                f"drifting-null features: {added}"
            )

        return train_df, test_df
