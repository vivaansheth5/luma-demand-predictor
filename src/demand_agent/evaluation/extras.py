"""Checks of the supporting models against simulator truth."""

from __future__ import annotations

import numpy as np

from demand_agent.data.panel import Panel
from demand_agent.models.sizecurve import SizeCurves
from demand_agent.models.uncensor import Uncensored
from demand_agent.signals.trends import TrendSignal
from demand_agent.sim.world import Truth


def uncensoring_report(panel: Panel, unc: Uncensored, truth: Truth) -> dict:
    """How much of the demand hidden by stockouts the uncensoring step recovers."""
    t = panel.n_days
    true = truth.demand[:t].astype(float)
    sales = panel.sales.astype(float)
    est = unc.demand.astype(float)
    weeks = t // 7

    def weekly(a: np.ndarray) -> np.ndarray:
        return a[-weeks * 7 :].sum(axis=(1, 2, 3)).reshape(weeks, 7).sum(axis=1)

    lost = true.sum() - sales.sum()
    last = slice(t - 28, t)
    return {
        "censored_share": unc.censored_share,
        "true_lost_units": float(lost),
        "recovered_units": float(est.sum() - sales.sum()),
        "recovery_ratio": float((est.sum() - sales.sum()) / max(lost, 1e-9)),
        "bias_sales": float(sales.sum() / true.sum() - 1.0),
        "bias_uncensored": float(est.sum() / true.sum() - 1.0),
        "bias_sales_last_28d": float(sales[last].sum() / true[last].sum() - 1.0),
        "bias_uncensored_last_28d": float(est[last].sum() / true[last].sum() - 1.0),
        "weekly_true": weekly(true).round().tolist(),
        "weekly_sales": weekly(sales).round().tolist(),
        "weekly_estimate": weekly(est).round(1).tolist(),
    }


def size_curve_report(panel: Panel, curves: SizeCurves, truth: Truth) -> dict:
    """Error of pooled and unpooled size curves against the true size mix, by sales volume."""
    active = panel.active_at(panel.n_days - 1)
    raw = np.where(np.isfinite(curves.raw), curves.raw, 0.25)
    n = curves.counts.sum(axis=-1)
    buckets = []
    for label, mask in (
        ("Under 20 units", n < 20),
        ("20 to 60 units", (n >= 20) & (n < 60)),
        ("Over 60 units", n >= 60),
        ("All pairs", np.ones_like(active)),
    ):
        m = active & mask
        if m.any():
            buckets.append(
                {
                    "bucket": label,
                    "pairs": int(m.sum()),
                    "raw_mae": float(np.abs(raw - truth.size_share)[m].mean()),
                    "pooled_mae": float(np.abs(curves.share - truth.size_share)[m].mean()),
                }
            )
    return {"buckets": buckets, "concentration": curves.concentration}


def trend_report(panel: Panel, trends: list[TrendSignal], truth: Truth, weeks: int = 8) -> list[dict]:
    """Detected style movement next to the true change in each style's latent trend factor."""
    t = panel.n_days
    rows = []
    for signal in trends:
        if signal.attribute != "style" or signal.value not in panel.styles:
            continue
        j = panel.styles.index(signal.value)
        true = float(truth.trend[t - 1, j] / truth.trend[t - 1 - weeks * 7, j] - 1.0)
        ahead = float(truth.trend[min(t + 27, truth.trend.shape[0] - 1), j] / truth.trend[t - 1, j] - 1.0)
        rows.append(
            {
                "style": signal.value,
                "status": signal.status,
                "detected_change_4w": signal.change_4w,
                "slope_pct_per_week": signal.slope_pct_per_week,
                "q_value": signal.q_value,
                "buzz_momentum": signal.buzz_momentum,
                "true_change_8w": true,
                "true_change_next_4w": ahead,
            }
        )
    rows.sort(key=lambda r: -r["true_change_next_4w"])
    return rows
