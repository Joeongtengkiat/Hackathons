import numpy as np
import pandas as pd
from sklearn.preprocessing import QuantileTransformer

class BasePreprocessor:
    TARGET    = "ChurnStatus"
    ID_COL    = "CustomerID"
    DROP_COLS = ["CustomerID", "ChurnStatus", "Month", "__is_test__"]

    @staticmethod
    def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
        """Strip whitespace and remove spaces/underscores from column names so
        that substring-based find() lookups work regardless of input formatting
        (e.g. 'Monthly Charge', 'monthly_charge', 'MonthlyCharge' all become
        'MonthlyCharge'-compatible after lowercasing)."""
        df = df.copy()
        df.columns = [
            c.strip().replace(" ", "").replace("_", "") if not c.startswith("__") and not c.startswith("fe_")
            else c
            for c in df.columns
        ]
        return df

    def __init__(self):
        self.encoders            = {}
        self.fill_values         = {}
        self.eng_params          = {}
        self.feature_cols        = []
        self.cat_feature_indices = []

    def fit_transform(self, train_df: pd.DataFrame, test_df: pd.DataFrame):
        train_df = self._normalize_columns(train_df)
        test_df  = self._normalize_columns(test_df)

        self.feature_cols = [c for c in train_df.columns if c not in self.DROP_COLS]

        X_train = train_df[self.feature_cols]
        X_test  = test_df[[c for c in self.feature_cols if c in test_df.columns]]

        y_train = train_df[self.TARGET].astype(int).values

        X_train = self._impute(X_train, fit=True)
        X_test  = self._impute(X_test,  fit=False)

        X_train = self._engineer_features(X_train, fit=True)
        X_test  = self._engineer_features(X_test,  fit=False)

        X_train = self._encode(X_train, fit=True)
        X_test  = self._encode(X_test,  fit=False)

        self.cat_feature_indices = [
            i for i, col in enumerate(X_train.columns) if col in self.encoders
        ]

        return X_train.values, y_train, X_test.values

    def _impute(self, df: pd.DataFrame, fit: bool):
        num_cols = df.select_dtypes(include=np.number).columns.tolist()
        cat_cols = df.select_dtypes(exclude=np.number).columns.tolist()

        if fit:
            self.fill_values["__num__"] = df[num_cols].median()
            self.fill_values["__cat__"] = {
                col: (df[col].mode()[0] if not df[col].mode().empty else "Unknown")
                for col in cat_cols
            }

        if num_cols:
            df[num_cols] = df[num_cols].fillna(self.fill_values.get("__num__", 0))
        if cat_cols:
            df[cat_cols] = df[cat_cols].fillna(self.fill_values.get("__cat__", {}))

        return df

    def _engineer_features(self, df: pd.DataFrame, fit: bool) -> pd.DataFrame:
        cols = set(df.columns)
        # Sort to guarantee deterministic column matching across runs
        num_cols_sorted = sorted(c for c in cols if pd.api.types.is_numeric_dtype(df[c]))
        num_cols_set    = set(num_cols_sorted)

        def find(*keywords):
            for kw in keywords:
                kw_lower = kw.lower()
                for c in num_cols_sorted:
                    if kw_lower in c.lower():
                        return c
            return None

        def is_numeric(col):
            return col is not None and col in num_cols_set

        tenure_col   = find("tenure")
        monthly_col  = find("monthlycharg", "monthly_charg")
        t_charges    = find("totalcharges", "total_charges")
        t_revenue    = find("totalrevenue", "total_revenue")
        t_refunds    = find("totalrefund",  "total_refund", "refund")
        clv_col      = find("lifetimevalue", "lifetime_value", "clv")
        referral_col = find("numberofreferral", "referrals", "referral")

        eps = 1e-9

        if is_numeric(tenure_col):
            tenure = df[tenure_col].clip(lower=1)
            total_cols = [c for c in num_cols_sorted if "total" in c.lower()]
            for col in total_cols:
                feat = f"fe_{col}_per_month"
                df[feat] = (df[col] / tenure).astype(np.float32)

        if is_numeric(monthly_col) and is_numeric(t_charges) and is_numeric(tenure_col):
            hist_avg = df[t_charges] / df[tenure_col].clip(lower=1)
            df["fe_charge_deviation"] = (df[monthly_col] - hist_avg).astype(np.float32)

        if is_numeric(t_refunds) and is_numeric(t_revenue):
            df["fe_refund_rate"] = (df[t_refunds] / (df[t_revenue].abs() + eps)).astype(np.float32)

        if is_numeric(t_revenue):
            for col in num_cols_sorted:
                if col != t_revenue and ("extra" in col.lower() or "longdistance" in col.lower() or "long_distance" in col.lower()):
                    feat = f"fe_{col}_pct_rev"
                    df[feat] = (df[col] / (df[t_revenue].abs() + eps)).astype(np.float32)

        if is_numeric(clv_col) and is_numeric(t_revenue):
            df["fe_clv_revenue_ratio"] = (df[clv_col] / (df[t_revenue].abs() + eps)).astype(np.float32)

        if is_numeric(referral_col) and is_numeric(tenure_col):
            df["fe_referral_rate"] = (df[referral_col] / df[tenure_col].clip(lower=1)).astype(np.float32)

        if fit:
            self.eng_params["binary_cols"] = [
                c for c in cols
                if df[c].dtype == object
                and set(df[c].dropna().head(10_000).str.strip().unique()).issubset({"yes", "no"})
            ]
        binary_cols = [c for c in self.eng_params.get("binary_cols", []) if c in cols]
        if binary_cols:
            counts = np.zeros(len(df), dtype=np.int16)
            for c in binary_cols:
                counts += (df[c] == "yes").astype(np.int16)
            df["fe_service_count"] = counts

        if "month_idx" in cols:
            df["fe_month_sin"] = np.sin(2 * np.pi * df["month_idx"] / 12).astype(np.float32)
            df["fe_month_cos"] = np.cos(2 * np.pi * df["month_idx"] / 12).astype(np.float32)

        return df

    def _encode(self, df: pd.DataFrame, fit: bool):
        cat_cols = df.select_dtypes(include=["object", "category"]).columns
        for col in cat_cols:
            if fit:
                df[col] = df[col].astype("category")
                self.encoders[col] = df[col].cat.categories
            else:
                if col not in self.encoders:
                    continue
                df[col] = pd.Categorical(df[col], categories=self.encoders[col])
            # cat.codes is vectorised, unseen/NaN → -1, no per-row loop
            df[col] = df[col].cat.codes
        return df


# ─────────────────────────────────────────────────────────────────────────────
# PREPROCESSOR SUBCLASS (Optimized)
# ─────────────────────────────────────────────────────────────────────────────

class Preprocessor(BasePreprocessor):
    def __init__(self):
        super().__init__()
        self.cat_feature_indices  = []
        self.quantile_transformer = QuantileTransformer(
            output_distribution='normal',
            n_quantiles=1000,
            random_state=42
        )
        self.n_features_raw_pre_eng         = 0
        self.n_features_after_engineering   = 0
        self.n_numeric_quantile_transformed = 0
        self.dropped_at_ingestion           = []

    def fit_transform(self, train_df: pd.DataFrame, test_df: pd.DataFrame):
        print("[preprocessor] Running feature engineering...")

        train_df = self._normalize_columns(train_df)
        test_df  = self._normalize_columns(test_df)

        drop = set(self.DROP_COLS)
        self.n_features_raw_pre_eng = len([c for c in train_df.columns if c not in drop])

        train_eng = self._add_features(train_df.copy(), fit=True)
        test_eng  = self._add_features(test_df.copy(), fit=False)

        test_eng = test_eng.reindex(columns=train_eng.columns, fill_value=np.nan)

        # Get the standard processed data
        X_train_vals, y_train, X_test_vals = super().fit_transform(train_eng, test_eng)
        self.n_features_after_engineering = len(self.feature_cols)

        # ── Quantile transformation ───────────────────────────────────────────
        num_indices = [i for i in range(X_train_vals.shape[1]) if i not in self.cat_feature_indices]
        if num_indices:
            num_idx = np.array(num_indices)
            print(f"[preprocessor] Quantile transforming {len(num_indices)} numeric features...")
            X_train_vals[:, num_idx] = self.quantile_transformer.fit_transform(X_train_vals[:, num_idx])
            X_test_vals[:, num_idx]  = self.quantile_transformer.transform(X_test_vals[:, num_idx])
        self.n_numeric_quantile_transformed = len(num_indices)

        return X_train_vals, y_train, X_test_vals

    def _add_features(self, df: pd.DataFrame, fit: bool = False) -> pd.DataFrame:
        cols_lower = {c.lower(): c for c in df.columns}

        def find(*keywords):
            for kw in keywords:
                for c_lower, c_orig in cols_lower.items():
                    if kw.lower() in c_lower:
                        return c_orig
            return None

        # Drop columns in-place specifically to avoid copying the whole frame.
        voice_col = find("voiceservice", "voice_service")
        if voice_col:
            if fit:
                self.dropped_at_ingestion.append(voice_col)
            df.drop(columns=[voice_col], inplace=True)
            cols_lower.pop(voice_col.lower(), None)

        # 1. Vectorized FillNA
        fill_dict = {}
        offer_col       = find("offer")
        internet_col    = find("internettype", "internet_type")
        connectivity_col = find("connectivitytype", "connectivity_type")
        if offer_col:        fill_dict[offer_col]        = "No Offer"
        if internet_col:     fill_dict[internet_col]     = "No Internet"
        if connectivity_col: fill_dict[connectivity_col] = "No Connection"
        if fill_dict:
            for col, val in fill_dict.items():
                df[col] = df[col].fillna(val)

        # 2. Batch new features into a dictionary to prevent Pandas DataFrame fragmentation
        new_features = {}

        tenure_col = find("tenureinmonths", "tenure_in_months", "tenure")
        if tenure_col and pd.api.types.is_numeric_dtype(df[tenure_col]):
            tenure_vals = df[tenure_col].values
            bins = np.array([3, 12, 24])
            labels = np.array(["0-3 Months", "4-12 Months", "13-24 Months", "24+ Months"])
            indices = np.searchsorted(bins, tenure_vals, side='left')
            new_features["TenureGroup"] = labels[indices]

        referred_col  = find("referredafriend", "referred_a_friend", "referredfriend")
        referrals_col = find("numberofreferrals", "number_of_referrals", "numofreferrals")
        if referred_col and referrals_col:
            referred = (df[referred_col] == "yes").astype(np.float32)
            new_features["fe_referral_interaction"] = referred * df[referrals_col].values

        city_col = find("locationcity", "location_city", "city")
        if city_col:
            if fit:
                self.eng_params["city_freq"] = df[city_col].value_counts(normalize=True).to_dict()
            freq_map = self.eng_params.get("city_freq", {})
            new_features["LocationCityFreq"] = df[city_col].map(freq_map).fillna(0).astype(np.float32)
        
        # Concat all new features in a single memory operation
        if new_features:
            df = pd.concat([df, pd.DataFrame(new_features, index=df.index)], axis=1)

        return df

