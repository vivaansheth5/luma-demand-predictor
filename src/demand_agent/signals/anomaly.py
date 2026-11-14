"""Anomaly detection with attribution.

An anomaly is a day whose demand cannot be explained by the series' own recent level and
weekday pattern. Three levels are monitored, each relative to the level above it, so that one
cause is reported once and at the level where it happened:

* the city total, which moves with festivals, sale events and weather;
* each store relative to the city, which isolates store problems such as an outage;
* each product relative to its category, which isolates product events such as a viral post.

The expectation for a day uses only earlier days (a trailing median, so one spike does not drag
the baseline), which means the detector works in real time. Surprise is measured on the Anscombe
scale, where count noise has constant variance, and normalised by the series' own robust spread.

A flag alone is noise to an operator, so every anomaly is checked against what the system
already knows: the festival and sale calendar, the discount plan, and the weather. Whatever
survives is labelled unexplained, and that is the short list a person should look at.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from demand_agent.data.panel import Panel

HEAVY_RAIN_MM = 30.0


@dataclass
class Anomaly:
    scope: str  # "city", "store" or "product"
    entity: int  # store index or product index
    name: str
    start: int  # day index
    end: int
    direction: str  # "spike" or "drop"
    z: float  # peak robust z-score
    actual: float  # units over the anomalous days
    expected: float
    explained_by: str | None  # known cause, or None when unexplained

    @property
    def ratio(self) -> float:
        return self.actual / max(self.expected, 1e-9)


def _anscombe(x: np.ndarray) -> np.ndarray:
    return 2.0 * np.sqrt(np.maximum(x, 0.0) + 0.375)


def expected_series(y: np.ndarray, window: int = 28) -> np.ndarray:
    """Causal expectation for each day of ``y`` (``[T, N]``): trailing median times weekday factor."""
    t_n = y.shape[0]
    expected = np.full(y.shape, np.nan)
    dow = np.arange(t_n) % 7
    for t in range(window, t_n):
        past = y[t - window : t]
        base = np.median(past, axis=0)
        same = past[dow[t - window : t] == dow[t]]
        factor = np.where(base > 0, np.median(same, axis=0) / np.maximum(base, 1e-9), 1.0)
        expected[t] = base * np.clip(factor, 0.5, 2.0)
    return expected


def robust_z(y: np.ndarray, expected: np.ndarray, window: int = 56) -> np.ndarray:
    """Surprise of each day, normalised by the series' trailing robust spread."""
    raw = _anscombe(y) - _anscombe(expected)
    z = np.full(y.shape, np.nan)
    with np.errstate(invalid="ignore", all="ignore"):
        for t in range(1, y.shape[0]):
            past = raw[max(t - window, 0) : t]
            enough = np.isfinite(past).sum(axis=0) >= 14
            if not enough.any():
                continue
            centre = np.nanmedian(past[:, enough], axis=0)
            mad = np.nanmedian(np.abs(past[:, enough] - centre), axis=0)
            z[t, enough] = raw[t, enough] / np.maximum(1.4826 * mad, 1.0)
    return z


def _events(z: np.ndarray, threshold: float) -> list[tuple[int, int, int, float]]:
    """Merge consecutive flagged days of each series into ``(entity, start, end, peak z)``."""
    out = []
    flagged = np.where(np.isfinite(z), np.sign(z) * (np.abs(z) > threshold), 0.0)
    for n in range(z.shape[1]):
        col = flagged[:, n]
        t = 0
        while t < len(col):
            if col[t] != 0:
                sign, start = col[t], t
                while t + 1 < len(col) and col[t + 1] == sign:
                    t += 1
                seg = z[start : t + 1, n]
                out.append((n, start, t, float(seg[np.argmax(np.abs(seg))])))
            t += 1
    return out


def _explain(panel: Panel, scope: str, entity: int, start: int, end: int, direction: str) -> str | None:
    """A known cause for the anomaly, or None."""
    if scope == "product":
        now = float(panel.discount[start : end + 1, entity].max())
        before = float(panel.discount[max(start - 3, 0) : start, entity].max(initial=0.0))
        if direction == "spike" and now > before + 0.05:
            return f"Discount of {now:.0%} started"
        if direction == "drop" and before > now + 0.05:
            return "Discount ended"
        if start - panel.launch_day[entity] < 21:
            return "Launch weeks"
        return None
    if scope == "store":
        return None  # city-wide causes are already removed; what is left is specific to the store
    weather = panel.weather.iloc[start : end + 1]
    if direction == "drop" and len(weather) and float(weather["rain_mm"].max()) >= HEAVY_RAIN_MM:
        return f"Heavy rain ({float(weather['rain_mm'].max()):.0f} mm)"
    cal = panel.calendar.iloc[start : end + 1]
    events = [e for e in cal["event"] if e]
    if events:
        name = max(set(events), key=events.count)
        after = name.startswith("After ")
        if (direction == "spike") != after:
            return name
    if (
        direction == "drop"
        and float(panel.calendar["payday"].iloc[start]) == 0
        and float(panel.calendar["month_end"].iloc[start]) == 1
    ):
        return "Month end"
    return None


def _trailing_sum(x: np.ndarray, span: int) -> np.ndarray:
    """Sum of each day and the ``span - 1`` days before it (NaN until enough valid days exist)."""
    if span == 1:
        return x
    missing = ~np.isfinite(x)
    zero = np.zeros((1, *x.shape[1:]))
    cum = np.concatenate([zero, np.cumsum(np.where(missing, 0.0, x), axis=0)])
    gaps = np.concatenate([zero, np.cumsum(missing, axis=0)])
    out = np.full(x.shape, np.nan)
    out[span - 1 :] = np.where(gaps[span:] - gaps[:-span] > 0, np.nan, cum[span:] - cum[:-span])
    return out


def _relative(expected: np.ndarray, parent_actual: np.ndarray, parent_expected: np.ndarray) -> np.ndarray:
    """Scale each series' expectation by its parent's surprise on the same day."""
    with np.errstate(invalid="ignore", divide="ignore"):
        factor = np.where(parent_expected > 0, parent_actual / parent_expected, 1.0)
    return expected * np.clip(np.nan_to_num(factor, nan=1.0), 0.3, 4.0)


def detect_anomalies(
    panel: Panel, demand: np.ndarray, *, threshold: float = 4.0, min_units: float = 4.0, min_ratio: float = 1.6
) -> list[Anomaly]:
    """Find anomalous days at city, store and product level; newest first.

    ``demand`` is uncensored daily demand per store and product, ``[T, S, K]``.
    """
    t_n = demand.shape[0]
    cat = panel.category_code
    n_cat = int(cat.max()) + 1
    city = demand.sum(axis=(1, 2))[:, None]  # [T, 1]
    stores = demand.sum(axis=2)  # [T, S]
    products = demand.sum(axis=1)  # [T, K]
    category = np.stack([products[:, cat == c].sum(axis=1) for c in range(n_cat)], axis=1)  # [T, C]

    city_exp = expected_series(city)
    cat_exp = expected_series(category)
    store_exp = _relative(expected_series(stores), city, city_exp)
    product_exp = _relative(expected_series(products), category[:, cat], cat_exp[:, cat])

    # A product sells a few units a day across the city, so single days are too noisy to judge.
    # Products are tested on three-day totals; a spike must persist to be flagged.
    views = (
        ("city", city, city_exp, ["All stores"], np.zeros(1, dtype=np.int64), 1),
        ("store", stores, store_exp, list(panel.stores["name"]), np.maximum(panel.open_day, 0), 1),
        ("product", products, product_exp, list(panel.catalog["name"]), np.maximum(panel.launch_day, 0), 3),
    )
    found: list[Anomaly] = []
    for scope, series, expected, names, first_day, span in views:
        z = robust_z(_trailing_sum(series, span), _trailing_sum(expected, span))
        # A series needs six clean weeks before its own baseline is trustworthy.
        young = np.arange(t_n)[:, None] < (first_day[None, :] + 42)
        z = np.where(young, np.nan, z)
        for entity, start, end, peak in _events(z, threshold):
            start = max(start - span + 1, 0)
            actual = float(series[start : end + 1, entity].sum())
            exp = float(expected[start : end + 1, entity].sum())
            direction = "spike" if peak > 0 else "drop"
            ratio = actual / max(exp, 1e-9)
            if max(actual, exp) < min_units:
                continue
            if scope != "city" and (1.0 / min_ratio) < ratio < min_ratio:
                continue  # statistically odd but too small to act on
            found.append(
                Anomaly(
                    scope=scope,
                    entity=int(entity),
                    name=names[entity],
                    start=int(start),
                    end=int(end),
                    direction=direction,
                    z=peak,
                    actual=actual,
                    expected=exp,
                    explained_by=_explain(panel, scope, int(entity), int(start), int(end), direction),
                )
            )
    found.sort(key=lambda a: (-a.end, -abs(a.z)))
    return found


def score_detection(anomalies: list[Anomaly], truth: list[dict], n_days: int, warmup: int = 84) -> dict:
    """Recall and precision of unexplained anomalies against the simulator's injected shocks."""
    truth = [e for e in truth if warmup <= e["start"] < n_days]
    kinds = {"viral_spike": ("product", "sku", "spike"), "outage": ("store", "store", "drop")}
    out: dict = {}
    for kind, (scope, key, direction) in kinds.items():
        events = [e for e in truth if e["type"] == kind]
        flagged = [a for a in anomalies if a.scope == scope and a.direction == direction and a.start >= warmup]
        hit = sum(
            any(a.entity == e[key] and a.start <= e["end"] + 1 and a.end >= e["start"] for a in flagged) for e in events
        )
        unexplained = [a for a in flagged if a.explained_by is None]
        true_pos = sum(
            any(a.entity == e[key] and a.start <= e["end"] + 1 and a.end >= e["start"] for e in events)
            for a in unexplained
        )
        out[kind] = {
            "injected": len(events),
            "detected": hit,
            "recall": hit / len(events) if events else None,
            "unexplained_flags": len(unexplained),
            "precision": true_pos / len(unexplained) if unexplained else None,
        }
    return out
