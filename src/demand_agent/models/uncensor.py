"""Demand uncensoring.

Sales are not demand. When a size sells out, the till records what was on the shelf, not what
customers wanted, so a model trained on raw sales learns to under-forecast its best sellers.

Each cell-day that ended with zero stock is treated as right-censored: true demand is at least
the units sold. Its expected demand is the conditional mean of a Poisson variable given that
lower bound,

    E[D | D >= s] = mu * P(D >= s - 1) / P(D >= s),

where ``mu`` is a local selling rate estimated from the cell's own uncensored days and scaled
by the store's daily traffic profile. The rate and the imputed values are refined together for a
few expectation-maximisation rounds.

Two versions of the result are kept. ``demand`` fills censored cells with their conditional
mean, which is the right target for a model of expected demand. ``draw`` fills them with one
random draw from the same conditional distribution, which keeps the natural day-to-day spread.
Quantile models and calibration use ``draw``: averages are smoother than reality, and intervals
learned from averages come out too narrow.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import poisson

from demand_agent.data.panel import Panel


@dataclass
class Uncensored:
    demand: np.ndarray  # [T, S, K, Z] estimated true demand (conditional mean in censored cells)
    draw: np.ndarray  # [T, S, K, Z] one plausible realisation (random draw in censored cells)
    profile: np.ndarray  # [T, S] store traffic index
    censored_share: float  # share of active cell-days that were censored
    uplift: float  # estimated demand / observed sales - 1

    @property
    def by_sku(self) -> np.ndarray:
        """Estimated demand per store and product, shape ``[T, S, K]``."""
        return self.demand.sum(axis=-1)

    @property
    def draw_by_sku(self) -> np.ndarray:
        return self.draw.sum(axis=-1)


def centred_sum(x: np.ndarray, half: int) -> np.ndarray:
    """Moving sum over ``[t - half, t + half]`` along time, truncated at both ends of the data."""
    t_n = x.shape[0]
    cum = np.concatenate([np.zeros((1, *x.shape[1:])), np.cumsum(x, axis=0, dtype=np.float64)])
    idx = np.arange(t_n)
    return cum[np.minimum(idx + half + 1, t_n)] - cum[np.maximum(idx - half, 0)]


def _traffic_profile(total: np.ndarray, active_days: np.ndarray) -> np.ndarray:
    """Store demand on a day relative to its own 29-day local average, shape ``[T, S]``."""
    days = centred_sum(np.ones_like(total), 14)
    smooth = centred_sum(total, 14) / days
    profile = np.where(smooth > 1e-6, total / np.maximum(smooth, 1e-6), 1.0)
    profile = np.where(active_days, profile, 1.0)
    return np.clip(profile, 0.3, 3.0)


def conditional_mean_at_least(mu: np.ndarray, sold: np.ndarray) -> np.ndarray:
    """``E[D | D >= sold]`` for ``D ~ Poisson(mu)``, elementwise."""
    mu = np.maximum(mu, 1e-9)
    upper = poisson.sf(sold - 1, mu)  # P(D >= sold)
    lower = poisson.sf(sold - 2, mu)  # P(D >= sold - 1)
    ratio = np.where(upper > 1e-12, lower / np.maximum(upper, 1e-12), 0.0)
    return np.where(upper > 1e-12, mu * ratio, sold).astype(np.float32)


def draw_at_least(mu: np.ndarray, sold: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """One draw of ``D ~ Poisson(mu)`` conditioned on ``D >= sold``, by inverse CDF."""
    mu = np.maximum(mu, 1e-9)
    below = poisson.cdf(sold - 1, mu)
    u = below + rng.random(mu.shape) * (1.0 - below)
    drawn = poisson.ppf(np.minimum(u, 1.0 - 1e-12), mu)
    return np.maximum(drawn, sold).astype(np.float32)


def uncensor(
    panel: Panel, *, half_window: int = 28, prior_days: float = 14.0, rounds: int = 3, seed: int = 2024
) -> Uncensored:
    """Estimate true demand for every cell-day of the panel."""
    sales = panel.sales.astype(np.float32)
    censored = panel.censored
    t_n = panel.n_days
    active = (np.arange(t_n)[:, None, None] >= panel.start_day[None])[..., None]  # [T, S, K, 1]
    store_active = np.arange(t_n)[:, None] >= np.maximum(panel.open_day, 0)[None, :]
    cat = panel.category_code

    estimate = sales.copy()
    seen = (~censored) & active  # days whose sales equal demand
    profile = _traffic_profile(sales.sum(axis=(2, 3)), store_active)

    for round_no in range(rounds):
        f = profile[:, :, None, None]
        if round_no == 0:
            weight = seen.astype(np.float32)
            value = sales * weight
        else:
            weight = np.broadcast_to(active, sales.shape).astype(np.float32)
            value = estimate * weight
        num = centred_sum(value, half_window)
        den = centred_sum(weight * f, half_window)
        # Fallback rates for cells with little clean history near the day in question:
        # the cell's all-time rate, then the pooled rate of its store, category and size.
        all_num, all_den = value.sum(axis=0), (weight * f).sum(axis=0)
        pool = np.zeros_like(all_num)
        for c in np.unique(cat):
            m = cat == c
            pool[:, m, :] = (all_num[:, m, :].sum(axis=1) / np.maximum(all_den[:, m, :].sum(axis=1), 1.0))[:, None, :]
        cell = (all_num + prior_days * pool) / (all_den + prior_days)
        rate = (num + prior_days * cell[None]) / (den + prior_days)
        mu = rate * f
        imputed = conditional_mean_at_least(mu, panel.sales)
        estimate = np.where(censored, imputed, sales)
        profile = _traffic_profile(estimate.sum(axis=(2, 3)), store_active)

    rng = np.random.default_rng(seed + panel.n_days)
    draw = sales.copy()
    draw[censored] = draw_at_least(mu[censored], panel.sales[censored], rng)

    n_active = float(np.broadcast_to(active, sales.shape).sum())
    total_sales = float(sales.sum())
    return Uncensored(
        demand=estimate.astype(np.float32),
        draw=draw.astype(np.float32),
        profile=profile,
        censored_share=float(censored.sum()) / max(n_active, 1.0),
        uplift=float(estimate.sum()) / max(total_sales, 1.0) - 1.0,
    )
