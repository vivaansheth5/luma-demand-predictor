"""Size curves by empirical Bayes.

A product in one store sells a handful of units a week, so its raw size mix is mostly noise,
and it is biased as well: a size that sold out looks unpopular. Both problems are handled here.

* Bias: shares are counted only on days when every size was still on the shelf.
* Noise: each (store, product) curve is a Dirichlet-multinomial posterior. Its prior combines
  the product's city-wide curve with the store's curve for the category, so a new store or a new
  product starts from a sensible shape and moves towards its own data as evidence accumulates.
  The prior strength is fitted by maximum likelihood, not chosen by hand.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize_scalar
from scipy.special import gammaln

from demand_agent.data.panel import Panel


@dataclass
class SizeCurves:
    share: np.ndarray  # [S, K, Z] posterior mean size mix
    raw: np.ndarray  # [S, K, Z] unpooled share from clean days (NaN when no sales)
    counts: np.ndarray  # [S, K, Z] clean-day unit counts
    concentration: dict[str, float]  # fitted prior strength per pooling level


def _dm_negloglik(log_kappa: float, counts: np.ndarray, prior: np.ndarray) -> float:
    """Negative Dirichlet-multinomial log-likelihood of ``counts`` given ``kappa * prior``."""
    kappa = np.exp(log_kappa)
    alpha = kappa * prior
    n = counts.sum(axis=-1)
    ll = gammaln(kappa) - gammaln(kappa + n) + (gammaln(alpha + counts) - gammaln(alpha)).sum(axis=-1)
    return -float(ll.sum())


def fit_concentration(counts: np.ndarray, prior: np.ndarray) -> float:
    """Maximum-likelihood prior strength for rows of ``counts`` around their ``prior`` means."""
    keep = counts.sum(axis=-1) > 0
    if keep.sum() < 3:
        return 50.0
    res = minimize_scalar(
        _dm_negloglik, bounds=(np.log(2.0), np.log(5000.0)), args=(counts[keep], prior[keep]), method="bounded"
    )
    return float(np.exp(res.x))


def _normalise(x: np.ndarray) -> np.ndarray:
    return x / np.maximum(x.sum(axis=-1, keepdims=True), 1e-12)


def fit_size_curves(panel: Panel, lookback_days: int = 240) -> SizeCurves:
    """Posterior mean size mix for every (store, product) pair."""
    lo = max(panel.n_days - lookback_days, 0)
    sales = panel.sales[lo:].astype(float)
    # A day is clean for a (store, product) when no size ended the day sold out.
    clean = ~panel.censored[lo:].any(axis=-1, keepdims=True)
    counts = (sales * clean).sum(axis=0)  # [S, K, Z]
    cat = panel.category_code
    n_cat = int(cat.max()) + 1

    # Level 1: city-wide curve per category.
    cat_counts = np.stack([counts[:, cat == c, :].sum(axis=(0, 1)) for c in range(n_cat)])
    cat_share = _normalise(cat_counts + 1.0)

    # Level 2a: product curve across the city, shrunk to its category.
    sku_counts = counts.sum(axis=0)  # [K, Z]
    k_sku = fit_concentration(sku_counts, cat_share[cat])
    sku_share = _normalise(sku_counts + k_sku * cat_share[cat])

    # Level 2b: store curve per category, shrunk to the city.
    sc_counts = np.stack([counts[:, cat == c, :].sum(axis=1) for c in range(n_cat)], axis=1)  # [S, C, Z]
    prior_sc = np.broadcast_to(cat_share[None], sc_counts.shape)
    k_store = fit_concentration(sc_counts.reshape(-1, sc_counts.shape[-1]), prior_sc.reshape(-1, sc_counts.shape[-1]))
    sc_share = _normalise(sc_counts + k_store * prior_sc)

    # Level 3: the pair's prior multiplies both parents and divides out their shared ancestor.
    prior = _normalise(sku_share[None, :, :] * sc_share[:, cat, :] / cat_share[cat][None])
    k_pair = fit_concentration(counts.reshape(-1, counts.shape[-1]), prior.reshape(-1, counts.shape[-1]))
    share = _normalise(counts + k_pair * prior)

    with np.errstate(invalid="ignore", divide="ignore"):
        raw = counts / counts.sum(axis=-1, keepdims=True)
    return SizeCurves(
        share=share,
        raw=raw,
        counts=counts,
        concentration={"product": k_sku, "store_category": k_store, "pair": k_pair},
    )
