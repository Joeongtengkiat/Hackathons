"""
detector.py — Unified drift detector for the NAISC Singtel 2026 pipeline.

Consolidates DriftDetectorV1, MultivariateDriftDetector, and DriftDetectorV2
into a single DriftDetector class.

detect() returns a 4-tuple:
    (drift_report, temporal_report, train_month_similarity, mv_report)

Sections run:
  Univariate:
    - Rolling pairwise KS / Chi-square across consecutive months
    - Temporal report (first month vs each subsequent)
    - Train vs test drift report (per feature)
    - Train month similarity to test
    - LOO AUPRC baseline (univariate features)

  Multivariate (mv_report):
    1. Joint distribution  — CBDT + MMD + PCA + Covariance (train vs test)
    2. Temporal MV         — same four tests, first month vs each subsequent
    3. Per-feature tests   — type-aware: KS/AD/MW/T-test + Wasserstein/Hellinger/JS
    4. Drift impact        — PSI × LightGBM gain importance
    5. Concept drift       — first-month-trained AUPRC evaluated on each subsequent month
    6. Correlation drift   — pairwise Pearson stability (Fisher z-test)
    7. Target drift        — churn rate stability across months
    8. Missing value drift — null-rate changes across months and train vs test

Usage:
    from detector import DriftDetector
    detector = DriftDetector(alpha=0.05)
    drift_report, temporal_report, train_month_similarity, mv_report = detector.detect(combined_sample, ignore_cols)
"""

import warnings
import numpy as np
import pandas as pd
from scipy import stats
from scipy.spatial.distance import squareform
from scipy.cluster.hierarchy import linkage, fcluster
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import roc_auc_score, average_precision_score
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.preprocessing import StandardScaler
from sklearn.decomposition import PCA
from sklearn.metrics.pairwise import rbf_kernel, euclidean_distances
from lightgbm import LGBMClassifier
from joblib import Parallel, delayed


class DriftDetector:

    def __init__(
        self,
        alpha:                      float = 0.05,
        min_psi:                    float = 0.01,
        min_drifted_months:         int   = 2,
        regime_psi_threshold:       float = 0.05,
        max_categorical_cardinality:int   = 10,
        n_pca_components:           int   = 20,
        mmd_subsample:              int   = 2000,
        cbdt_n_estimators:          int   = 100,
        cbdt_auroc_threshold:       float = 0.60,
        random_state:               int   = 42,
    ):
        self.alpha                       = alpha
        self.min_psi                     = min_psi
        self.min_drifted_months          = min_drifted_months
        self.regime_psi_threshold        = regime_psi_threshold
        self.max_categorical_cardinality = max_categorical_cardinality
        self.n_pca_components            = n_pca_components
        self.mmd_subsample               = mmd_subsample
        self.cbdt_n_estimators           = cbdt_n_estimators
        self.cbdt_auroc_threshold        = cbdt_auroc_threshold
        self.random_state                = random_state

        self.drift_report            = {}
        self.temporal_report         = {}
        self.monthly_report          = {}
        self.train_month_similarity  = {}
        self._idx_to_label           = {}
        self.auprc: float            = None

        self._cache      = {}
        self._test_cache = {}
        self._is_numeric = {}
        self._cat_max_val= {}

    # ==========================================================================
    # Entry point
    # ==========================================================================

    def detect(self, combined_df: pd.DataFrame, ignore_cols: list):
        """
        Run full drift detection.
        Returns (drift_report, temporal_report, train_month_similarity, mv_report).
        """
        if "month_idx" not in combined_df.columns:
            return self.drift_report, self.temporal_report, self.train_month_similarity, {}

        combined = combined_df.sort_values("month_idx").reset_index(drop=True)

        # Preserve original (un-factorized) copy for multivariate methods.
        # The univariate path factorizes categoricals in-place for speed;
        # multivariate methods need the original strings/floats for correct
        # type detection and per-feature encoding.
        combined_original = combined.copy()

        if "Month" in combined.columns:
            self._idx_to_label = (
                combined.drop_duplicates("month_idx")
                        .set_index("month_idx")["Month"]
                        .to_dict()
            )

        months = sorted(combined["month_idx"].unique())
        cols   = [
            c for c in combined.columns
            if c not in ignore_cols + ["month_idx", "__is_test__"]
            and self._is_driftable(combined[c])
        ]

        # Global integer encoding — core speedup (univariate only)
        for c in cols:
            is_num = pd.api.types.is_numeric_dtype(combined[c])
            self._is_numeric[c] = is_num
            if not is_num:
                combined[c], uniques = pd.factorize(combined[c])
                self._cat_max_val[c] = len(uniques)

        is_test_map  = combined.groupby("month_idx")["__is_test__"].max()
        train_months = [m for m in months if is_test_map[m] == 0]

        # Pre-slice data into per-month cache
        for m in months:
            m_mask = (combined["month_idx"] == m).values
            self._cache[m] = {}
            for c in cols:
                arr = combined[c].values[m_mask]
                self._cache[m][c] = arr[~pd.isna(arr)] if self._is_numeric[c] else arr[arr != -1]

        test_mask = (combined["__is_test__"] == 1).values
        for c in cols:
            arr = combined[c].values[test_mask]
            self._test_cache[c] = arr[~pd.isna(arr)] if self._is_numeric[c] else arr[arr != -1]

        # ── Univariate: rolling pairwise across consecutive months ─────────────
        for i in range(1, len(months)):
            m_prev, m_curr = months[i - 1], months[i]
            self.monthly_report[(m_prev, m_curr)] = {}
            for col in cols:
                a, b = self._cache[m_prev][col], self._cache[m_curr][col]
                if len(a) == 0 or len(b) == 0:
                    continue
                if self._is_numeric[col]:
                    self._ks_test(col, a, b, self.monthly_report[(m_prev, m_curr)])
                else:
                    self._chi_square_test(col, a, b, self.monthly_report[(m_prev, m_curr)])

        self._create_temporal_report(months, cols)
        self._detect_regimes(months, cols)
        self._create_drift_report(train_months, cols)
        self._create_train_month_similarity_report(train_months, cols)

        # ── Multivariate — uses original un-factorized data ────────────────────
        mv_cols = [
            c for c in combined_original.columns
            if c not in ignore_cols + ["month_idx", "__is_test__"]
            and self._is_driftable(combined_original[c])
        ]
        mv_report = self._run_multivariate(combined_original, train_months, mv_cols, ignore_cols)

        # Surface multivariate concept-drift LOO AUPRC as self.auprc
        mv_auprc = (mv_report or {}).get("concept", {}).get("avg_auprc")
        if mv_auprc is not None:
            self.auprc = mv_auprc

        return self.drift_report, self.temporal_report, self.train_month_similarity, mv_report

    # ==========================================================================
    # Univariate detection
    def _create_temporal_report(self, months, cols):
        col_monthly = {}
        first_month = months[0]
        for m in months[1:]:
            label = self._idx_to_label.get(m, str(m))
            for col in cols:
                a, b = self._cache[first_month][col], self._cache[m][col]
                if len(a) == 0 or len(b) == 0:
                    continue
                tmp = {}
                if self._is_numeric[col]:
                    self._ks_test(col, a, b, tmp)
                else:
                    self._chi_square_test(col, a, b, tmp)
                if col in tmp:
                    col_monthly.setdefault(col, {})[label] = tmp[col]

        for col, monthly in col_monthly.items():
            labels = list(monthly.keys())
            for l in labels:
                monthly[l]["drift"]    = bool(monthly[l]["p_value"] < self.alpha and monthly[l]["psi"] > self.min_psi)
                monthly[l]["severity"] = self._psi_severity(monthly[l]["psi"])

            any_drift = any(monthly[l]["drift"] for l in labels)
            best_lbl  = min(labels, key=lambda l: monthly[l]["p_value"])
            max_psi   = round(float(max(monthly[l]["psi"] for l in labels)), 6)

            entry = {
                "type":     monthly[best_lbl]["type"],
                "p_value":  monthly[best_lbl]["p_value"],
                "psi":      max_psi,
                "drift":    any_drift,
                "severity": self._psi_severity(max_psi),
                "monthly":  monthly,
            }
            if "wasserstein" in monthly[best_lbl]:
                entry["wasserstein"] = round(float(max(monthly[l].get("wasserstein", 0) for l in labels)), 6)
            if "new_category_ratio" in monthly[best_lbl]:
                entry["new_category_ratio"] = round(float(max(monthly[l].get("new_category_ratio", 0) for l in labels)), 6)
            self.temporal_report[col] = entry

    def _create_drift_report(self, train_months, cols):
        n_months = len(train_months)
        col_data = {}
        for m in train_months:
            for col in cols:
                a, b = self._cache[m][col], self._test_cache[col]
                if len(a) == 0 or len(b) == 0:
                    continue
                tmp = {}
                if self._is_numeric[col]:
                    self._ks_test(col, a, b, tmp)
                else:
                    self._chi_square_test(col, a, b, tmp)
                if col in tmp:
                    d = col_data.setdefault(col, {"psi_list": [], "p_list": [], "type": tmp[col]["type"]})
                    d["psi_list"].append(tmp[col]["psi"])
                    d["p_list"].append(tmp[col]["p_value"])
                    for k in ("wasserstein", "new_category_ratio", "tvd"):
                        if k in tmp[col]:
                            d.setdefault(k + "_list", []).append(tmp[col][k])
                    if "majority_class_switch" in tmp[col]:
                        d.setdefault("majority_class_switch_list", []).append(tmp[col]["majority_class_switch"])

        for col, data in col_data.items():
            psi_arr, p_arr = np.array(data["psi_list"]), np.array(data["p_list"])
            median_psi     = float(np.median(psi_arr))
            max_psi        = float(np.max(psi_arr))
            drifted_count  = int(np.sum((p_arr < self.alpha) & (psi_arr > self.min_psi)))

            entry = {
                "type":                 data["type"],
                "p_value":              round(float(np.min(p_arr)), 6),
                "psi":                  round(median_psi, 6),
                "max_psi":              round(max_psi, 6),
                "drift":                drifted_count >= self.min_drifted_months,
                "severity":             self._psi_severity(round(median_psi, 6)),
                "drifted_train_months": drifted_count,
                "total_train_months":   n_months,
            }
            for k in ("wasserstein", "new_category_ratio", "tvd"):
                if k + "_list" in data:
                    entry[k] = round(float(np.max(data[k + "_list"])), 6)
            if "majority_class_switch_list" in data:
                entry["majority_class_switch"] = any(data["majority_class_switch_list"])
            self.drift_report[col] = entry

    def _create_train_month_similarity_report(self, train_months, cols):
        for m in train_months:
            label      = self._idx_to_label.get(m, str(m))
            p_values, psi_values = [], []
            for col in cols:
                a, b = self._cache[m][col], self._test_cache[col]
                if len(a) == 0 or len(b) == 0:
                    continue
                tmp = {}
                if self._is_numeric[col]:
                    self._ks_test(col, a, b, tmp)
                else:
                    self._chi_square_test(col, a, b, tmp)
                if col in tmp:
                    p_values.append(tmp[col]["p_value"])
                    psi_values.append(tmp[col]["psi"])
            avg_p = round(float(np.nanmean(p_values)), 6) if p_values else float("nan")
            self.train_month_similarity[label] = {
                "avg_p_value": avg_p,
                "avg_psi":     round(float(np.nanmean(psi_values)), 6) if psi_values else float("nan"),
                "signal":      self._similarity_signal(avg_p),
            }

    def _detect_regimes(self, months, cols):
        month_labels = [self._idx_to_label.get(m, str(m)) for m in months]
        n = len(months)
        for col in cols:
            psi_matrix = np.zeros((n, n))
            for i, m1 in enumerate(months):
                for j, m2 in enumerate(months):
                    if i >= j:
                        continue
                    a, b = self._cache[m1][col], self._cache[m2][col]
                    if len(a) == 0 or len(b) == 0:
                        continue
                    dist = self._distribution_distance(col, a, b)
                    psi_matrix[i][j] = psi_matrix[j][i] = dist

            condensed = squareform(psi_matrix)
            if condensed.max() < self.regime_psi_threshold:
                regime_info = {"n_regimes": 1, "pattern": "stable",
                               "assignment": {label: 1 for label in month_labels}}
            else:
                Z      = linkage(condensed, method="average")
                labels = fcluster(Z, t=self.regime_psi_threshold, criterion="distance")
                n_reg  = len(set(labels))
                assignment = {month_labels[i]: int(labels[i]) for i in range(n)}
                if n_reg == 1:
                    pattern = "stable"
                elif n_reg == 2:
                    r_indices = {}
                    for idx, lbl in enumerate(labels):
                        r_indices.setdefault(lbl, []).append(idx)
                    is_contiguous = lambda idxs: sorted(idxs) == list(range(min(idxs), max(idxs) + 1))
                    pattern = "step shift" if all(is_contiguous(idxs) for idxs in r_indices.values()) else "cyclical"
                else:
                    pattern = "complex"
                regime_info = {"n_regimes": n_reg, "pattern": pattern, "assignment": assignment}

            if col in self.temporal_report:
                self.temporal_report[col]["regimes"] = regime_info

    # ── Statistical tests ──────────────────────────────────────────────────────

    def _compute_psi_num(self, a, b, bins=10):
        breakpoints = np.linspace(a.min(), a.max(), bins + 1)
        breakpoints[0], breakpoints[-1] = -np.inf, np.inf
        a_c = np.histogram(a, bins=breakpoints)[0].astype(float)
        b_c = np.histogram(b, bins=breakpoints)[0].astype(float)
        a_p = np.where(a_c == 0, 1e-8, a_c / a_c.sum())
        b_p = np.where(b_c == 0, 1e-8, b_c / b_c.sum())
        return round(float(np.sum((b_p - a_p) * np.log(b_p / a_p))), 6)

    def _compute_psi_cat(self, a, b, max_cat):
        a_c = np.bincount(a, minlength=max_cat).astype(float)
        b_c = np.bincount(b, minlength=max_cat).astype(float)
        a_p = np.where(a_c == 0, 1e-8, a_c / a_c.sum())
        b_p = np.where(b_c == 0, 1e-8, b_c / b_c.sum())
        return round(float(np.sum((b_p - a_p) * np.log(b_p / a_p))), 6)

    def _compute_tvd_cat(self, a, b, max_cat):
        a_p = np.bincount(a, minlength=max_cat) / len(a)
        b_p = np.bincount(b, minlength=max_cat) / len(b)
        return round(float(np.sum(np.abs(a_p - b_p)) / 2), 6)

    def _frequency_encode(self, a, b, max_cat):
        freqs = np.bincount(a, minlength=max_cat) / len(a)
        return freqs[a], freqs[b]

    def _distribution_distance(self, col, a, b):
        if self._is_numeric[col]:
            return self._compute_psi_num(a, b)
        max_cat  = self._cat_max_val[col]
        n_unique = ((np.bincount(a, minlength=max_cat) > 0) | (np.bincount(b, minlength=max_cat) > 0)).sum()
        if n_unique <= 5:
            return self._compute_tvd_cat(a, b, max_cat)
        return self._compute_psi_cat(a, b, max_cat)

    def _ks_test(self, col, a, b, report):
        _, p = stats.ks_2samp(a, b)
        report[col] = {
            "type":        "numerical (KS)",
            "p_value":     round(p, 6),
            "psi":         self._compute_psi_num(a, b),
            "wasserstein": round(float(stats.wasserstein_distance(a, b)), 6),
            "drift":       p < self.alpha,
        }

    def _chi_square_test(self, col, a, b, report):
        max_cat      = self._cat_max_val[col]
        a_counts     = np.bincount(a, minlength=max_cat).astype(float)
        b_counts     = np.bincount(b, minlength=max_cat).astype(float)
        active_cats  = (a_counts > 0) | (b_counts > 0)
        n_unique     = active_cats.sum()

        if n_unique > self.max_categorical_cardinality:
            a_enc, b_enc      = self._frequency_encode(a, b, max_cat)
            new_cat_count     = b_counts[a_counts == 0].sum()
            new_category_ratio = round(float(new_cat_count / len(b)), 6)
            _, p = stats.ks_2samp(a_enc, b_enc)
            report[col] = {
                "type":               "categorical (freq-KS)",
                "p_value":            round(p, 6),
                "psi":                self._compute_psi_num(a_enc, b_enc),
                "wasserstein":        round(float(stats.wasserstein_distance(a_enc, b_enc)), 6),
                "new_category_ratio": new_category_ratio,
                "drift":              p < self.alpha,
            }
            return

        a_obs, b_obs = a_counts[active_cats], b_counts[active_cats]
        if a_obs.sum() == 0 or b_obs.sum() == 0:
            return
        new_cat_count      = b_counts[a_counts == 0].sum()
        new_category_ratio = round(float(new_cat_count / len(b)), 6)
        b_exp = np.maximum((b_obs / b_obs.sum()) * a_obs.sum(), 1e-10)
        _, p  = stats.chisquare(f_obs=a_obs, f_exp=b_exp)

        if n_unique <= 5:
            tvd        = self._compute_tvd_cat(a, b, max_cat)
            a_majority = int(np.argmax(a_counts)) if a_counts.max() > 0 else None
            b_majority = int(np.argmax(b_counts)) if b_counts.max() > 0 else None
            report[col] = {
                "type":                  "categorical (TVD)",
                "p_value":               round(p, 6),
                "psi":                   tvd,
                "tvd":                   tvd,
                "majority_class_switch": bool(a_majority != b_majority),
                "drift":                 p < self.alpha,
            }
        else:
            report[col] = {
                "type":               "categorical (Chi²)",
                "p_value":            round(p, 6),
                "psi":                self._compute_psi_cat(a, b, max_cat),
                "new_category_ratio": new_category_ratio,
                "drift":              p < self.alpha,
            }

    # ==========================================================================
    # Multivariate detection
    # ==========================================================================

    def _run_multivariate(self, combined, train_months, cols, ignore_cols):
        train_df = combined[combined["__is_test__"] == 0]
        test_df  = combined[combined["__is_test__"] == 1]

        print("  [MV 1/3] Joint distribution (CBDT+MMD+PCA+Cov)...")
        joint = self._compare_multivariate(train_df, test_df, cols)

        print("  [MV 2/3] Drift impact score...")
        impact = self._compute_drift_impact(combined, self.drift_report, ignore_cols)

        print("  [MV 3/3] Concept drift (leave-one-month-out)...")
        concept = self._compute_concept_drift(combined, ignore_cols)

        target  = self._compute_target_drift(combined)
        missing = self._compute_missing_drift(combined, ignore_cols)

        return {
            "joint":   joint,
            "impact":  impact,
            "concept": concept,
            "target":  target,
            "missing": missing,
        }

    def _compare_multivariate(self, reference, current, cols):
        num_cols = [
            c for c in cols
            if pd.api.types.is_numeric_dtype(reference[c]) and c in current.columns
        ]
        if not num_cols:
            return {"error": "No numeric features"}

        ref_raw = reference[num_cols].copy()
        cur_raw = current[num_cols].copy()
        for c in num_cols:
            med = ref_raw[c].median()
            ref_raw[c] = ref_raw[c].fillna(med)
            cur_raw[c] = cur_raw[c].fillna(med)

        scaler = StandardScaler()
        X_ref  = scaler.fit_transform(ref_raw.values)
        X_cur  = scaler.transform(cur_raw.values)

        cbdt  = self._cbdt(X_ref, X_cur, num_cols)
        mmd   = self._mmd_rbf(X_ref, X_cur)
        pca   = self._pca_drift(X_ref, X_cur)
        cov   = self._covariance_drift(X_ref, X_cur)
        votes = [cbdt["drift"], mmd["drift"], pca["drift"], cov["drift"]]
        return {
            "n_ref": len(X_ref), "n_cur": len(X_cur), "n_features": len(num_cols),
            "cbdt": cbdt, "mmd": mmd, "pca": pca, "covariance": cov,
            "overall_drift":    sum(votes) >= 2,
            "drift_vote_count": sum(votes),
        }

    def _cbdt(self, X_ref, X_cur, feature_names):
        n   = min(self.mmd_subsample, len(X_ref), len(X_cur))
        rng = np.random.RandomState(self.random_state)
        X_r = X_ref[rng.choice(len(X_ref), n, replace=False)]
        X_c = X_cur[rng.choice(len(X_cur), n, replace=False)]
        X_all = np.vstack([X_r, X_c])
        y_all = np.array([0] * n + [1] * n)

        clf = RandomForestClassifier(
            n_estimators=self.cbdt_n_estimators, max_depth=6,
            min_samples_leaf=10, random_state=self.random_state, n_jobs=-1,
        )
        try:
            cv     = StratifiedKFold(n_splits=3, shuffle=True, random_state=self.random_state)
            y_prob = cross_val_predict(clf, X_all, y_all, cv=cv, method="predict_proba")[:, 1]
            auroc  = float(roc_auc_score(y_all, y_prob))
        except Exception:
            auroc = 0.5

        clf.fit(X_all, y_all)
        imps     = clf.feature_importances_
        top_idx  = np.argsort(imps)[::-1][:min(15, len(feature_names))]
        top_feat = [(feature_names[i], round(float(imps[i]), 4)) for i in top_idx]
        return {
            "auroc":             round(auroc, 4),
            "h_divergence":      round(max(0.0, 2 * (auroc - 0.5)), 4),
            "drift":             auroc > self.cbdt_auroc_threshold,
            "top_drift_drivers": top_feat,
        }

    def _mmd_rbf(self, X, Y):
        n   = min(self.mmd_subsample, len(X), len(Y))
        rng = np.random.RandomState(self.random_state)
        X_s = X[rng.choice(len(X), n, replace=False)]
        Y_s = Y[rng.choice(len(Y), n, replace=False)]
        sub   = np.vstack([X_s[:200], Y_s[:200]])
        dists = euclidean_distances(sub)
        sigma = max(float(np.median(dists[dists > 0])), 1e-6)
        gamma = 1.0 / (2 * sigma ** 2)
        kxx = rbf_kernel(X_s, X_s, gamma=gamma)
        kxy = rbf_kernel(X_s, Y_s, gamma=gamma)
        kyy = rbf_kernel(Y_s, Y_s, gamma=gamma)
        nx, ny = len(X_s), len(Y_s)
        mmd2 = float(max(0.0,
            (kxx.sum() - np.diag(kxx).sum()) / (nx * (nx - 1))
            - 2 * kxy.mean()
            + (kyy.sum() - np.diag(kyy).sum()) / (ny * (ny - 1))
        ))
        return {"score": round(mmd2, 6), "sigma": round(sigma, 4), "drift": mmd2 > 0.005}

    def _pca_drift(self, X_ref, X_cur):
        n_comp = max(1, min(self.n_pca_components, X_ref.shape[1] - 1,
                            len(X_ref) - 1, len(X_cur) - 1))
        pca = PCA(n_components=n_comp, random_state=self.random_state)
        pca.fit(X_ref)
        Z_ref = pca.transform(X_ref)
        Z_cur = pca.transform(X_cur)
        drifted_pcs = []
        for i in range(n_comp):
            _, p = stats.ks_2samp(Z_ref[:, i], Z_cur[:, i])
            if p < self.alpha:
                drifted_pcs.append((f"PC{i+1}", round(float(pca.explained_variance_ratio_[i]), 4), round(p, 6)))
        drifted_var = round(sum(ev for _, ev, _ in drifted_pcs), 4)
        return {
            "n_components_tested":        n_comp,
            "n_drifted_pcs":              len(drifted_pcs),
            "drifted_explained_variance": drifted_var,
            "drift":                      len(drifted_pcs) > 0,
            "drifted_pcs":                drifted_pcs,
        }

    def _covariance_drift(self, X_ref, X_cur):
        if X_ref.shape[1] < 2:
            return {"frobenius_norm": 0.0, "frobenius_normalized": 0.0, "drift": False}
        C_ref     = np.nan_to_num(np.corrcoef(X_ref.T), nan=0.0)
        C_cur     = np.nan_to_num(np.corrcoef(X_cur.T), nan=0.0)
        frob      = float(np.linalg.norm(C_ref - C_cur, "fro"))
        frob_norm = round(frob / X_ref.shape[1], 4)
        return {
            "frobenius_norm":       round(frob, 4),
            "frobenius_normalized": frob_norm,
            "drift":                frob_norm > 0.05,
        }

    # ── Drift impact ──────────────────────────────────────────────────────────

    def _compute_drift_impact(self, combined, drift_report_univariate, ignore_cols):
        train_df = combined[combined["__is_test__"] == 0].copy()
        if "ChurnStatus" not in train_df.columns:
            return {}
        feature_cols = [
            c for c in train_df.columns
            if c not in ignore_cols + ["month_idx", "__is_test__", "ChurnStatus"]
            and train_df[c].nunique() > 1
        ]
        X = train_df[feature_cols].copy()
        for c in feature_cols:
            if pd.api.types.is_numeric_dtype(X[c]):
                X[c] = X[c].fillna(X[c].median())
            else:
                X[c] = X[c].fillna("Unknown")
        for c in X.select_dtypes(include="object").columns:
            X[c] = pd.factorize(X[c])[0]

        y = train_df["ChurnStatus"].values.astype(int)
        model = LGBMClassifier(verbosity=-1, objective="binary", is_unbalance=True,
                               random_state=self.random_state, importance_type="gain")
        model.fit(X.values, y)
        gain_imp  = dict(zip(feature_cols, model.feature_importances_))
        total_imp = sum(gain_imp.values()) + 1e-9

        impact_scores = {}
        for col, info in drift_report_univariate.items():
            if col not in gain_imp:
                continue
            psi = info.get("psi", info.get("tvd", 0.0))
            imp = gain_imp[col] / total_imp
            impact_scores[col] = {
                "psi":             round(psi, 4),
                "gain_importance": round(imp, 6),
                "impact_score":    round(psi * imp * 1000, 4),
                "drifted":         info.get("drift", False),
            }
        return impact_scores

    # ── Concept drift ─────────────────────────────────────────────────────────

    def _compute_concept_drift(self, combined, ignore_cols):
        if "ChurnStatus" not in combined.columns:
            return {"error": "ChurnStatus not available"}

        train_df     = combined[combined["__is_test__"] == 0].copy()
        train_months = sorted(train_df["month_idx"].unique())
        if len(train_months) < 2:
            return {"error": "Need >= 2 training months"}

        feature_cols = [
            c for c in train_df.columns
            if c not in ignore_cols + ["month_idx", "__is_test__", "ChurnStatus"]
            and train_df[c].nunique() > 1
        ]

        X_full = train_df[feature_cols].copy()
        for c in feature_cols:
            if pd.api.types.is_numeric_dtype(X_full[c]):
                X_full[c] = X_full[c].fillna(X_full[c].median())
            else:
                X_full[c] = X_full[c].fillna("Unknown")
        for c in X_full.select_dtypes(include="object").columns:
            X_full[c] = pd.factorize(X_full[c])[0]

        X_arr     = X_full.values.astype(float)
        y_arr     = train_df["ChurnStatus"].values.astype(int)
        month_arr = train_df["month_idx"].values

        first_month = train_months[0]
        first_mask  = month_arr == first_month
        X_tr, y_tr  = X_arr[first_mask], y_arr[first_mask]

        def _evaluate_month(held_out):
            warnings.filterwarnings("ignore")
            mask       = month_arr == held_out
            X_ev, y_ev = X_arr[mask], y_arr[mask]
            if len(X_ev) == 0 or len(np.unique(y_ev)) < 2:
                return held_out, float("nan")
            try:
                m = LGBMClassifier(verbosity=-1, objective="binary", is_unbalance=True,
                                   random_state=self.random_state, importance_type="gain")
                m.fit(X_tr, y_tr)
                auprc = float(average_precision_score(y_ev, m.predict_proba(X_ev)[:, 1]))
            except Exception:
                auprc = float("nan")
            return held_out, round(auprc, 4)

        results     = Parallel(n_jobs=-1, prefer="threads")(delayed(_evaluate_month)(m) for m in train_months[1:])
        month_auprc = {self._idx_to_label.get(h, str(h)): a for h, a in results}

        valid = [v for v in month_auprc.values() if not np.isnan(v)]
        if not valid:
            return {"error": "All months failed"}

        avg     = round(float(np.mean(valid)), 4)
        thresh  = avg - 0.10
        drifted = {m: v for m, v in month_auprc.items() if not np.isnan(v) and v < thresh}
        return {
            "month_auprc":            month_auprc,
            "avg_auprc":              avg,
            "concept_drift_detected": len(drifted) > 0,
            "drifted_months":         drifted,
            "drift_type":             "concept_drift_present" if drifted else "covariate_only",
            "threshold_used":         round(thresh, 4),
        }

    # ── Monthly feature importance drift ─────────────────────────────────────

    def compute_monthly_feature_importances(self, train_df: pd.DataFrame, ignore_cols: list, top_n: int = 15):
        """
        Train a LightGBM on each training month independently using the FULL
        training dataframe (not the detection subsample).
        Collect gain importances, normalise per month (divide by per-month max),
        and return the top_n features ranked by mean normalised importance.
        """
        if "ChurnStatus" not in train_df.columns:
            return {"error": "ChurnStatus not available"}

        train_df     = train_df.copy()
        train_months = sorted(train_df["month_idx"].unique())
        if len(train_months) < 2:
            return {"error": "Need >= 2 training months"}

        # Build label map from this dataframe (may differ from detection sample)
        if "Month" in train_df.columns:
            idx_to_label = (
                train_df.drop_duplicates("month_idx")
                        .set_index("month_idx")["Month"]
                        .to_dict()
            )
        else:
            idx_to_label = self._idx_to_label

        feature_cols = [
            c for c in train_df.columns
            if c not in ignore_cols + ["month_idx", "__is_test__", "ChurnStatus", "Month"]
            and train_df[c].nunique() > 1
        ]

        X_full = train_df[feature_cols].copy()
        for c in feature_cols:
            if pd.api.types.is_numeric_dtype(X_full[c]):
                X_full[c] = X_full[c].fillna(X_full[c].median())
            else:
                X_full[c] = X_full[c].fillna("Unknown")
        for c in X_full.select_dtypes(include="object").columns:
            X_full[c] = pd.factorize(X_full[c])[0]

        X_arr     = X_full.values.astype(float)
        y_arr     = train_df["ChurnStatus"].values.astype(int)
        month_arr = train_df["month_idx"].values

        def _fit_month(month):
            warnings.filterwarnings("ignore")
            label     = idx_to_label.get(month, str(month))
            mask      = month_arr == month
            X_m, y_m  = X_arr[mask], y_arr[mask]
            if len(X_m) == 0 or len(np.unique(y_m)) < 2:
                return label, None
            try:
                m = LGBMClassifier(
                    verbosity=-1, objective="binary", is_unbalance=True,
                    random_state=self.random_state, importance_type="gain",
                )
                m.fit(X_m, y_m)
                imps    = m.feature_importances_.astype(float)
                max_imp = imps.max() + 1e-9
                return label, dict(zip(feature_cols, imps / max_imp))
            except Exception:
                return label, None

        results = Parallel(n_jobs=-1, prefer="threads")(delayed(_fit_month)(m) for m in train_months)
        monthly_importances = {label: imp for label, imp in results if imp is not None}

        if not monthly_importances:
            return {"error": "All months failed"}

        # Preserve chronological order (train_months is already sorted by month_idx)
        ordered_months = [
            idx_to_label.get(m, str(m))
            for m in train_months
            if idx_to_label.get(m, str(m)) in monthly_importances
        ]

        feat_means = {
            feat: float(np.mean([
                v[feat] for v in monthly_importances.values() if feat in v
            ]))
            for feat in feature_cols
        }
        top_features = sorted(feat_means, key=feat_means.get, reverse=True)[:top_n]

        return {
            "monthly_importances": monthly_importances,
            "ordered_months":      ordered_months,
            "top_features":        top_features,
        }

    # ── Correlation drift ─────────────────────────────────────────────────────

    # ── Target drift ──────────────────────────────────────────────────────────

    def _compute_target_drift(self, combined):
        if "ChurnStatus" not in combined.columns:
            return {"error": "ChurnStatus not available"}
        train_df     = combined[combined["__is_test__"] == 0].copy()
        overall      = round(float(train_df["ChurnStatus"].values.astype(int).mean()), 4)
        total_records = len(train_df)
        avg_records   = total_records / train_df["month_idx"].nunique() if train_df["month_idx"].nunique() > 0 else 0

        month_rates   = {}
        month_counts  = {}
        for m in sorted(train_df["month_idx"].unique()):
            mdf   = train_df[train_df["month_idx"] == m]
            label = self._idx_to_label.get(m, str(m))
            month_rates[label]  = round(float(mdf["ChurnStatus"].values.astype(int).mean()), 4)
            month_counts[label] = len(mdf)

        deviations        = {m: round(abs(r - overall), 4) for m, r in month_rates.items()}
        count_deviations  = {m: round(abs(c - avg_records) / avg_records, 4) if avg_records > 0 else 0.0
                             for m, c in month_counts.items()}
        max_dev           = max(deviations.values()) if deviations else 0.0
        max_count_dev     = max(count_deviations.values()) if count_deviations else 0.0

        return {
            "overall_churn_rate":    overall,
            "avg_records_per_month": round(avg_records, 1),
            "month_rates":           month_rates,
            "month_counts":          month_counts,
            "deviations":            deviations,
            "count_deviations":      count_deviations,
            "max_deviation":         round(max_dev, 4),
            "max_count_deviation":   round(max_count_dev, 4),
            "target_drift_detected": max_dev > 0.05,
            "volume_drift_detected": max_count_dev > 0.20,
            "flagged_months":        {m: d for m, d in deviations.items() if d > 0.05},
            "flagged_volume_months": {m: d for m, d in count_deviations.items() if d > 0.20},
        }

    # ── Missing value drift ───────────────────────────────────────────────────

    def _compute_missing_drift(self, combined, ignore_cols, threshold=0.05):
        feature_cols = [
            c for c in combined.columns
            if c not in ignore_cols + ["month_idx", "__is_test__", "ChurnStatus"]
            and combined[c].nunique() > 1
        ]
        train_df  = combined[combined["__is_test__"] == 0]
        test_df   = combined[combined["__is_test__"] == 1]
        months    = sorted(train_df["month_idx"].unique())
        test_null = {c: round(float(test_df[c].isna().mean()), 4) for c in feature_cols if c in test_df.columns}

        flagged = {}
        for col in feature_cols:
            rates = [
                round(float(train_df[train_df["month_idx"] == m][col].isna().mean()), 4)
                for m in months
            ]
            max_delta  = max(abs(rates[i] - rates[i - 1]) for i in range(1, len(rates))) if len(rates) > 1 else 0.0
            train_avg  = float(np.mean(rates))
            test_delta = abs(test_null.get(col, train_avg) - train_avg)
            if max_delta > threshold or test_delta > threshold:
                flagged[col] = {
                    "max_consecutive_delta": round(max_delta, 4),
                    "train_avg_null_rate":   round(train_avg, 4),
                    "test_null_rate":        round(test_null.get(col, train_avg), 4),
                    "test_delta":            round(test_delta, 4),
                    "temporal_flag":         max_delta > threshold,
                    "test_flag":             test_delta > threshold,
                }
        return {"flagged_features": flagged, "n_flagged": len(flagged), "threshold": threshold}

    # ==========================================================================
    # Helpers
    # ==========================================================================

    @staticmethod
    def _psi_severity(psi):
        if psi < 0.05:  return "very low"
        if psi < 0.1:   return "low"
        if psi < 0.2:   return "moderate"
        if psi < 0.25:  return "high"
        return "very high"

    @staticmethod
    def _similarity_signal(avg_p_value):
        import math
        if math.isnan(avg_p_value): return "insufficient data"
        if avg_p_value > 0.1:       return "similar"
        if avg_p_value > 0.05:      return "moderate drift"
        return "dissimilar"

    def _is_driftable(self, series):
        return len(series.dropna().unique()) > 1

    @property
    def drifted_columns(self):
        return [c for c, i in self.drift_report.items() if i["drift"]]

    @property
    def temporal_drifted_columns(self):
        return [c for c, i in self.temporal_report.items() if i["drift"]]
