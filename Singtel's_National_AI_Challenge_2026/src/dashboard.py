"""
dashboard.py — NAISC Singtel 2026 Drift Intelligence Dashboard
Run: streamlit run dashboard.py
Tabs: Data | Drift | Mitigation | Preprocessing | Results
"""

import json
import math
import os

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

REPORT_PATH = "report.json"

st.set_page_config(
    page_title="Drift Intelligence Dashboard",
    page_icon="📊",
    layout="wide",
)

def load_report():
    if not os.path.exists(REPORT_PATH):
        return None
    with open(REPORT_PATH) as f:
        return json.load(f)

data = load_report()

if data is None:
    st.error("No report.json found. Run the pipeline first: `python ./src/main.py ...`")
    st.stop()

drift_report        = data.get("drift_report", {})
temporal_report     = data.get("temporal_report", {})
mv_report           = data.get("mv_report", {})
mitigation_log      = data.get("mitigation_log", {})
auprc_train         = data.get("auprc_train", 0)
auprc_test          = data.get("auprc_test", 0)
t_drift             = data.get("t_drift", 0)
t_mitigate          = data.get("t_mitigate", 0)
data_stats          = data.get("data_stats", {})
preprocessing_stats = data.get("preprocessing_stats", {})

# ── Header ────────────────────────────────────────────────────────────────────

st.title("Drift Intelligence Dashboard")
st.caption("NAISC Singtel 2026 — Adaptive Drift Intelligence Challenge")

n_drifted = sum(1 for v in drift_report.values() if v.get("drift"))
n_total   = len(drift_report)

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Train AU-PRC",        f"{auprc_train:.4f}")
c2.metric("Test AU-PRC",         f"{auprc_test:.4f}", f"{auprc_test - auprc_train:+.4f}")
c3.metric("Drifted Features",    f"{n_drifted} / {n_total}")
c4.metric("Drift Detection (s)", f"{t_drift:.1f}s")
c5.metric("Mitigation (s)",      f"{t_mitigate:.1f}s")

st.divider()

# ── Tabs ──────────────────────────────────────────────────────────────────────

tab_data, tab_drift, tab_mit, tab_pre, tab_res = st.tabs([
    "Data", "Drift", "Mitigation", "Preprocessing", "Results"
])

# ══════════════════════════════════════════════════════════════════════════════
# Tab: Data
# ══════════════════════════════════════════════════════════════════════════════

with tab_data:
    st.subheader("Dataset Overview")

    n_train   = data_stats.get("n_train", 0)
    n_test    = data_stats.get("n_test", 0)
    n_feats   = data_stats.get("n_features", 0)
    churn_r   = data_stats.get("churn_rate", 0)
    n_num     = data_stats.get("n_numeric", 0)
    n_cat     = data_stats.get("n_categorical", 0)
    n_miss_tr = data_stats.get("n_missing_train", 0)
    n_miss_te = data_stats.get("n_missing_test", 0)

    mc1, mc2, mc3, mc4, mc5 = st.columns(5)
    mc1.metric("Train Rows",        f"{n_train:,}")
    mc2.metric("Test Rows",         f"{n_test:,}")
    mc3.metric("Features",          f"{n_feats}")
    mc4.metric("Overall Churn Rate", f"{churn_r:.1%}")
    mc5.metric("Missing (train)",   f"{n_miss_tr:,}")

    st.divider()
    col_l, col_r = st.columns(2)

    # Churn rate by month
    month_churn = data_stats.get("month_churn_rate", {})
    month_count = data_stats.get("month_count", {})
    months_ord  = data_stats.get("months", sorted(month_churn.keys()))

    with col_l:
        if month_churn:
            df_mc = pd.DataFrame({
                "Month":      months_ord,
                "Churn Rate": [month_churn.get(m, 0) for m in months_ord],
                "Records":    [month_count.get(m, 0) for m in months_ord],
            })
            fig = go.Figure()
            fig.add_trace(go.Bar(
                x=df_mc["Month"], y=df_mc["Churn Rate"],
                marker_color="#e63946", name="Churn Rate",
                text=[f"{v:.1%}" for v in df_mc["Churn Rate"]],
                textposition="outside",
            ))
            fig.add_hline(y=churn_r, line_dash="dash", line_color="gray",
                          annotation_text=f"Overall: {churn_r:.1%}")
            fig.update_layout(title="Monthly Churn Rate", xaxis_title="Month",
                              yaxis_title="Churn Rate", yaxis_tickformat=".1%",
                              height=350, margin=dict(t=50))
            st.plotly_chart(fig, width="stretch")

    with col_r:
        # Feature type breakdown
        fig_type = go.Figure(data=[go.Pie(
            labels=["Numeric", "Categorical"],
            values=[n_num, n_cat],
            hole=0.45,
            marker_colors=["#457b9d", "#e9c46a"],
        )])
        fig_type.update_layout(title="Feature Types", height=350, margin=dict(t=50))
        st.plotly_chart(fig_type, width="stretch")

    st.divider()
    col_l2, col_r2 = st.columns(2)

    # Missing values chart
    missing = data_stats.get("missing_per_col", {})
    with col_l2:
        if missing:
            df_miss = pd.DataFrame(
                list(missing.items()), columns=["Column", "Missing Count"]
            ).sort_values("Missing Count", ascending=False)
            df_miss["% of Train"] = (df_miss["Missing Count"] / n_train * 100).round(1)
            fig_miss = px.bar(
                df_miss, x="Missing Count", y="Column", orientation="h",
                text="% of Train",
                color="Missing Count", color_continuous_scale="Reds",
                title="Missing Values per Column (Train)",
                height=max(300, len(df_miss) * 36),
            )
            fig_miss.update_traces(texttemplate="%{text}%", textposition="outside")
            fig_miss.update_layout(yaxis={"autorange": "reversed"}, coloraxis_showscale=False)
            st.plotly_chart(fig_miss, width="stretch")
        else:
            st.success("No missing values in training data.")

    # Key numeric column stats
    col_stats = data_stats.get("col_stats", {})
    with col_r2:
        if col_stats:
            st.markdown("**Key Numeric Feature Statistics**")
            stat_rows = []
            for col, s in col_stats.items():
                stat_rows.append({
                    "Feature": col,
                    "Mean":    s["mean"],
                    "Std":     s["std"],
                    "Min":     s["min"],
                    "Max":     s["max"],
                    "Missing": s["missing"],
                })
            st.dataframe(pd.DataFrame(stat_rows), width="stretch", height=400)

    # Cardinality table
    cardinality = data_stats.get("cardinality", {})
    if cardinality:
        st.divider()
        st.subheader("Categorical Feature Cardinality")
        df_card = pd.DataFrame(
            list(cardinality.items()), columns=["Feature", "Unique Values"]
        ).sort_values("Unique Values", ascending=False)
        fig_card = px.bar(
            df_card, x="Unique Values", y="Feature", orientation="h",
            color="Unique Values", color_continuous_scale="Blues",
            title="Number of Unique Categories per Feature",
            height=max(300, len(df_card) * 36),
        )
        fig_card.update_layout(yaxis={"autorange": "reversed"}, coloraxis_showscale=False)
        st.plotly_chart(fig_card, width="stretch")

    # ── Train vs Test Comparison ───────────────────────────────────────────────
    st.divider()
    st.subheader("Train vs Test Comparison")

    cmp_l, cmp_r = st.columns(2)

    # Row count comparison
    with cmp_l:
        fig_size = go.Figure(go.Bar(
            x=["Train", "Test"],
            y=[n_train, n_test],
            marker_color=["#457b9d", "#e9c46a"],
            text=[f"{n_train:,}", f"{n_test:,}"],
            textposition="outside",
        ))
        fig_size.update_layout(
            title="Row Count: Train vs Test",
            yaxis_title="Rows",
            height=320, margin=dict(t=50),
        )
        st.plotly_chart(fig_size, width="stretch")

    # Missing value rate comparison (train vs test)
    with cmp_r:
        missing_train = data_stats.get("missing_per_col", {})
        missing_test  = data_stats.get("missing_test_per_col", {})
        all_miss_cols = sorted(set(missing_train) | set(missing_test))
        if all_miss_cols:
            df_miss_cmp = pd.DataFrame({
                "Feature":       all_miss_cols,
                "Train Missing %": [round(missing_train.get(c, 0) / n_train * 100, 2) if n_train else 0 for c in all_miss_cols],
                "Test Missing %":  [round(missing_test.get(c, 0)  / n_test  * 100, 2) if n_test  else 0 for c in all_miss_cols],
            }).sort_values("Train Missing %", ascending=False)
            fig_miss_cmp = go.Figure()
            fig_miss_cmp.add_trace(go.Bar(
                y=df_miss_cmp["Feature"], x=df_miss_cmp["Train Missing %"],
                name="Train", orientation="h", marker_color="#457b9d",
            ))
            fig_miss_cmp.add_trace(go.Bar(
                y=df_miss_cmp["Feature"], x=df_miss_cmp["Test Missing %"],
                name="Test", orientation="h", marker_color="#e9c46a",
            ))
            fig_miss_cmp.update_layout(
                title="Missing Value Rate: Train vs Test",
                xaxis_title="% Missing", barmode="group",
                height=max(320, len(all_miss_cols) * 40),
                yaxis={"autorange": "reversed"},
                margin=dict(t=50),
            )
            st.plotly_chart(fig_miss_cmp, width="stretch")
        else:
            st.success("No missing values in either train or test.")

    # Numeric stats delta table
    col_stats = data_stats.get("col_stats", {})
    if col_stats:
        st.markdown("**Numeric Feature Stats — Train (pipeline only has train stats)**")
        cmp_tbl_l, cmp_tbl_r = st.columns(2)
        with cmp_tbl_l:
            stat_rows = [
                {
                    "Feature": col,
                    "Mean":    s["mean"],
                    "Std":     s["std"],
                    "Min":     s["min"],
                    "Max":     s["max"],
                    "Missing": s["missing"],
                }
                for col, s in col_stats.items()
            ]
            df_stats = pd.DataFrame(stat_rows)
            st.markdown("**Train**")
            st.dataframe(df_stats, width="stretch", hide_index=True, height=350)

        with cmp_tbl_r:
            st.markdown("**Train vs Test — Missing Count Delta**")
            delta_rows = []
            for col in col_stats:
                tr_miss = missing_train.get(col, 0)
                te_miss = missing_test.get(col, 0)
                delta   = te_miss - tr_miss
                if tr_miss > 0 or te_miss > 0:
                    delta_rows.append({
                        "Feature":        col,
                        "Train Missing":  tr_miss,
                        "Test Missing":   te_miss,
                        "Delta":          delta,
                    })
            if delta_rows:
                df_delta = pd.DataFrame(delta_rows).sort_values("Delta", ascending=False)
                st.dataframe(df_delta, width="stretch", hide_index=True, height=350)
            else:
                st.success("No missing value discrepancies between train and test.")

# ══════════════════════════════════════════════════════════════════════════════
# Tab: Drift
# ══════════════════════════════════════════════════════════════════════════════

with tab_drift:
    st.subheader("Drift Analysis")

    sub_feat, sub_mv, sub_concept, sub_label, sub_fi = st.tabs([
        "Feature Drift", "Multivariate", "Concept Drift", "Label & Volume", "Feature Importance"
    ])

    # ── Feature Drift ──────────────────────────────────────────────────────────
    with sub_feat:
        rows = []
        for col, info in drift_report.items():
            rows.append({
                "Feature":  col,
                "PSI":      round(float(info.get("psi", 0) or 0), 4),
                "p-value":  round(float(info.get("p_value", 1) or 1), 4),
                "Type":     info.get("type", ""),
                "Drifted":  bool(info.get("drift", False)),
                "Severity": info.get("severity", ""),
            })
        df_feat = pd.DataFrame(rows).sort_values("PSI", ascending=False)

        col_l, col_r = st.columns([2, 1])
        with col_l:
            top_n = st.slider("Show top N features by PSI", 10, min(100, len(df_feat)), 30,
                              key="feat_topn")
            fig = px.bar(
                df_feat.head(top_n), x="PSI", y="Feature", orientation="h",
                color="Drifted",
                color_discrete_map={True: "#e63946", False: "#457b9d"},
                title="Population Stability Index per Feature",
                height=max(400, top_n * 22),
            )
            fig.update_layout(yaxis={"autorange": "reversed"})
            st.plotly_chart(fig, width="stretch")

        with col_r:
            st.markdown("**Drift Summary**")
            st.dataframe(df_feat[["Feature", "PSI", "p-value", "Severity", "Drifted"]],
                         width="stretch", height=500)

        sev_counts = df_feat["Severity"].value_counts().reset_index()
        sev_counts.columns = ["Severity", "Count"]
        fig2 = px.pie(sev_counts, names="Severity", values="Count",
                      color_discrete_sequence=px.colors.sequential.RdBu,
                      title="Features by Drift Severity")
        st.plotly_chart(fig2, width="stretch")

    # ── Multivariate ───────────────────────────────────────────────────────────
    with sub_mv:
        joint = mv_report.get("joint", {})
        cbdt  = joint.get("cbdt", {})

        mc1, mc2, mc3, mc4 = st.columns(4)
        mc1.metric("CBDT AUROC",       f"{cbdt.get('auroc', 0):.4f}",
                   help="Domain classifier AUROC — 0.5 = no drift, 1.0 = full drift")
        mc2.metric("H-Divergence",     f"{cbdt.get('h_divergence', 0):.4f}")
        mc3.metric("Overall Drift",    "Yes" if joint.get("overall_drift") else "No")
        mc4.metric("Drift Vote Count", f"{joint.get('drift_vote_count', 0)} / 4")

        drivers = cbdt.get("top_drift_drivers", [])
        if drivers:
            st.subheader("Top CBDT Drift Drivers")
            df_drivers = pd.DataFrame(drivers, columns=["Feature", "Importance"])
            fig = px.bar(
                df_drivers, x="Importance", y="Feature", orientation="h",
                color="Importance", color_continuous_scale="Reds",
                title="Feature Importance in Train vs Test Classifier",
                height=max(300, len(df_drivers) * 28),
            )
            fig.update_layout(yaxis={"autorange": "reversed"})
            st.plotly_chart(fig, width="stretch")

        impact = mv_report.get("impact", {})
        if impact:
            st.subheader("Drift Impact Score (PSI × Feature Importance)")
            impact_rows = [
                {"Feature": k, "Impact": round(float(v.get("impact_score", 0) or 0), 4),
                 "PSI": round(float(v.get("psi", 0) or 0), 4)}
                for k, v in impact.items()
            ]
            df_impact = pd.DataFrame(impact_rows).sort_values("Impact", ascending=False).head(20)
            fig = px.bar(
                df_impact, x="Impact", y="Feature", orientation="h",
                color="PSI", color_continuous_scale="Oranges",
                title="Top 20 Features by Drift Impact",
                height=500,
            )
            fig.update_layout(yaxis={"autorange": "reversed"})
            st.plotly_chart(fig, width="stretch")

    # ── Concept Drift ──────────────────────────────────────────────────────────
    with sub_concept:
        concept     = mv_report.get("concept", {})
        month_auprc = concept.get("month_auprc", {})
        avg_auprc   = concept.get("avg_auprc")

        if month_auprc:
            df_concept = pd.DataFrame(
                list(month_auprc.items()), columns=["Month", "AU-PRC"]
            ).dropna()
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=df_concept["Month"], y=df_concept["AU-PRC"],
                mode="lines+markers", name="Monthly AU-PRC",
                line=dict(color="#e63946", width=2), marker=dict(size=8),
            ))
            if avg_auprc:
                fig.add_hline(y=avg_auprc, line_dash="dash", line_color="gray",
                              annotation_text=f"Avg: {avg_auprc:.4f}")
                fig.add_hline(y=avg_auprc - 0.10, line_dash="dot", line_color="red",
                              annotation_text="Drift threshold (avg − 0.10)")
            fig.update_layout(
                title="Concept Drift: Model trained on month 1, evaluated on each subsequent month",
                xaxis_title="Month", yaxis_title="AU-PRC", height=400,
            )
            st.plotly_chart(fig, width="stretch")

            drifted_months = concept.get("drifted_months", {})
            if drifted_months:
                st.warning(f"Concept drift detected in {len(drifted_months)} month(s): "
                           f"{list(drifted_months.keys())}")
            else:
                st.success("No concept drift detected across months.")
        else:
            st.info("Concept drift data not available.")

    # ── Label & Volume Drift ───────────────────────────────────────────────────
    with sub_label:
        target        = mv_report.get("target", {})
        month_rates   = target.get("month_rates", {})
        month_counts  = target.get("month_counts", {})
        overall_churn = target.get("overall_churn_rate")
        flagged_label = target.get("flagged_months", {})
        flagged_vol   = target.get("flagged_volume_months", {})

        if month_rates:
            months_t = sorted(month_rates.keys())
            cl, cr = st.columns(2)

            with cl:
                df_rates = pd.DataFrame({
                    "Month": months_t,
                    "Churn Rate": [month_rates[m] for m in months_t],
                })
                fig = go.Figure()
                fig.add_trace(go.Bar(
                    x=df_rates["Month"], y=df_rates["Churn Rate"],
                    marker_color=["#e63946" if m in flagged_label else "#457b9d" for m in months_t],
                    name="Churn Rate",
                ))
                if overall_churn:
                    fig.add_hline(y=overall_churn, line_dash="dash", line_color="gray",
                                  annotation_text=f"Overall: {overall_churn:.4f}")
                fig.update_layout(title="Monthly Churn Rate (Label Drift)",
                                  xaxis_title="Month", yaxis_title="Churn Rate", height=350)
                st.plotly_chart(fig, width="stretch")

            with cr:
                avg_rec = target.get("avg_records_per_month", 0)
                df_counts = pd.DataFrame({
                    "Month": months_t,
                    "Records": [month_counts.get(m, 0) for m in months_t],
                })
                fig = go.Figure()
                fig.add_trace(go.Bar(
                    x=df_counts["Month"], y=df_counts["Records"],
                    marker_color=["#e63946" if m in flagged_vol else "#2a9d8f" for m in months_t],
                    name="Records",
                ))
                if avg_rec:
                    fig.add_hline(y=avg_rec, line_dash="dash", line_color="gray",
                                  annotation_text=f"Avg: {avg_rec:.0f}")
                fig.update_layout(title="Monthly Record Volume (Volume Drift)",
                                  xaxis_title="Month", yaxis_title="Record Count", height=350)
                st.plotly_chart(fig, width="stretch")

            if flagged_label:
                st.error(f"Label drift flagged in: {list(flagged_label.keys())}")
            if flagged_vol:
                st.warning(f"Volume drift flagged in: {list(flagged_vol.keys())}")
        else:
            st.info("Target drift data not available.")

    # ── Feature Importance Heatmap ─────────────────────────────────────────────
    with sub_fi:
        fi           = mv_report.get("monthly_feature_importance", {})
        monthly_imps = fi.get("monthly_importances", {})
        top_features = fi.get("top_features", [])
        months_ord_fi = fi.get("ordered_months") or sorted(monthly_imps.keys())

        if monthly_imps and top_features:
            n_feat = st.slider("Number of features to show", 5, min(30, len(top_features)), 15,
                               key="fi_n")
            feats  = top_features[:n_feat]
            matrix = [[monthly_imps.get(m, {}).get(f, float("nan")) for m in months_ord_fi]
                      for f in feats]
            fig = go.Figure(data=go.Heatmap(
                z=matrix, x=months_ord_fi, y=feats,
                colorscale="RdYlGn",
                text=[[f"{v:.2f}" if not math.isnan(v) else "" for v in row] for row in matrix],
                texttemplate="%{text}",
                colorbar=dict(title="Norm. Gain"),
            ))
            fig.update_layout(
                title="Normalised Feature Importance per Month (1.0 = top feature that month)",
                xaxis_title="Month", yaxis_title="Feature",
                height=max(400, n_feat * 30),
                yaxis={"autorange": "reversed"},
            )
            st.plotly_chart(fig, width="stretch")
            st.caption("High variance across months signals unstable importance — a marker of concept drift.")
        else:
            st.info("Monthly feature importance data not available.")

# ══════════════════════════════════════════════════════════════════════════════
# Tab: Mitigation
# ══════════════════════════════════════════════════════════════════════════════

with tab_mit:
    st.subheader("Drift Mitigation")

    cbdt_drivers     = {feat for feat, _ in mv_report.get("joint", {}).get("cbdt", {}).get("top_drift_drivers", [])}
    concept_drifted  = bool(mv_report.get("concept", {}).get("concept_drift_detected"))
    cbdt_applied     = "__cbdt_density_ratio__" in mitigation_log
    temporal_applied = "__temporal_similarity__" in mitigation_log

    def _drift_type(col):
        if col in cbdt_drivers and concept_drifted: return "Concept + Multivariate"
        if col in cbdt_drivers: return "Multivariate"
        if concept_drifted:     return "Concept"
        return "Covariate"

    def _col_type(info):
        if "wasserstein" in info: return "Numeric"
        if "tvd" in info or "new_category_ratio" in info: return "Categorical"
        return "Numeric" if "numeric" in str(info.get("type", "")).lower() else "Categorical"

    def _description(info):
        psi  = float(info.get("psi",  0.0) or 0.0)
        wass = float(info.get("wasserstein", 0.0) or 0.0)
        if "wasserstein" in info:
            if psi >= 0.25: return f"Severe mean/variance shift (PSI={psi:.3f}, W={wass:.3f})"
            if psi >= 0.10: return f"Moderate distribution shift (PSI={psi:.3f}, W={wass:.3f})"
            return f"Mild distribution shift (PSI={psi:.3f}, W={wass:.3f})"
        if "tvd" in info:
            if info.get("majority_class_switch"): return f"Dominant category changed (TVD={info.get('tvd',0):.3f})"
            return f"Category proportions shifted (TVD={info.get('tvd',0):.3f})"
        if "new_category_ratio" in info:
            return f"New unseen categories ({info.get('new_category_ratio',0):.1%} of values)"
        return f"Distribution shifted ({info.get('severity', 'detected')})"

    def _mitigation(info, col):
        psi   = float(info.get("psi", 0.0) or 0.0)
        parts = []
        if "wasserstein" in info:
            parts.append("Proximity Reweighting (strong)" if psi >= 0.25 else "Proximity Reweighting")
        else:
            parts.append("Frequency Ratio Reweighting")
        if cbdt_applied:     parts.append("CBDT Density Ratio")
        if temporal_applied: parts.append("Temporal Reweighting")
        return " + ".join(parts[:2])

    drifted = {c: v for c, v in drift_report.items() if v.get("drift")}

    # Summary metrics
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Drifted Features",    len(drifted))
    m2.metric("CBDT Applied",        "Yes" if cbdt_applied else "No")
    m3.metric("Temporal Reweighting","Yes" if temporal_applied else "No")
    m4.metric("Concept Drift",       "Yes" if concept_drifted else "No")

    st.divider()

    if drifted:
        rows = []
        for col, info in drifted.items():
            rows.append({
                "Feature":           col,
                "Column Type":       _col_type(info),
                "Drift Type":        _drift_type(col),
                "Drift Description": _description(info),
                "Mitigation":        _mitigation(info, col),
                "PSI":               round(float(info.get("psi", 0) or 0), 4),
                "Severity":          info.get("severity", ""),
            })
        df_mit = pd.DataFrame(rows).sort_values("PSI", ascending=False)

        sev_icon = {"very low": "🟢", "low": "🟡", "moderate": "🟠",
                    "high": "🔴", "severe": "🔴", "very high": "🔴"}
        df_mit[""] = df_mit["Severity"].map(lambda s: sev_icon.get(s, "⚪"))

        st.markdown("**Drifted Features — Detection & Mitigation**")
        st.dataframe(
            df_mit[["", "Feature", "Column Type", "Drift Type", "Drift Description", "Mitigation", "PSI", "Severity"]],
            width="stretch",
            height=min(600, 50 + len(df_mit) * 35),
        )

        # PSI bar chart coloured by mitigation type
        col_l, col_r = st.columns(2)
        with col_l:
            fig_psi = px.bar(
                df_mit.sort_values("PSI", ascending=True),
                x="PSI", y="Feature", orientation="h",
                color="Drift Type",
                color_discrete_sequence=px.colors.qualitative.Safe,
                title="PSI by Feature (coloured by Drift Type)",
                height=max(350, len(df_mit) * 24),
            )
            st.plotly_chart(fig_psi, width="stretch")

        with col_r:
            sev_counts = df_mit["Severity"].value_counts().reset_index()
            sev_counts.columns = ["Severity", "Count"]
            fig_sev = px.pie(
                sev_counts, names="Severity", values="Count",
                color_discrete_sequence=["#2a9d8f","#e9c46a","#f4a261","#e76f51","#e63946"],
                title="Drifted Features by Severity",
                hole=0.4,
            )
            st.plotly_chart(fig_sev, width="stretch")

        st.divider()
        st.subheader("Global Mitigation Strategies Applied")
        global_keys = [
            ("__cbdt_density_ratio__",       "CBDT Density Ratio"),
            ("__temporal_similarity__",       "Temporal Reweighting"),
            ("__concept_continuous_weight__", "Concept Continuous Weight"),
            ("__label_drift_correction__",    "Label Drift Correction"),
            ("__volume_drift_correction__",   "Volume Drift Correction"),
            ("__mv_drift_robust_features__",  "MV Drift Robust Features"),
            ("__missing_indicators__",        "Missing Value Indicators"),
        ]
        found_any = False
        for key, label in global_keys:
            if key in mitigation_log:
                st.markdown(f"**{label}:** {mitigation_log[key]}")
                found_any = True
        if not found_any:
            st.info("No global mitigation metadata logged.")
    else:
        st.success("No drift detected — no mitigation required.")

# ══════════════════════════════════════════════════════════════════════════════
# Tab: Preprocessing
# ══════════════════════════════════════════════════════════════════════════════

with tab_pre:
    st.subheader("Preprocessing Pipeline")

    n_raw    = preprocessing_stats.get("n_features_raw", 0)
    n_eng    = preprocessing_stats.get("n_features_after_engineering", 0)
    n_final  = preprocessing_stats.get("n_features_final", 0)
    n_cat    = preprocessing_stats.get("n_cat_features", 0)
    n_qt     = preprocessing_stats.get("n_numeric_quantile_transformed", 0)
    t_pre    = preprocessing_stats.get("t_preprocess", 0)

    p1, p2, p3, p4, p5 = st.columns(5)
    p1.metric("Raw Features",              n_raw)
    p2.metric("After Engineering",         n_eng,   f"+{n_eng - n_raw}")
    p3.metric("Final Features",            n_final, f"-{n_eng - n_final} dropped")
    p4.metric("Categorical Features",      n_cat)
    p5.metric("Quantile Transformed",      n_qt)

    st.divider()
    col_l, col_r = st.columns(2)

    with col_l:
        # Feature count funnel
        stages  = ["Raw Features", "After Engineering", "Final (post-selection)"]
        counts  = [n_raw, n_eng, n_final]
        colors  = ["#457b9d", "#2a9d8f", "#e9c46a"]
        fig_fun = go.Figure(go.Bar(
            x=counts, y=stages, orientation="h",
            marker_color=colors,
            text=counts, textposition="outside",
        ))
        fig_fun.update_layout(
            title="Feature Count Through Pipeline",
            xaxis_title="Number of Features",
            height=300, margin=dict(t=50),
            yaxis={"autorange": "reversed"},
        )
        st.plotly_chart(fig_fun, width="stretch")

    with col_r:
        # Engineered features list
        eng_feats = preprocessing_stats.get("engineered_features", [])
        dropped   = preprocessing_stats.get("dropped_at_ingestion", [])
        if eng_feats:
            st.markdown("**Engineered Features Added**")
            for f in eng_feats:
                st.markdown(f"- `{f}`")
        if dropped:
            st.markdown("**Dropped at Ingestion**")
            for f in dropped:
                st.markdown(f"- `{f}`")

    st.divider()
    col_l2, col_r2 = st.columns(2)

    with col_l2:
        st.markdown("**Imputation Strategy**")
        imputation = preprocessing_stats.get("imputation", {})
        st.markdown(f"- **Numeric:** {imputation.get('numeric', 'median')}")
        st.markdown(f"- **Categorical:** {imputation.get('categorical', 'mode')}")
        special = imputation.get("special_fills", {})
        if special:
            st.markdown("- **Special fills:**")
            for col, val in special.items():
                st.markdown(f"  - `{col}` → `\"{val}\"`")

        st.markdown("**Encoding**")
        st.markdown(f"- {preprocessing_stats.get('encoding', 'ordinal (cat.codes)')}")

        st.markdown("**Scaling**")
        st.markdown(f"- {preprocessing_stats.get('scaling', 'QuantileTransformer (normal, n_quantiles=1000)')}")

    with col_r2:
        # Missing value counts from data_stats for context
        missing = data_stats.get("missing_per_col", {})
        if missing:
            st.markdown("**Columns Requiring Imputation (Train)**")
            df_imp = pd.DataFrame(
                list(missing.items()), columns=["Column", "Missing Count"]
            ).sort_values("Missing Count", ascending=False)
            df_imp["% Missing"] = (df_imp["Missing Count"] / data_stats.get("n_train", 1) * 100).round(1)
            st.dataframe(df_imp, width="stretch", hide_index=True)

# ══════════════════════════════════════════════════════════════════════════════
# Tab: Results
# ══════════════════════════════════════════════════════════════════════════════

with tab_res:
    st.subheader("Model Results")

    t_pre_val = preprocessing_stats.get("t_preprocess", 0)
    t_total   = t_drift + t_mitigate + t_pre_val

    r1, r2, r3, r4 = st.columns(4)
    r1.metric("Train AU-PRC",  f"{auprc_train:.4f}")
    r2.metric("Test AU-PRC",   f"{auprc_test:.4f}",  f"{auprc_test - auprc_train:+.4f}")
    r3.metric("Generalisation Gap", f"{abs(auprc_test - auprc_train):.4f}")
    r4.metric("Total Runtime (s)",  f"{t_total:.1f}s")

    st.divider()
    col_l, col_r = st.columns(2)

    with col_l:
        # AU-PRC comparison bar
        fig_perf = go.Figure(go.Bar(
            x=["Train AU-PRC", "Test AU-PRC"],
            y=[auprc_train, auprc_test],
            marker_color=["#457b9d", "#e63946"],
            text=[f"{auprc_train:.4f}", f"{auprc_test:.4f}"],
            textposition="outside",
        ))
        fig_perf.update_layout(
            title="Model Performance: Train vs Test",
            yaxis=dict(range=[0, 1], title="AU-PRC"),
            height=350, margin=dict(t=50),
        )
        fig_perf.add_hline(y=0.5, line_dash="dot", line_color="gray",
                           annotation_text="Random baseline (0.5)")
        st.plotly_chart(fig_perf, width="stretch")

    with col_r:
        # Runtime breakdown
        timings = {
            "Drift Detection":  t_drift,
            "Mitigation":       t_mitigate,
            "Preprocessing":    t_pre_val,
        }
        timings = {k: v for k, v in timings.items() if v > 0}
        fig_time = go.Figure(go.Pie(
            labels=list(timings.keys()),
            values=list(timings.values()),
            hole=0.4,
            textinfo="label+percent",
            marker_colors=["#e63946", "#f4a261", "#457b9d", "#2a9d8f"],
        ))
        fig_time.update_layout(title="Runtime Breakdown (s)", height=350, margin=dict(t=50))
        st.plotly_chart(fig_time, width="stretch")

    st.divider()
    st.subheader("Pipeline Configuration")
    cfg_rows = [
        ("Model",                    "LightGBM (gradient boosting)"),
        ("Pseudo-label high thresh", "0.80"),
        ("Pseudo-label low thresh",  "0.20"),
        ("Pseudo-label weight",      "3.0×"),
        ("Max pseudo rounds",        "20 (early stopping patience=2)"),
        ("Feature selection",        "Zero-importance pruning post-training"),
        ("Sample weights",           "Drift-corrected (proximity + CBDT + temporal)"),
        ("Quantile transform",       "QuantileTransformer (normal, n_quantiles=1000)"),
        ("Categorical encoding",     "Ordinal (cat.codes) — LightGBM native support"),
    ]
    df_cfg = pd.DataFrame(cfg_rows, columns=["Setting", "Value"])
    st.dataframe(df_cfg, width="stretch", hide_index=True, height=360)
