"""Forecast evaluation.

Every replayed forecast is scored against what happened next. Because the history comes from a
simulator, "what happened" is true demand, including the demand lost to stockouts that real
sales data can never show. Three reference points frame the model's error:

* the status-quo forecasts a planning team would use (trailing averages of sales);
* the model's own starting point, the uncensored run-rate, which isolates what the learned
  model adds beyond stockout correction;
* an oracle that knows the true expected demand. Its error is pure randomness and no forecaster
  can beat it, so the gap between the status quo and the oracle is the only error worth chasing.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import poisson

from demand_agent.config import CATEGORIES, CATEGORY_LABELS, QUANTILES, WINDOWS
from demand_agent.data.panel import Panel
from demand_agent.models.pipeline import ForecastRun, window_totals

MODEL = "Demand agent"
RUN_RATE = "Uncensored run-rate"
IN_STOCK = "In-stock sales rate"
TRAILING = "Trailing 28-day sales"
LAST_WEEK = "Last week's sales"
ORACLE = "Oracle (noise floor)"
METHODS = (MODEL, RUN_RATE, IN_STOCK, TRAILING, LAST_WEEK, ORACLE)


def reference_rates(panel: Panel) -> dict[str, np.ndarray]:
    """Status-quo daily demand rates per store and product at the panel's last day."""
    t_n = panel.n_days
    age = t_n - panel.start_day
    sales = panel.sales.astype(float)
    last28 = sales[-28:].sum(axis=0)
    in_stock_days = (panel.sod[-28:] > 0).sum(axis=0)
    return {
        TRAILING: last28.sum(axis=-1) / np.clip(age, 1, 28),
        IN_STOCK: (last28 / np.maximum(in_stock_days, 1)).sum(axis=-1),
        LAST_WEEK: sales[-7:].sum(axis=(0, 3)) / np.clip(age, 1, 7),
    }


@dataclass
class Scored:
    """Long arrays of forecasts and outcomes pooled over folds (one row per pair and window)."""

    fold: np.ndarray
    store: np.ndarray
    sku: np.ndarray
    window: np.ndarray
    age: np.ndarray
    actual: np.ndarray
    point: dict[str, np.ndarray]
    quantiles: np.ndarray  # calibrated, [n, Q]
    raw_quantiles: np.ndarray
    reference_quantiles: np.ndarray  # Poisson around the in-stock sales rate
    calibrated: np.ndarray  # bool per row


def score_run(run: ForecastRun, fold: int, truth_demand: np.ndarray, truth_mean: np.ndarray) -> Scored:
    """Compare one forecast with true demand over each of its windows."""
    fc, panel = run.forecast, run.panel
    actual = window_totals(truth_demand, run.cutoff)  # [S, K, W]
    oracle = window_totals(truth_mean, run.cutoff)
    lengths = np.array([n for _, n in WINDOWS], dtype=float)
    keep = fc.active[..., None] & np.isfinite(actual) & np.isfinite(fc.mean)
    s, k, w = np.nonzero(keep)
    rates = reference_rates(panel)
    point = {
        MODEL: fc.mean[keep],
        RUN_RATE: (fc.level[..., None] * lengths)[keep],
        ORACLE: oracle[keep],
        **{name: (rate[..., None] * lengths)[keep] for name, rate in rates.items()},
    }
    ref_q = np.stack([poisson.ppf(q, np.maximum(point[IN_STOCK], 1e-9)) for q in QUANTILES], axis=1)
    n = len(s)
    return Scored(
        fold=np.full(n, fold),
        store=s,
        sku=k,
        window=w,
        age=(run.cutoff - panel.start_day + 1)[s, k],
        actual=actual[keep],
        point=point,
        quantiles=fc.quantiles[keep],
        raw_quantiles=fc.raw_quantiles[keep],
        reference_quantiles=ref_q,
        calibrated=np.full(n, run.calibrated),
    )


def pool(parts: list[Scored]) -> Scored:
    cat = np.concatenate
    return Scored(
        fold=cat([p.fold for p in parts]),
        store=cat([p.store for p in parts]),
        sku=cat([p.sku for p in parts]),
        window=cat([p.window for p in parts]),
        age=cat([p.age for p in parts]),
        actual=cat([p.actual for p in parts]),
        point={m: cat([p.point[m] for p in parts]) for m in parts[0].point},
        quantiles=cat([p.quantiles for p in parts]),
        raw_quantiles=cat([p.raw_quantiles for p in parts]),
        reference_quantiles=cat([p.reference_quantiles for p in parts]),
        calibrated=cat([p.calibrated for p in parts]),
    )


def wape(actual: np.ndarray, pred: np.ndarray) -> float:
    """Weighted absolute percentage error: total absolute miss over total demand."""
    return float(np.abs(actual - pred).sum() / max(actual.sum(), 1e-9))


def bias(actual: np.ndarray, pred: np.ndarray) -> float:
    return float((pred.sum() - actual.sum()) / max(actual.sum(), 1e-9))


def pinball(actual: np.ndarray, quantiles: np.ndarray) -> float:
    """Mean pinball loss over all quantiles, divided by mean demand (lower is better)."""
    q = np.array(QUANTILES)[None, :]
    diff = actual[:, None] - quantiles
    return float(np.maximum(q * diff, (q - 1.0) * diff).mean() / max(actual.mean(), 1e-9))


def _point_table(sc: Scored, mask: np.ndarray) -> dict[str, dict[str, float]]:
    return {
        m: {"wape": wape(sc.actual[mask], sc.point[m][mask]), "bias": bias(sc.actual[mask], sc.point[m][mask])}
        for m in METHODS
    }


def _aggregate_wape(sc: Scored, groups: np.ndarray, mask: np.ndarray, methods=(MODEL, IN_STOCK, TRAILING)) -> dict:
    """WAPE after summing forecasts and outcomes within ``groups`` (accuracy at coarser levels)."""
    _, inv = np.unique(groups[mask], return_inverse=True)
    actual = np.bincount(inv, weights=sc.actual[mask])
    return {m: wape(actual, np.bincount(inv, weights=sc.point[m][mask])) for m in methods}


def summarise(sc: Scored, panel: Panel, fold_labels: dict[int, str]) -> dict:
    """All evaluation tables shown in the dashboard and the README."""
    every = np.ones(len(sc.actual), dtype=bool)
    cal = sc.calibrated
    cat_code = panel.category_code[sc.sku]
    w28, w7 = WINDOWS.index((1, 28)), WINDOWS.index((1, 7))

    overall = _point_table(sc, every)
    floor = overall[ORACLE]["wape"]

    def skill(method: str, reference: str = TRAILING) -> float:
        gap = overall[reference]["wape"] - floor
        return float((overall[reference]["wape"] - overall[method]["wape"]) / gap) if gap > 1e-9 else 0.0

    coverage = {
        "nominal": list(QUANTILES),
        "raw": [float((sc.actual[cal] <= sc.raw_quantiles[cal][:, j]).mean()) for j in range(len(QUANTILES))],
        "calibrated": [float((sc.actual[cal] <= sc.quantiles[cal][:, j]).mean()) for j in range(len(QUANTILES))],
        "reference": [
            float((sc.actual[cal] <= sc.reference_quantiles[cal][:, j]).mean()) for j in range(len(QUANTILES))
        ],
    }
    lo, hi = QUANTILES.index(0.1), QUANTILES.index(0.9)
    interval = {
        "nominal": 0.8,
        "raw": float(
            ((sc.actual[cal] >= sc.raw_quantiles[cal][:, lo]) & (sc.actual[cal] <= sc.raw_quantiles[cal][:, hi])).mean()
        ),
        "calibrated": float(
            ((sc.actual[cal] >= sc.quantiles[cal][:, lo]) & (sc.actual[cal] <= sc.quantiles[cal][:, hi])).mean()
        ),
    }
    by_length = {}
    for length in (7, 14, 21, 28):
        m = cal & np.isin(sc.window, [i for i, (_, n) in enumerate(WINDOWS) if n == length])
        by_length[str(length)] = {
            "raw": float((sc.actual[m] <= sc.raw_quantiles[m][:, hi]).mean()),
            "calibrated": float((sc.actual[m] <= sc.quantiles[m][:, hi]).mean()),
        }

    pair_key = sc.fold * 10_000_000 + sc.store * 100_000 + sc.sku * 100 + sc.window
    del pair_key
    store_cat = sc.fold * 1_000_000 + sc.store * 100 + cat_code
    store_only = sc.fold * 1_000 + sc.store
    network = sc.fold
    levels = {}
    for name, w in (("7 days", w7), ("28 days", w28)):
        m = sc.window == w
        levels[name] = {
            "Product x store": {k: _point_table(sc, m)[k]["wape"] for k in (MODEL, IN_STOCK, TRAILING)},
            "Category x store": _aggregate_wape(sc, store_cat, m),
            "Store": _aggregate_wape(sc, store_only, m),
            "Network": _aggregate_wape(sc, network, m),
        }

    return {
        "methods": list(METHODS),
        "overall": overall,
        "noise_floor": floor,
        "skill_vs_trailing": skill(MODEL, TRAILING),
        "skill_vs_in_stock": skill(MODEL, IN_STOCK),
        "by_window": {
            f"{start}-{start + length - 1}": {m: v["wape"] for m, v in _point_table(sc, sc.window == w).items()}
            for w, (start, length) in enumerate(WINDOWS)
        },
        "by_category": {
            CATEGORY_LABELS[c]: {
                m: v["wape"] for m, v in _point_table(sc, (cat_code == i) & (sc.window == w28)).items()
            }
            for i, c in enumerate(CATEGORIES)
        },
        "by_store": {
            str(panel.stores["name"].iloc[i]): {
                m: v["wape"] for m, v in _point_table(sc, (sc.store == i) & (sc.window == w28)).items()
            }
            for i in range(panel.n_stores)
            if ((sc.store == i) & (sc.window == w28)).any()
        },
        "by_age": {
            label: {m: v["wape"] for m, v in _point_table(sc, mask & (sc.window == w28)).items()}
            for label, mask in (
                ("New (under 4 weeks)", sc.age < 28),
                ("Young (4-12 weeks)", (sc.age >= 28) & (sc.age < 84)),
                ("Established", sc.age >= 84),
            )
            if (mask & (sc.window == w28)).any()
        },
        "by_fold": {
            fold_labels[f]: {
                m: v for m, v in _point_table(sc, sc.fold == f).items() if m in (MODEL, IN_STOCK, TRAILING, ORACLE)
            }
            for f in np.unique(sc.fold)
        },
        "levels": levels,
        "coverage": coverage,
        "interval80": interval,
        "p90_by_length": by_length,
        "pinball": {
            MODEL: pinball(sc.actual[cal], sc.quantiles[cal]),
            "Demand agent (uncalibrated)": pinball(sc.actual[cal], sc.raw_quantiles[cal]),
            "Poisson around in-stock rate": pinball(sc.actual[cal], sc.reference_quantiles[cal]),
        },
        "rows": int(len(sc.actual)),
        "calibrated_rows": int(cal.sum()),
    }
