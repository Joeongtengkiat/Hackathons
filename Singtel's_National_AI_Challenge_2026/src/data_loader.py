import pandas as pd
import re
import numpy as np

class DataLoader:
    TARGET    = "ChurnStatus"
    ID_COL    = "CustomerID"
    DROP_COLS = ["CustomerID", "ChurnStatus", "Month"]

    _SPLIT_COL = "__is_test__"

    def __init__(self, train_path: str, test_path: str):
        self.train_path = train_path
        self.test_path  = test_path
        self.train_df   = None
        self.test_df    = None
        self.combined   = None

    def load(self):
        self.train_df = pd.read_csv(self.train_path, engine='pyarrow')
        self.test_df  = pd.read_csv(self.test_path,  engine='pyarrow')
        self._downcast(self.train_df)
        self._downcast(self.test_df)
        self._validate()
        self._normalise_categoricals()
        self._drop_constant_columns()
        self._add_month_index()
        self._binarise_target()

        print(f"   Train : {self.train_df.shape}  |  months: {self._month_range(self.train_df)}")
        print(f"   Test  : {self.test_df.shape}  |  months: {self._month_range(self.test_df)}")

        return self.train_df, self.test_df

    def detection_sample(self, n: int = 200_000) -> pd.DataFrame:
        """Build a small combined sample for drift detection only — no full combine needed."""
        n_train = min(int(n * 0.75), len(self.train_df))
        n_test  = min(n - n_train,   len(self.test_df))
        sample = pd.concat([
            self.train_df.sample(n=n_train, random_state=42).assign(**{self._SPLIT_COL: np.int8(0)}),
            self.test_df.sample(n=n_test,   random_state=42).assign(**{self._SPLIT_COL: np.int8(1)}),
        ], ignore_index=True).sort_values("month_idx").reset_index(drop=True)
        print(f"   Detection sample : {sample.shape}")
        return sample

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _normalise_categoricals(self):
        """TRUE OPTIMIZATION: Only clean unique values, then map. Massive speedup over N elements."""
        rgx_hyphen = re.compile(r'[-_]')
        rgx_space = re.compile(r'\s+')

        def clean_val(val):
            if pd.isna(val) or not isinstance(val, str):
                return val
            s = val.replace('\xa0', ' ').strip().lower()
            s = rgx_hyphen.sub(' ', s)
            return rgx_space.sub(' ', s).strip()

        for df in [self.train_df, self.test_df]:
            str_cols = [c for c in df.select_dtypes(include=["object"]).columns if c != "Month"]
            for col in str_cols:
                uniques = df[col].dropna().unique()
                clean_map = {u: clean_val(u) for u in uniques}
                df[col] = df[col].map(clean_map).fillna(df[col])

    def _drop_constant_columns(self):
        constant_cols = [
            c for c in self.train_df.columns
            if c not in (self.TARGET, self.ID_COL, "Month")
            and self.train_df[c].nunique() <= 1
        ]
        if constant_cols:
            print(f"   Dropping constant columns: {constant_cols}")
            self.train_df.drop(columns=constant_cols, inplace=True)
            self.test_df.drop(columns=[c for c in constant_cols if c in self.test_df.columns], inplace=True)

    def _downcast(self, df: pd.DataFrame) -> None:
        """Cast float64 columns to float32 in-place — halves memory footprint."""
        float_cols = df.select_dtypes("float64").columns
        if len(float_cols):
            df[float_cols] = df[float_cols].astype(np.float32)

    def _validate(self):
        assert self.TARGET in self.train_df.columns, f"Missing '{self.TARGET}' in train"
        assert self.ID_COL in self.test_df.columns,  f"Missing '{self.ID_COL}' in test"

    def _add_month_index(self):
        """Optimized: Map to_datetime over unique months only."""
        for df in [self.train_df, self.test_df]:
            unique_months = df["Month"].dropna().unique()
            parsed_uniques = pd.to_datetime(pd.Series(unique_months), format="%y-%b", errors="coerce")
            month_map = dict(zip(unique_months, parsed_uniques))
            
            parsed = df["Month"].map(month_map)
            
            if parsed.isna().any():
                import warnings
                bad = df.loc[parsed.isna(), "Month"].unique().tolist()
                warnings.warn(f"[DataLoader] Could not parse Month values: {bad}. These rows get month_idx=-1.", RuntimeWarning)
            df["month_idx"] = (parsed.dt.year * 12 + parsed.dt.month).fillna(-1).astype(int)

    def _binarise_target(self):
        """Convert ChurnStatus from 'Yes'/'No' strings to 1/0 integer once at load time."""
        col = self.TARGET
        if col in self.train_df.columns:
            self.train_df[col] = (
                self.train_df[col].astype(str).str.strip().str.lower() == "yes"
            ).astype(np.int8)
        if col in self.test_df.columns:
            self.test_df[col] = (
                self.test_df[col].astype(str).str.strip().str.lower() == "yes"
            ).astype(np.int8)

    def _month_range(self, df):
        return ", ".join(
            df.drop_duplicates("month_idx")
            .sort_values("month_idx")["Month"]
            .tolist()
        )

    @property
    def test_ids(self):
        return self.test_df[self.ID_COL].values