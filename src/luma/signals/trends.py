"""Emerging trend detection.

A style is trending when the products that carry it take a growing share of demand. Three
things make a naive share chart misleading, and each is handled here.

* Seasonality. Outerwear grows every winter, so every outerwear-heavy style appears to trend.
  Shares are therefore measured inside each category and recombined with fixed category weights
  (a mix-adjusted share), which removes the seasonal category effect.
* Noise. With eight weekly points, a rising line is easy to imagine. The Mann-Kendall test asks
  whether the ordering of the points is more monotone than chance allows, and the Theil-Sen
  slope gives a growth rate that one odd week cannot bend.
* Many comparisons. Testing dozens of attributes guarantees false alarms, so p-values are
  adjusted with the Benjamini-Hochberg procedure to hold the false discovery rate at 10%.

Styles also have a social buzz index that leads sales by about ten days. A style whose buzz is
accelerating before its sales have moved is reported as "emerging": the early warning that
matters most for buying.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import erf, sqrt

import numpy as np

from luma.data.panel import Panel


@dataclass
class TrendSignal:
    attribute: str  # a catalogue attribute, such as "style", "color" or "fabric"
    value: str
    status: str  # Emerging, Rising, Peaking, Fading or Stable
    slope_pct_per_week: float  # Theil-Sen slope of the mix-adjusted share, % of its level
    change_4w: float  # last four weeks against the four before, as a ratio minus one
    p_value: float
    q_value: float
    share: float  # current mix-adjusted share of demand
    series: list[float]  # weekly mix-adjusted share, oldest first
    buzz_momentum: float | None  # last 7 days of buzz against the last 28, minus one
    buzz_series: list[float] | None
    leading_stores: list[str]  # where the movement is strongest
    top_skus: list[int]


def mann_kendall(x: np.ndarray) -> tuple[float, float]:
    """Mann-Kendall S statistic and two-sided p-value (normal approximation, tie-corrected)."""
    n = len(x)
    diff = np.sign(x[None, :] - x[:, None])
    s = float(np.triu(diff, 1).sum())
    _, counts = np.unique(x, return_counts=True)
    var = (n * (n - 1) * (2 * n + 5) - (counts * (counts - 1) * (2 * counts + 5)).sum()) / 18.0
    if var <= 0:
        return s, 1.0
    z = (s - np.sign(s)) / sqrt(var)
    return s, float(2.0 * (1.0 - 0.5 * (1.0 + erf(abs(z) / sqrt(2.0)))))


def theil_sen(x: np.ndarray) -> float:
    """Median of all pairwise slopes."""
    i, j = np.triu_indices(len(x), 1)
    return float(np.median((x[j] - x[i]) / (j - i)))


def benjamini_hochberg(p: np.ndarray) -> np.ndarray:
    """Benjamini-Hochberg adjusted p-values (q-values)."""
    n = len(p)
    order = np.argsort(p)
    ranked = p[order] * n / np.arange(1, n + 1)
    q = np.minimum.accumulate(ranked[::-1])[::-1]
    out = np.empty(n)
    out[order] = np.minimum(q, 1.0)
    return out


def _weekly(daily: np.ndarray, weeks: int) -> np.ndarray:
    """Sum the last ``weeks * 7`` days into weeks along axis 0."""
    tail = daily[-weeks * 7 :]
    return tail.reshape(weeks, 7, *tail.shape[1:]).sum(axis=1)


def mix_adjusted_share(weekly: np.ndarray, member: np.ndarray, cat: np.ndarray, test_weeks: int) -> np.ndarray:
    """Share of demand held by ``member`` products, free of category seasonality.

    ``weekly`` is ``[W, K]`` demand. Within-category shares are combined with category weights
    fixed over the test window.
    """
    n_cat = int(cat.max()) + 1
    onehot = np.eye(n_cat)[cat]  # [K, C]
    cat_total = weekly @ onehot  # [W, C]
    cat_member = (weekly * member[None, :]) @ onehot
    weights = cat_total[-test_weeks:].mean(axis=0)
    weights = weights / max(weights.sum(), 1e-9)
    share = np.where(cat_total > 0, cat_member / np.maximum(cat_total, 1e-9), 0.0)
    return share @ weights


def detect_trends(
    panel: Panel,
    demand: np.ndarray,
    *,
    test_weeks: int = 8,
    display_weeks: int = 20,
    fdr: float = 0.10,
) -> list[TrendSignal]:
    """Test every value of every catalogue attribute for a demand-share trend.

    ``demand`` is uncensored daily demand per store and product, ``[T, S, K]``.
    """
    display_weeks = min(display_weeks, panel.n_days // 7)
    test_weeks = min(test_weeks, display_weeks)
    weekly_store = _weekly(demand, display_weeks)  # [W, S, K]
    weekly = weekly_store.sum(axis=1)  # [W, K]
    cat = panel.category_code
    catalog = panel.catalog
    live = panel.launch_day <= panel.n_days - 1

    candidates: list[tuple[str, str, np.ndarray]] = []
    for attribute in panel.attributes:
        for value in sorted(catalog[attribute].astype(str).unique()):
            member = ((catalog[attribute].astype(str) == value).to_numpy() & live).astype(float)
            if member.sum() >= 2:
                candidates.append((attribute, value, member))

    rows = []
    for attribute, value, member in candidates:
        series = mix_adjusted_share(weekly, member, cat, test_weeks)
        window = series[-test_weeks:]
        level = max(float(window.mean()), 1e-9)
        _, p = mann_kendall(window)
        slope = theil_sen(window) / level
        half = test_weeks // 2
        change = float(window[-half:].mean() / max(window[:half].mean(), 1e-9) - 1.0)
        rows.append((attribute, value, member, series, p, slope, change))

    q = benjamini_hochberg(np.array([r[4] for r in rows])) if rows else np.array([])
    store_names = list(panel.stores["name"])
    signals: list[TrendSignal] = []
    for (attribute, value, member, series, p, slope, change), q_value in zip(rows, q, strict=True):
        buzz_mom = buzz_series = None
        if panel.has_buzz and attribute == panel.trend_attribute and value in panel.styles:
            b = panel.buzz[:, panel.styles.index(value)]
            buzz_mom = float(b[-7:].mean() / max(b[-28:].mean(), 1e-9) - 1.0)
            n = display_weeks * 7
            buzz_series = b[-n:].reshape(display_weeks, 7).mean(axis=1).round(2).tolist()

        significant = q_value <= fdr
        if significant and slope > 0:
            status = "Peaking" if (buzz_mom is not None and buzz_mom < -0.05) else "Rising"
        elif significant and slope < 0:
            status = "Fading"
        elif buzz_mom is not None and buzz_mom > 0.10 and change > 0:
            status = "Emerging"
        else:
            status = "Stable"

        # Where is it moving most? Compare each store's last four weeks with the four before.
        half = test_weeks // 2
        per_store = []
        for s in range(panel.n_stores):
            if panel.open_day[s] > panel.n_days - test_weeks * 7:
                continue
            share = mix_adjusted_share(weekly_store[:, s, :], member, cat, test_weeks)[-test_weeks:]
            per_store.append((float(share[-half:].mean() - share[:half].mean()), store_names[s]))
        per_store.sort(reverse=slope >= 0)
        recent = weekly[-half:].sum(axis=0) * member
        top = np.argsort(-recent)[:3]

        signals.append(
            TrendSignal(
                attribute=attribute,
                value=value,
                status=status,
                slope_pct_per_week=100.0 * slope,
                change_4w=change,
                p_value=float(p),
                q_value=float(q_value),
                share=float(series[-1]),
                series=series.round(5).tolist(),
                buzz_momentum=buzz_mom,
                buzz_series=buzz_series,
                leading_stores=[name for _, name in per_store[:3]],
                top_skus=[int(k) for k in top if recent[k] > 0],
            )
        )
    order = {"Emerging": 0, "Rising": 1, "Peaking": 2, "Fading": 3, "Stable": 4}
    signals.sort(key=lambda t: (order[t.status], -abs(t.slope_pct_per_week)))
    return signals
