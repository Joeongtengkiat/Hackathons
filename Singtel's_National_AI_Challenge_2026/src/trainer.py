"""
trainer.py — Fixed LightGBM trainer per NAISC 2026 challenge rules.
"""

import numpy as np
import joblib
from lightgbm import LGBMClassifier
from sklearn.metrics import average_precision_score


class Trainer:
    FIXED_PARAMS = {
        "verbosity":       -1,
        "objective":       "binary",
        "is_unbalance":    True,
        "importance_type": "gain",
    }

    def __init__(self, seed: int = 42):
        self.model       = LGBMClassifier(**self.FIXED_PARAMS, random_state=seed)
        self.auprc_train = None
        self.auprc_test  = None

    def train(self, X_train, y_train, sample_weights=None, cat_feature_indices=None):
        self.model.fit(
            X_train,
            y_train,
            sample_weight=sample_weights,
            categorical_feature=cat_feature_indices if cat_feature_indices else "auto",
        )
        self.auprc_train = average_precision_score(y_train, self.predict_proba(X_train))
        return self

    def predict_proba(self, X) -> np.ndarray:
        return self.model.predict_proba(X)[:, 1]

    def evaluate_test(self, X_test, y_test):
        self.auprc_test = average_precision_score(y_test, self.predict_proba(X_test))
        return self.auprc_test

    def save(self, path: str = "model.joblib"):
        joblib.dump(self.model, path)
        print(f"💾 Model saved to {path}")
