"""Feature engineering.

One row describes one question: "standing at the end of day ``origin``, how many units of this
product will this store sell over the window that starts ``start`` days ahead and lasts
``length`` days?"

Three rules shape the design.

* No leakage. Every history feature reads data up to and including the origin day and nothing
  later. Features about the target window come only from sources known in advance: the festival
  calendar, the planned discounts, and the date itself.
* Scale-free. The target is demand divided by the series' recent run-rate, and history features
  are ratios to that run-rate. One model can then learn from a best seller and a slow seller
  alike, and a product with three days of history borrows strength from the rest of the range.
* Pooled signals. A pair's own history is thin, so the row also carries how its product sells
  across all stores, how its category sells in its store, and where its trend attribute (style,
  by default) is heading: sales momentum and, when supplied, a leading buzz index.

Categories, sizes and product attributes come from the panel, so any catalogue works.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from luma.config import WINDOWS
from luma.data.panel import Panel

CHANNELS: tuple[str, ...] = ("festive", "party", "sale", "sports", "campus", "payday", "month_end")
ATTRIBUTE_PREFIX = "attr_"  # catalogue attributes enter the model as attr_<column>

# Feature families used to explain a forecast in plain language.
DRIVER_GROUPS: dict[str, tuple[str, ...]] = {
    "Recent sales level": ("log_level", "level_rel", "sku_pop", "avail14"),
    "Sales momentum": (
        "r7",
        "r14",
        "r56",
        "wk1",
        "wk2",
        "wk3",
        "wk4",
        "slope28",
        "cv28",
        "zero28",
        "sku_net_mom",
        "cat_store_mom",
        "store_mom",
    ),
    "Trend and buzz": ("style_mom", "style_mom_long", "buzz_mom", "buzz_mom_long"),
    "Festivals and events": tuple(
        f"{c}_{s}"
        for c in ("festive", "party", "sports", "campus", "payday", "month_end")
        for s in ("l7", "l28", "w", "delta")
    ),
    "Price and promotions": (
        "sale_l7",
        "sale_l28",
        "sale_w",
        "sale_delta",
        "disc_l7",
        "disc_l28",
        "disc_w",
        "disc_delta",
        "price",
        "price_rel",
    ),
    "Season": ("sin_doy", "cos_doy"),
    "Product lifecycle": ("age", "age_mid"),
    "Product attributes": ("category",),  # plus every attr_<column> feature
    "Store": ("store",),
    "Forecast window": ("w_start", "w_len"),
}


@dataclass
class FeatureFrame:
    X: pd.DataFrame
    origin: np.ndarray
    store: np.ndarray
    sku: np.ndarray
    window: np.ndarray  # index into WINDOWS
    length: np.ndarray  # window length in days
    level: np.ndarray  # recent run-rate in units per day
    y: np.ndarray | None  # demand over the window divided by (level * length)
    y_draw: np.ndarray | None = None  # same, from a realisation that keeps natural spread
    categorical: tuple[str, ...] = ("store", "category")  # integer-coded categorical columns

    def __len__(self) -> int:
        return len(self.origin)

    @property
    def scale(self) -> np.ndarray:
        """Units implied by a scaled value of 1."""
        return self.level * self.length

    @property
    def y_units(self) -> np.ndarray:
        if self.y is None:
            raise ValueError("frame has no target")
        return self.y * self.scale

    def subset(self, mask: np.ndarray) -> FeatureFrame:
        return FeatureFrame(
            X=self.X.loc[mask].reset_index(drop=True),
            origin=self.origin[mask],
            store=self.store[mask],
            sku=self.sku[mask],
            window=self.window[mask],
            length=self.length[mask],
            level=self.level[mask],
            y=None if self.y is None else self.y[mask],
            y_draw=None if self.y_draw is None else self.y_draw[mask],
            categorical=self.categorical,
        )


def _cum0(x: np.ndarray) -> np.ndarray:
    """Cumulative sum along time with a leading zero row, so ``c[b] - c[a]`` sums ``x[a:b]``."""
    return np.concatenate([np.zeros((1, *x.shape[1:])), np.cumsum(x, axis=0, dtype=np.float64)])


def _safe_div(a: np.ndarray, b: np.ndarray, fill: float = np.nan) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(b > 1e-9, a / np.where(b > 1e-9, b, 1.0), fill)


class FeatureBuilder:
    """Builds feature rows for any set of forecast origins from one panel."""

    def __init__(
        self, panel: Panel, demand: np.ndarray, prior_days: float = 7.0, demand_draw: np.ndarray | None = None
    ) -> None:
        """``demand`` is uncensored daily demand per store and product, shape ``[T, S, K]``.

        ``demand_draw`` is an optional realisation of the same quantity (see ``uncensor``); when
        given, frames also carry a ``y_draw`` target for the quantile models.
        """
        self.panel = panel
        self._cdraw = None if demand_draw is None else _cum0(demand_draw.astype(np.float64))
        self.prior_days = prior_days
        t_n = panel.n_days
        self.t_n = t_n
        u = demand.astype(np.float64)
        self._cu = _cum0(u)
        self._cu2 = _cum0(u * u)
        self._ctu = _cum0(u * np.arange(t_n)[:, None, None])
        self._cz = _cum0((panel.sales.sum(axis=-1) == 0).astype(np.float64))
        self._cav = _cum0(1.0 - panel.censored.mean(axis=-1))
        self._cb = _cum0(panel.buzz.astype(np.float64))
        cal = panel.calendar
        self._ccal = _cum0(cal[list(CHANNELS)].to_numpy(dtype=np.float64))
        self._doy = cal["doy"].to_numpy()
        self._cd = _cum0(panel.discount.astype(np.float64))

        cat = panel.category_code
        self._cat = cat
        n_cat = len(panel.categories)
        self._cat_onehot = np.eye(n_cat)[cat]  # [K, C]
        self._style = panel.style_code
        self._style_onehot = np.eye(len(panel.styles))[self._style]  # [K, J]
        catalog = panel.catalog
        self._codes = {
            "category": cat,
            **{f"{ATTRIBUTE_PREFIX}{a}": _stable_codes(catalog[a]) for a in panel.attributes},
        }
        self.categorical: tuple[str, ...] = ("store", *self._codes)
        price = panel.price
        med = np.array([np.median(price[cat == c]) if (cat == c).any() else 1.0 for c in range(n_cat)])
        self._price = price
        self._price_rel = price / med[cat]

    # ---- helpers ---------------------------------------------------------------------------
    def _last(self, cum: np.ndarray, origin: int, n: int) -> np.ndarray:
        return cum[origin + 1] - cum[max(origin + 1 - n, 0)]

    def labelled_origins(self, step: int, min_history: int, horizon: int = 28) -> list[int]:
        """Origins whose longest target window is fully observed, newest last."""
        last = self.t_n - 1 - horizon
        return list(range(last, min_history - 1, -step))[::-1]

    def run_rate(self, origin: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Shrunk recent demand per day for every pair: ``(level, prior_rate, age)``."""
        p = self.panel
        age = origin - p.start_day + 1
        active = age >= 1
        n28 = np.clip(age, 1, 28)
        s28 = self._last(self._cu, origin, 28)
        exposure = (n28 * active) @ self._cat_onehot  # [S, C]
        pooled = (s28 * active) @ self._cat_onehot
        city = pooled.sum(axis=0) / np.maximum(exposure.sum(axis=0), 1.0)
        prior = np.where(exposure > 0, pooled / np.maximum(exposure, 1.0), city[None, :])[:, self._cat]
        level = (s28 + self.prior_days * prior) / (n28 + self.prior_days)
        return np.maximum(level, 0.02), prior, age

    # ---- main ------------------------------------------------------------------------------
    def build(
        self,
        origins: Sequence[int],
        windows: Sequence[tuple[int, int]] = WINDOWS,
        with_target: bool = True,
    ) -> FeatureFrame:
        cols: dict[str, list[np.ndarray]] = {}
        meta: dict[str, list[np.ndarray]] = {
            k: [] for k in ("origin", "store", "sku", "window", "length", "level", "y", "y_draw")
        }

        def push(name: str, values: np.ndarray) -> None:
            cols.setdefault(name, []).append(values)

        for origin in origins:
            base, level, pairs = self._origin_features(origin)
            s_idx, k_idx = pairs
            n = len(s_idx)
            if n == 0:
                continue
            for start, length in windows:
                lo, hi = origin + start, origin + start + length
                if with_target and hi > self.t_n:
                    continue
                for name, values in base.items():
                    push(name, values)
                win = (self._ccal[hi] - self._ccal[lo]) / length
                for c, ch in enumerate(CHANNELS):
                    push(f"{ch}_w", np.full(n, win[c]))
                    push(f"{ch}_delta", win[c] - base[f"{ch}_l28"])
                disc_w = ((self._cd[hi] - self._cd[lo]) / length)[k_idx]
                push("disc_w", disc_w)
                push("disc_delta", disc_w - base["disc_l28"])
                angle = 2 * np.pi * self._doy[lo + length // 2] / 365.25
                push("sin_doy", np.full(n, np.sin(angle)))
                push("cos_doy", np.full(n, np.cos(angle)))
                push("w_start", np.full(n, float(start)))
                push("w_len", np.full(n, float(length)))
                push("age_mid", base["age"] + start + length / 2.0)
                meta["origin"].append(np.full(n, origin))
                meta["store"].append(s_idx)
                meta["sku"].append(k_idx)
                meta["window"].append(np.full(n, WINDOWS.index((start, length))))
                meta["length"].append(np.full(n, length))
                meta["level"].append(level)
                if with_target:
                    total = (self._cu[hi] - self._cu[lo])[s_idx, k_idx]
                    meta["y"].append(total / (level * length))
                    if self._cdraw is not None:
                        drawn = (self._cdraw[hi] - self._cdraw[lo])[s_idx, k_idx]
                        meta["y_draw"].append(drawn / (level * length))

        if not meta["origin"]:
            raise ValueError("no feature rows: no active pairs at the requested origins")
        frame = pd.DataFrame({name: np.concatenate(parts) for name, parts in cols.items()})
        for name in self.categorical:
            frame[name] = frame[name].astype(np.int32)
        return FeatureFrame(
            X=frame,
            origin=np.concatenate(meta["origin"]),
            store=np.concatenate(meta["store"]),
            sku=np.concatenate(meta["sku"]),
            window=np.concatenate(meta["window"]),
            length=np.concatenate(meta["length"]).astype(float),
            level=np.concatenate(meta["level"]),
            y=np.concatenate(meta["y"]) if with_target else None,
            y_draw=np.concatenate(meta["y_draw"]) if with_target and meta["y_draw"] else None,
            categorical=self.categorical,
        )

    def _origin_features(self, origin: int) -> tuple[dict[str, np.ndarray], np.ndarray, tuple[np.ndarray, np.ndarray]]:
        """Window-independent features of every active pair at one origin."""
        level, prior, age = self.run_rate(origin)
        active = age >= 1
        s_idx, k_idx = np.nonzero(active)
        last = self._last

        sums = {n: last(self._cu, origin, n) for n in (7, 14, 28, 56)}
        expo = {n: np.clip(age, 1, n) for n in (7, 14, 28, 56)}
        mean = {n: sums[n] / expo[n] for n in sums}
        f: dict[str, np.ndarray] = {}

        f["log_level"] = np.log(level)
        f["level_rel"] = _safe_div(level, prior)
        f["r7"] = mean[7] / level
        f["r14"] = mean[14] / level
        f["r56"] = np.where(age > 28, mean[56] / level, np.nan)
        for j in range(1, 5):
            hi_i = origin + 1 - 7 * (j - 1)
            lo_i = max(origin + 1 - 7 * j, 0)
            week = self._cu[hi_i] - self._cu[lo_i]
            f[f"wk{j}"] = np.where(age >= 7 * j, week / (7.0 * level), np.nan)
        var = last(self._cu2, origin, 28) / expo[28] - mean[28] ** 2
        f["cv28"] = np.sqrt(np.maximum(var, 0.0)) / (mean[28] + 0.05)
        f["zero28"] = last(self._cz, origin, 28) / expo[28]
        sxy = last(self._ctu, origin, 28) - (origin - 13.5) * sums[28]
        f["slope28"] = np.where(age >= 28, sxy / (28 * (28**2 - 1) / 12.0) / level, np.nan)
        f["avail14"] = last(self._cav, origin, 14) / expo[14]
        f["age"] = np.minimum(age, 500).astype(float)

        # Pooled momentum, normalised by exposure so openings and launches do not look like growth.
        def momentum(short: int, long: int, pool) -> np.ndarray:
            a = pool(sums[short] * active) / np.maximum(pool(expo[short] * active), 1e-9)
            b = pool(sums[long] * active) / np.maximum(pool(expo[long] * active), 1e-9)
            return _safe_div(a, b, 1.0)

        def by_cat(x: np.ndarray) -> np.ndarray:  # [S, C]
            return x @ self._cat_onehot

        def by_style(x: np.ndarray) -> np.ndarray:  # [J]
            return x.sum(axis=0) @ self._style_onehot

        f["cat_store_mom"] = momentum(7, 28, by_cat)[:, self._cat]
        f["store_mom"] = np.broadcast_to(momentum(7, 28, lambda x: x.sum(axis=1))[:, None], age.shape)
        f["sku_net_mom"] = np.broadcast_to(momentum(7, 28, lambda x: x.sum(axis=0))[None, :], age.shape)
        f["style_mom"] = np.broadcast_to(momentum(7, 28, by_style)[self._style][None, :], age.shape)
        f["style_mom_long"] = np.broadcast_to(momentum(14, 56, by_style)[self._style][None, :], age.shape)
        rel = np.where(active, _safe_div(level, prior), np.nan)
        seen = np.isfinite(rel)
        pop = _safe_div(np.where(seen, rel, 0.0).sum(axis=0), seen.sum(axis=0).astype(float))
        f["sku_pop"] = np.broadcast_to(pop[None, :], age.shape)

        buzz = {n: last(self._cb, origin, n) / min(n, origin + 1) for n in (7, 28, 56)}
        f["buzz_mom"] = np.broadcast_to((buzz[7] / np.maximum(buzz[28], 1e-9))[self._style][None, :], age.shape)
        f["buzz_mom_long"] = np.broadcast_to((buzz[7] / np.maximum(buzz[56], 1e-9))[self._style][None, :], age.shape)

        cal7 = last(self._ccal, origin, 7) / 7.0
        cal28 = last(self._ccal, origin, 28) / 28.0
        for c, ch in enumerate(CHANNELS):
            f[f"{ch}_l7"] = np.full(age.shape, cal7[c])
            f[f"{ch}_l28"] = np.full(age.shape, cal28[c])
        f["disc_l7"] = np.broadcast_to((last(self._cd, origin, 7) / 7.0)[None, :], age.shape)
        f["disc_l28"] = np.broadcast_to((last(self._cd, origin, 28) / 28.0)[None, :], age.shape)
        f["price"] = np.broadcast_to(self._price[None, :], age.shape)
        f["price_rel"] = np.broadcast_to(self._price_rel[None, :], age.shape)

        f["store"] = np.broadcast_to(np.arange(age.shape[0])[:, None], age.shape)
        for name, codes in self._codes.items():
            f[name] = np.broadcast_to(codes[None, :], age.shape)

        flat = {name: np.asarray(values)[s_idx, k_idx] for name, values in f.items()}
        return flat, level[s_idx, k_idx], (s_idx, k_idx)


def _stable_codes(values: pd.Series) -> np.ndarray:
    """Integer codes by sorted unique value, so codes agree between panels of the same range."""
    cats = sorted(values.astype(str).unique())
    return np.array([cats.index(v) for v in values.astype(str)], dtype=np.int64)


def feature_groups(columns: Sequence[str]) -> tuple[list[str], np.ndarray]:
    """Map feature columns to driver groups: ``(group names, group index per column)``."""
    names = list(DRIVER_GROUPS)
    lookup = {feat: g for g, feats in enumerate(DRIVER_GROUPS.values()) for feat in feats}
    attributes = names.index("Product attributes")
    missing = [c for c in columns if c not in lookup and not c.startswith(ATTRIBUTE_PREFIX)]
    if missing:
        raise KeyError(f"features without a driver group: {missing}")
    return names, np.array([lookup.get(c, attributes) for c in columns])
