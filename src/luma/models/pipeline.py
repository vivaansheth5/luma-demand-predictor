"""Training and forecasting pipeline.

``fit_forecaster`` turns a panel into a model. ``make_forecast`` asks that model for demand over
every window, for every (store, product) pair live at the origin, and exposes the answer as the
distribution the inventory logic needs. ``rolling_forecasts`` replays the weeks before the as-of
date the way production would have lived them: forecast, wait for the window to be observed,
record the miss, and use the accumulated misses to calibrate the next forecast.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from datetime import date

import numpy as np
from scipy.stats import nbinom

from luma.config import HORIZON_DAYS, QUANTILES, WINDOWS, ModelSettings, window_index
from luma.data.panel import Panel
from luma.features.build import FeatureBuilder
from luma.models.gbm import CalibrationSet, DemandForecaster
from luma.models.uncensor import Uncensored, uncensor


@dataclass
class Forecast:
    """Demand forecast made at the end of day ``origin``."""

    origin: int
    asof: date
    active: np.ndarray  # [S, K]
    level: np.ndarray  # [S, K] recent run-rate, units per day
    mean: np.ndarray  # [S, K, W] expected units per window
    quantiles: np.ndarray  # [S, K, W, Q] calibrated
    raw_quantiles: np.ndarray  # [S, K, W, Q] before calibration
    driver_names: list[str]
    drivers: np.ndarray  # [S, K, G] multiplicative effect per driver group on the 28-day forecast

    def window(self, start: int, length: int) -> tuple[np.ndarray, np.ndarray]:
        w = window_index(start, length)
        return self.mean[:, :, w], self.quantiles[:, :, w, :]

    def negbin(self, start: int, length: int) -> tuple[np.ndarray, np.ndarray]:
        """Negative-binomial ``(mean, shape)`` matched to the forecast for a window.

        The mean comes from the expected-demand model. The shape is set so the distribution's
        spread equals the calibrated P10-P90 width. A negative binomial thinned by a size share
        ``p`` is again negative binomial with mean ``p * mean`` and the same shape, which is how
        a product-level forecast becomes a size-level distribution without a second model.
        """
        mean, q = self.window(start, length)
        lo, hi = q[..., QUANTILES.index(0.1)], q[..., QUANTILES.index(0.9)]
        sigma = (hi - lo) / 2.563
        mean = np.nan_to_num(mean, nan=0.0)
        var = np.maximum(np.nan_to_num(sigma, nan=0.0) ** 2, mean * 1.02)
        shape = np.clip(mean**2 / np.maximum(var - mean, 1e-9), 0.3, 400.0)
        return np.maximum(mean, 1e-6), shape


def nb_sf(k: np.ndarray, mean: np.ndarray, shape: np.ndarray) -> np.ndarray:
    """``P(D > k)`` for a negative binomial given by mean and shape."""
    return nbinom.sf(k, shape, shape / (shape + mean))


def nb_ppf(q: float | np.ndarray, mean: np.ndarray, shape: np.ndarray) -> np.ndarray:
    """Quantile of a negative binomial given by mean and shape."""
    return nbinom.ppf(q, shape, shape / (shape + mean))


def expected_shortage(stock: np.ndarray, mean: np.ndarray, shape: np.ndarray, support: int = 60) -> np.ndarray:
    """``E[(D - stock)^+]``: expected units of demand that ``stock`` cannot serve."""
    k = np.arange(support).reshape((1,) * mean.ndim + (support,))
    p = (shape / (shape + mean))[..., None]
    return nbinom.sf(stock[..., None] + k, shape[..., None], p).sum(axis=-1)


def truncated(panel: Panel, n_days: int) -> Panel:
    """The panel as it looked at the end of day ``n_days - 1`` (for backtests)."""
    if n_days >= panel.n_days:
        return panel
    return replace(
        panel,
        sales=panel.sales[:n_days],
        sod=panel.sod[:n_days],
        eod=panel.eod[:n_days],
        weather=panel.weather.iloc[:n_days].reset_index(drop=True),
        buzz=panel.buzz[:n_days],
        on_hand=panel.eod[n_days - 1].copy(),
        pipeline=np.zeros_like(panel.pipeline),
    )


def fit_forecaster(
    panel: Panel, settings: ModelSettings, unc: Uncensored | None = None
) -> tuple[DemandForecaster, FeatureBuilder]:
    """Uncensor demand, build features for every labelled origin, and fit the models."""
    unc = unc or uncensor(panel)
    builder = FeatureBuilder(panel, unc.by_sku, settings.level_prior_days, demand_draw=unc.draw_by_sku)
    origins = builder.labelled_origins(settings.origin_step, settings.min_history_days)
    if len(origins) < 4:
        raise ValueError(f"need at least 4 labelled origins to train, got {len(origins)}")
    model = DemandForecaster(settings).fit(builder.build(origins))
    return model, builder


def make_forecast(model: DemandForecaster, builder: FeatureBuilder, origin: int | None = None) -> Forecast:
    """Forecast every window for every live pair at ``origin`` (default: the latest day)."""
    panel = builder.panel
    origin = panel.n_days - 1 if origin is None else origin
    frame = builder.build([origin], with_target=False)
    pred = model.predict(frame)
    s_n, k_n, w_n, q_n = panel.n_stores, panel.n_skus, len(WINDOWS), len(model.quantiles)
    mean = np.full((s_n, k_n, w_n), np.nan)
    quant = np.full((s_n, k_n, w_n, q_n), np.nan)
    raw = np.full((s_n, k_n, w_n, q_n), np.nan)
    mean[frame.store, frame.sku, frame.window] = pred.mean
    quant[frame.store, frame.sku, frame.window] = pred.quantiles
    raw[frame.store, frame.sku, frame.window] = pred.raw_quantiles

    # Cumulative windows must not shrink as they lengthen.
    cumulative = [window_index(1, n) for n in (7, 14, 21, 28)]
    mean[:, :, cumulative] = np.maximum.accumulate(mean[:, :, cumulative], axis=2)
    quant[:, :, cumulative, :] = np.maximum.accumulate(quant[:, :, cumulative, :], axis=2)

    level, _, age = builder.run_rate(origin)
    rows = frame.window == window_index(1, 28)
    names, factors = model.explain(frame.subset(rows))
    drivers = np.full((s_n, k_n, len(names)), np.nan)
    drivers[frame.store[rows], frame.sku[rows]] = factors
    return Forecast(
        origin=origin,
        asof=panel.date_of(origin),
        active=age >= 1,
        level=level,
        mean=mean,
        quantiles=quant,
        raw_quantiles=raw,
        driver_names=names,
        drivers=drivers,
    )


def window_totals(daily: np.ndarray, origin: int) -> np.ndarray:
    """Totals of ``daily`` (``[T, S, K]``) over every forecast window after ``origin``.

    Returns ``[S, K, W]`` with NaN for windows that extend past the data.
    """
    t_n = daily.shape[0]
    cum = np.concatenate([np.zeros((1, *daily.shape[1:])), np.cumsum(daily, axis=0, dtype=np.float64)])
    out = np.full((*daily.shape[1:], len(WINDOWS)), np.nan)
    for w, (start, length) in enumerate(WINDOWS):
        lo, hi = origin + start, origin + start + length
        if hi <= t_n:
            out[..., w] = cum[hi] - cum[lo]
    return out


def record_errors(calibration: CalibrationSet, forecast: Forecast, observed: np.ndarray) -> None:
    """Add a past forecast's quantile errors to the calibration set.

    ``observed`` is daily demand per store and product as estimated after the fact, covering
    the forecast's windows (a realisation of uncensored sales; never simulator truth).
    """
    actual = window_totals(observed, forecast.origin)
    lengths = np.broadcast_to(np.array([n for _, n in WINDOWS], dtype=float), actual.shape)
    keep = forecast.active[..., None] & np.isfinite(actual) & np.isfinite(forecast.mean)
    calibration.add(lengths[keep], actual[keep], forecast.mean[keep], forecast.raw_quantiles[keep])


@dataclass
class ForecastRun:
    """One forecast in the rolling replay."""

    cutoff: int  # origin day index
    forecast: Forecast
    panel: Panel  # the panel as of the cutoff
    uncensored: Uncensored
    calibrated: bool  # whether earlier errors were available to calibrate this forecast
    fit_seconds: float
    train_rows: int
    importance: dict[str, float]


@dataclass
class RollingResult:
    folds: list[ForecastRun]  # oldest first; evaluation folds
    live: ForecastRun  # the forecast at the as-of date
    model: DemandForecaster
    builder: FeatureBuilder
    calibration: CalibrationSet


MIN_TRAIN_DAYS = 106  # shortest history with four labelled origins after the warm-up


def rolling_forecasts(panel: Panel, settings: ModelSettings, progress=None) -> RollingResult:
    """Replay ``backtest_folds`` earlier forecast dates, then forecast at the as-of date.

    Each replayed forecast is trained only on data up to its own cutoff. Once its 28-day horizon
    has passed, its errors (against demand as estimated at that later date) join the calibration
    set used by every subsequent forecast, exactly as a weekly production job would do it.
    """
    t_n = panel.n_days
    calibration = CalibrationSet()
    runs: list[ForecastRun] = []
    # Each replayed date needs enough earlier history to train on; short histories replay fewer.
    folds = max(0, min(settings.backtest_folds, (t_n - MIN_TRAIN_DAYS) // HORIZON_DAYS))
    cutoffs = [t_n - 1 - HORIZON_DAYS * f for f in range(folds, 0, -1)] + [t_n - 1]
    model = builder = None
    for i, cutoff in enumerate(cutoffs):
        if progress:
            progress(f"forecast origin {panel.date_of(cutoff)} ({i + 1}/{len(cutoffs)})")
        view = truncated(panel, cutoff + 1)
        started = time.perf_counter()
        unc = uncensor(view)
        model, builder = fit_forecaster(view, settings, unc)
        model.offsets = calibration.offsets()
        forecast = make_forecast(model, builder)
        runs.append(
            ForecastRun(
                cutoff=cutoff,
                forecast=forecast,
                panel=view,
                uncensored=unc,
                calibrated=bool(model.offsets),
                fit_seconds=time.perf_counter() - started,
                train_rows=model.train_rows,
                importance=model.importance(),
            )
        )
        if cutoff + HORIZON_DAYS < t_n:
            later = uncensor(truncated(panel, cutoff + 1 + HORIZON_DAYS)).draw_by_sku
            record_errors(calibration, forecast, later)
    assert model is not None and builder is not None
    return RollingResult(folds=runs[:-1], live=runs[-1], model=model, builder=builder, calibration=calibration)
