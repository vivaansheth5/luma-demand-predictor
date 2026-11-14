"""Gradient-boosted demand models.

One LightGBM model predicts expected demand (Tweedie loss, which suits low counts with many
zeros). One model per quantile predicts the spread (pinball loss). All of them share a feature
matrix and predict demand relative to the series' run-rate.

Raw quantile models are not trustworthy on their own: nothing forces a stated 90% to cover 90%
of outcomes. :class:`CalibrationSet` stores how earlier forecasts missed once their windows were
observed, and shifts each quantile by the amount that would have made it exact (conformal
calibration, per quantile and per window length, on residuals scaled by the series' noise).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import lightgbm as lgb
import numpy as np

from luma.config import QUANTILES, ModelSettings
from luma.features.build import FeatureFrame, feature_groups


@dataclass
class Prediction:
    mean: np.ndarray  # [n] units over the window
    quantiles: np.ndarray  # [n, Q] calibrated quantiles, units over the window
    raw_quantiles: np.ndarray  # [n, Q] before calibration


def noise_scale(mean: np.ndarray) -> np.ndarray:
    """Typical size of a forecast miss for a count with this mean (Poisson-like)."""
    return np.sqrt(np.maximum(mean, 0.0) + 1.0)


@dataclass
class CalibrationSet:
    """Out-of-sample quantile errors from forecasts whose windows have since been observed.

    Calibration corrects spread, not level. Every past forecast date carries its own common
    miss (the festival it under-read, the fad that had just ended), and that miss does not
    transfer to the next date. So each date's median error is removed before errors are pooled;
    what remains says how much too narrow or too wide the quantiles are, which does transfer.
    """

    quantiles: tuple[float, ...] = QUANTILES
    lengths: list[np.ndarray] = field(default_factory=list)
    residuals: list[np.ndarray] = field(default_factory=list)  # centred (actual - quantile) / noise

    def add(self, length: np.ndarray, actual: np.ndarray, mean: np.ndarray, raw_quantiles: np.ndarray) -> None:
        """Record the errors of one forecast date."""
        keep = np.isfinite(actual) & np.isfinite(mean)
        length = length[keep]
        resid = (actual[keep, None] - raw_quantiles[keep]) / noise_scale(mean[keep])[:, None]
        mid = int(np.argmin(np.abs(np.array(self.quantiles) - 0.5)))
        for value in np.unique(length):
            rows = length == value
            resid[rows] -= np.median(resid[rows, mid])
        self.lengths.append(length)
        self.residuals.append(resid)

    def __len__(self) -> int:
        return int(sum(len(x) for x in self.lengths))

    def offsets(self) -> dict[int, np.ndarray]:
        """Shift per quantile and window length that gives past forecasts the right spread."""
        if not self.lengths:
            return {}
        length = np.concatenate(self.lengths)
        resid = np.concatenate(self.residuals)
        out: dict[int, np.ndarray] = {}
        for value in np.unique(length):
            rows = length == value
            n = int(rows.sum())
            out[int(value)] = np.array(
                [np.quantile(resid[rows, j], min(q * (1.0 + 1.0 / n), 1.0)) for j, q in enumerate(self.quantiles)]
            )
        return out


@dataclass
class DemandForecaster:
    settings: ModelSettings = field(default_factory=ModelSettings)
    quantiles: tuple[float, ...] = QUANTILES
    mean_model: lgb.Booster | None = None
    quantile_models: list[lgb.Booster] = field(default_factory=list)
    offsets: dict[int, np.ndarray] = field(default_factory=dict)  # window length -> shift per quantile
    feature_names: list[str] = field(default_factory=list)
    train_rows: int = 0

    # ---- training --------------------------------------------------------------------------
    def _params(self, objective: str, **extra) -> dict:
        s = self.settings
        return {
            "objective": objective,
            "learning_rate": s.learning_rate,
            "num_leaves": s.num_leaves,
            "min_data_in_leaf": s.min_data_in_leaf,
            "feature_fraction": s.feature_fraction,
            "bagging_fraction": s.bagging_fraction,
            "bagging_freq": 1,
            "lambda_l2": s.lambda_l2,
            "max_bin": 127,
            "cat_smooth": 10.0,
            "min_data_per_group": 40,
            "seed": s.seed,
            "num_threads": s.num_threads,
            "deterministic": True,
            "force_row_wise": True,
            "verbose": -1,
            **extra,
        }

    @staticmethod
    def sample_weight(frame: FeatureFrame) -> np.ndarray:
        """Weight rows towards unit volume so the loss tracks units, without ignoring slow sellers."""
        w = np.clip(frame.level, 0.05, 6.0) ** 0.75 * np.sqrt(frame.length / 7.0)
        return w / w.mean()

    def fit(self, frame: FeatureFrame) -> DemandForecaster:
        if frame.y is None:
            raise ValueError("training frame needs a target")
        s = self.settings
        if s.row_subsample < 1.0:
            keep = np.random.default_rng(s.seed).random(len(frame)) < s.row_subsample
            frame = frame.subset(keep)
        self.feature_names = list(frame.X.columns)
        self.train_rows = len(frame)
        data = lgb.Dataset(
            frame.X,
            label=frame.y,
            weight=self.sample_weight(frame),
            categorical_feature=list(frame.categorical),
            free_raw_data=False,
        )
        self.mean_model = lgb.train(
            self._params("tweedie", tweedie_variance_power=s.tweedie_power), data, num_boost_round=s.n_estimators_mean
        )
        if frame.y_draw is not None:
            # Quantiles are learned from a realisation with natural spread, not from averages.
            data = lgb.Dataset(
                frame.X,
                label=frame.y_draw,
                weight=self.sample_weight(frame),
                categorical_feature=list(frame.categorical),
                free_raw_data=False,
            )
        self.quantile_models = [
            lgb.train(self._params("quantile", alpha=q), data, num_boost_round=s.n_estimators_quantile)
            for q in self.quantiles
        ]
        return self

    # ---- prediction ------------------------------------------------------------------------
    def predict(self, frame: FeatureFrame) -> Prediction:
        if self.mean_model is None:
            raise RuntimeError("model is not fitted")
        x = frame.X[self.feature_names]
        scale = frame.scale
        mean = np.maximum(self.mean_model.predict(x), 0.0) * scale
        raw = np.stack([m.predict(x) for m in self.quantile_models], axis=1) * scale[:, None]
        # Quantiles must be non-negative and ordered (rearrangement removes any crossing).
        raw = np.sort(np.maximum(raw, 0.0), axis=1)
        q = raw.copy()
        if self.offsets:
            noise = noise_scale(mean)
            for length, shift in self.offsets.items():
                rows = frame.length == length
                q[rows] += shift[None, :] * noise[rows, None]
            q = np.sort(np.maximum(q, 0.0), axis=1)
        return Prediction(mean=mean, quantiles=q, raw_quantiles=raw)

    # ---- explanation -----------------------------------------------------------------------
    def explain(self, frame: FeatureFrame) -> tuple[list[str], np.ndarray]:
        """Multiplicative effect of each driver group on the expected-demand forecast.

        LightGBM's exact TreeSHAP values are additive in log space for the Tweedie model, so the
        exponential of a group's summed values is a factor: 1.15 means that group raises the
        forecast 15% above the model's average prediction. Returns ``(names, [n, G] factors)``.
        """
        if self.mean_model is None:
            raise RuntimeError("model is not fitted")
        contrib = self.mean_model.predict(frame.X[self.feature_names], pred_contrib=True)
        names, group_of = feature_groups(self.feature_names)
        grouped = np.zeros((len(frame), len(names)))
        for g in range(len(names)):
            grouped[:, g] = contrib[:, :-1][:, group_of == g].sum(axis=1)
        return names, np.exp(grouped)

    def importance(self) -> dict[str, float]:
        """Share of total split gain per driver group in the expected-demand model."""
        if self.mean_model is None:
            raise RuntimeError("model is not fitted")
        gain = self.mean_model.feature_importance(importance_type="gain")
        names, group_of = feature_groups(self.feature_names)
        total = np.zeros(len(names))
        np.add.at(total, group_of, gain)
        return dict(zip(names, (total / max(total.sum(), 1e-12)).tolist(), strict=True))
