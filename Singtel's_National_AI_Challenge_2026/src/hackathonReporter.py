"""
hackathonReporter.py — Single reporter class for NAISC Singtel 2026.
"""

import math
import pandas as pd
import numpy as np


class HackathonReporter:
    """
    Produces clean, judge-readable console output matching the NAISC Singtel 2026 rubric.
    """

    _W_COL   = 20
    _W_TYPE  = 13
    _W_DTYPE = 15
    _W_DESC  = 48
    _W_MIT   = 22

    def save_predictions(self, ids, probs, filepath="prediction.csv"):
        pd.DataFrame({
            "CustomerID":        ids,
            "probability_score": probs,
        }).to_csv(filepath, index=False)
        print(f"📄 Predictions saved to: {filepath}")

    def print_final_hackathon_summary(self, drift_report, time_taken, auprc_train, auprc_test,
                                       mv_report=None, mitigation_log=None):
        W, T, DT, D, M = self._W_COL, self._W_TYPE, self._W_DTYPE, self._W_DESC, self._W_MIT
        SEP = f"+{'-'*(W+2)}+{'-'*(T+2)}+{'-'*(DT+2)}+{'-'*(D+2)}+{'-'*(M+2)}+"

        cbdt_drivers = set()
        concept_drifted_flag = False
        if mv_report:
            top = mv_report.get("joint", {}).get("cbdt", {}).get("top_drift_drivers", [])
            cbdt_drivers = {feat for feat, _ in top}
            concept_drifted_flag = bool(mv_report.get("concept", {}).get("concept_drift_detected"))

        self._mitigation_log = mitigation_log or {}

        # ── 1. Drift table ────────────────────────────────────────────────────
        print("\n1. Data Drift Detection & Mitigation Summary")
        print(SEP)
        print(f"| {'Columns with Drift':<{W}} | {'Column Type':<{T}} | {'Drift Type':<{DT}} | "
              f"{'Drift Description':<{D}} | {'Drift Mitigation':<{M}} |")
        print(SEP)

        drifted = {c: v for c, v in drift_report.items() if v.get("drift")}

        if not drifted:
            print(f"| {'None':<{W}} | {'N/A':<{T}} | {'N/A':<{DT}} | "
                  f"{'No drift detected between train and test sets':<{D}} | {'None':<{M}} |")
        else:
            for col, info in drifted.items():
                col_name   = str(col)[:W].ljust(W)
                col_type   = self._get_col_type(info)[:T].ljust(T)
                drift_type = self._classify_drift_type(col, cbdt_drivers, concept_drifted_flag)
                drift_type = drift_type[:DT].ljust(DT)
                desc       = self._drift_description(info)[:D].ljust(D)
                mit        = self._mitigation_method(info, col=col)[:M].ljust(M)
                print(f"| {col_name} | {col_type} | {drift_type} | {desc} | {mit} |")

        print(SEP)

        # ── 1b. Monthly label & volume drift table ────────────────────────────
        if mv_report:
            self._print_monthly_drift_table(mv_report.get("target", {}))

        # ── 1c. Monthly feature importance drift ──────────────────────────────
        if mv_report:
            self._print_monthly_feature_importance_drift(mv_report)

        # ── 2. Runtime table ──────────────────────────────────────────────────
        print("\n2. Runtime (in seconds)")
        print("+----------------+")
        print("| Time Taken (s) |")
        print("+----------------+")
        print(f"| {time_taken:<14.1f} |")
        print("+----------------+")

        # ── 3. Performance table ──────────────────────────────────────────────
        print("\n3. Model Performance Metrics")
        print("+-----------+---------+")
        print("|           | AU-PRC  |")
        print("+-----------+---------+")
        print(f"| Train Set | {auprc_train:<7.3f} |")
        print("+-----------+---------+")
        print(f"| Test Set  | {auprc_test:<7.3f} |")
        print("+-----------+---------+\n")

    def _print_monthly_drift_table(self, target: dict):
        if not target or "error" in target:
            return

        month_rates   = target.get("month_rates",   {})
        month_counts  = target.get("month_counts",  {})
        deviations    = target.get("deviations",    {})
        count_devs    = target.get("count_deviations", {})
        flagged_label = target.get("flagged_months",        {})
        flagged_vol   = target.get("flagged_volume_months", {})
        overall       = target.get("overall_churn_rate",    float("nan"))
        avg_records   = target.get("avg_records_per_month", float("nan"))

        print(f"\n4. Monthly Label & Volume Drift")
        print(f"   Overall churn rate: {overall:.4f}  |  Avg records/month: {avg_records:.0f}")

        SEP = f"+{'-'*14}+{'-'*12}+{'-'*12}+{'-'*10}+{'-'*10}+{'-'*18}+"
        print(SEP)
        print(f"| {'Month':<12} | {'Churn Rate':<10} | {'Records':<10} | "
              f"{'Lbl Dev':<8} | {'Vol Dev':<8} | {'Flags':<16} |")
        print(SEP)

        for month in sorted(month_rates.keys()):
            rate     = month_rates.get(month, float("nan"))
            count    = month_counts.get(month, 0)
            lbl_dev  = deviations.get(month, 0.0)
            vol_dev  = count_devs.get(month, 0.0)
            flags    = []
            if month in flagged_label:
                flags.append("label ⚠")
            if month in flagged_vol:
                flags.append("volume ⚠")
            print(f"| {month:<12} | {rate:<10.4f} | {count:<10} | "
                  f"{lbl_dev:<8.4f} | {vol_dev:<8.4f} | {', '.join(flags):<16} |")

        print(SEP)

    def _print_monthly_feature_importance_drift(self, mv_report: dict):
        fi = (mv_report or {}).get("monthly_feature_importance", {})
        if not fi or "error" in fi:
            return

        monthly      = fi.get("monthly_importances", {})
        top_features = fi.get("top_features", [])
        if not monthly or not top_features:
            return

        months = fi.get("ordered_months") or sorted(monthly.keys())
        FEAT_W = 26
        MON_W  = 7

        print(f"\n5. Monthly Feature Importance Drift — Top {len(top_features)} Features")
        print("   (Normalised gain per month; 1.00 = highest-importance feature that month)\n")

        row = f"  {'Feature':<{FEAT_W}}"
        sep = f"  {'-' * FEAT_W}"
        for m in months:
            row += f" | {str(m)[:MON_W]:^{MON_W}}"
            sep += f"-+-{'-' * MON_W}"
        print(row)
        print(sep)

        for feat in top_features:
            row = f"  {feat[:FEAT_W]:<{FEAT_W}}"
            for m in months:
                val = monthly.get(m, {}).get(feat, float("nan"))
                cell = f"{'nan':^{MON_W}}" if val != val else f"{val:^{MON_W}.2f}"
                row += f" | {cell}"
            print(row)
        print()

    def _classify_drift_type(self, col: str, cbdt_drivers: set, concept_drifted: bool) -> str:
        if col in cbdt_drivers and concept_drifted:
            return "Concept+MV"
        if col in cbdt_drivers:
            return "Multivariate"
        if concept_drifted:
            return "Concept"
        return "Covariate"

    def _get_col_type(self, info: dict) -> str:
        if "wasserstein" in info:
            return "Numeric"
        if "tvd" in info or "new_category_ratio" in info:
            return "Categorical"
        return "Numeric" if "numeric" in str(info.get("type", "")).lower() else "Categorical"

    def _drift_description(self, info: dict) -> str:
        psi  = float(info.get("psi",  0.0) or 0.0)
        wass = float(info.get("wasserstein", 0.0) or 0.0)
        if "wasserstein" in info:
            skew_shift = info.get("skew_shift")
            range_exp  = info.get("range_explode")
            if range_exp:
                return f"Feature range explodes in test set (PSI={psi:.3f}, W={wass:.3f})"
            if skew_shift == "left":
                return f"Greater left-skewness in test vs train (PSI={psi:.3f}, W={wass:.3f})"
            if skew_shift == "right":
                return f"Greater right-skewness in test vs train (PSI={psi:.3f}, W={wass:.3f})"
            if psi >= 0.25:
                return f"Severe mean/variance shift in test set (PSI={psi:.3f}, W={wass:.3f})"
            if psi >= 0.10:
                return f"Moderate distribution shift in test set (PSI={psi:.3f}, W={wass:.3f})"
            return f"Mild distribution shift in test set (PSI={psi:.3f}, W={wass:.3f})"
        if "tvd" in info:
            if info.get("majority_class_switch"):
                return f"Dominant category changed in test set (TVD={info.get('tvd',0):.3f})"
            return f"Category proportions shifted in test set (TVD={info.get('tvd',0):.3f})"
        if "new_category_ratio" in info:
            r = info.get("new_category_ratio", 0.0)
            return f"New unseen categories in test set ({r:.1%} of values)"
        return f"Distribution shifted in test set ({info.get('severity', 'detected')})"

    def _mitigation_method(self, info: dict, col: str = "") -> str:
        log = getattr(self, "_mitigation_log", {})
        logged = log.get(col, "")

        is_numeric = "wasserstein" in info
        psi = float(info.get("psi", 0.0) or 0.0)
        cbdt_applied = "__cbdt_density_ratio__" in log
        temporal_applied = "__temporal_similarity__" in log

        if is_numeric:
            parts = []
            if psi >= 0.25:
                parts.append("Proximity Reweighting (strong)")
            elif psi >= 0.10:
                parts.append("Proximity Reweighting")
            else:
                parts.append("Proximity Reweighting (mild)")
            if cbdt_applied:
                parts.append("CBDT Density Ratio")
            if temporal_applied:
                parts.append("Temporal Reweighting")
            return " + ".join(parts[:2])  # keep it concise
        else:
            parts = ["Frequency Ratio Reweighting"]
            if cbdt_applied:
                parts.append("CBDT Density Ratio")
            return " + ".join(parts[:2])
