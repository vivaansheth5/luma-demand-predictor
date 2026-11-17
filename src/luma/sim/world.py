"""The demo world: a synthetic fashion retailer in Bangalore.

Luma ships with a simulated network so it can be tried, and tested, before any data is
connected. The simulator has the structure a multi-store fashion retailer has: staggered store openings, product
drops with lifecycles, festival and sale calendars, weather, style trends that social buzz
leads by about ten days, neighbourhoods that adopt trends at different speeds, and stockouts
that hide true demand.

The simulator keeps the true demand. The agent never sees it; it is used only to score the
agent, which is the one thing real data can never offer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from luma.config import CATEGORIES, CATEGORY_LABELS, SIZES, SimSettings
from luma.data.panel import Panel, save_json
from luma.inventory.baseline import BaselinePolicy
from luma.inventory.engine import InventoryState, RunLog, run_inventory
from luma.sim.calendar import build_calendar, build_weather, discount_plan
from luma.sim.catalog import (
    COLORS,
    STORES,
    STYLES,
    build_catalog,
    category_size_curves,
    stores_frame,
)

# Category response to each calendar channel, in CATEGORIES order.
_FESTIVE_AFF = np.array([0.90, 1.00, 0.60, 1.00, 1.25])
_PARTY_AFF = np.array([0.20, 0.50, 0.00, 0.40, 1.40])
_SPORTS_AFF = np.array([0.12, 0.05, 1.00, 0.05, 0.00])
_CAMPUS_AFF = np.array([0.15, 0.06, 0.08, 0.15, 0.10])
# Annual seasonality per category: (amplitude, peak day of year).
_SEASON = np.array([[0.16, 120], [0.42, 5], [0.0, 0], [0.08, 15], [0.10, 130]])
_COLOR_SEASON = {"pastel": (0.15, 110), "earth": (0.15, 300), "maroon": (0.12, 10)}


@dataclass
class Truth:
    """Simulator ground truth. Used for evaluation only."""

    demand: np.ndarray  # [T + H, S, K, Z] true demand, including demand lost to stockouts
    lam: np.ndarray  # [T + H, S, K] expected demand
    size_share: np.ndarray  # [S, K, Z]
    trend: np.ndarray  # [T + H, J] latent style trend multiplier
    buzz: np.ndarray  # [T + H, J]
    weather: pd.DataFrame  # [T + H]
    anomalies: list[dict]
    history_days: int

    @property
    def future_demand(self) -> np.ndarray:
        return self.demand[self.history_days :]

    def save(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            folder / "truth.npz",
            demand=self.demand.astype(np.int16),
            lam=self.lam.astype(np.float32),
            size_share=self.size_share.astype(np.float32),
            trend=self.trend.astype(np.float32),
            buzz=self.buzz.astype(np.float32),
            history_days=np.array(self.history_days),
        )
        self.weather.to_csv(folder / "truth_weather.csv", index=False)
        save_json(folder / "truth_anomalies.json", self.anomalies)

    @classmethod
    def load(cls, folder: Path) -> Truth:
        import json

        z = np.load(folder / "truth.npz")
        return cls(
            demand=z["demand"].astype(np.int32),
            lam=z["lam"].astype(float),
            size_share=z["size_share"].astype(float),
            trend=z["trend"].astype(float),
            buzz=z["buzz"].astype(float),
            weather=pd.read_csv(folder / "truth_weather.csv", parse_dates=["date"])
            if (folder / "truth_weather.csv").exists()
            else pd.read_parquet(folder / "truth_weather.parquet"),
            anomalies=json.loads((folder / "truth_anomalies.json").read_text()),
            history_days=int(z["history_days"]),
        )


@dataclass
class World:
    settings: SimSettings
    panel: Panel
    truth: Truth

    def save(self, folder: Path, fmt: str | None = None) -> None:
        self.panel.save(folder, fmt)
        self.truth.save(folder / "truth")


def _logistic(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def _style_trends(start: date, n_days: int, rng: np.random.Generator) -> np.ndarray:
    """Latent trend multiplier per style, shape ``[n_days, n_styles]``.

    Each style follows a slow mean-reverting random walk. A few scripted movements are layered
    on top so the world contains trends at every stage: one that peaked and faded, one in a
    steady climb, one in decline, and one that is only just starting.
    """
    ordinal = np.array([(start + timedelta(days=i)).toordinal() for i in range(n_days)], dtype=float)
    doy = np.array([(start + timedelta(days=i)).timetuple().tm_yday for i in range(n_days)])
    log_g = np.zeros((n_days, len(STYLES)))
    shocks = rng.normal(0.0, 0.012, (n_days, len(STYLES)))
    for i in range(1, n_days):
        log_g[i] = 0.995 * log_g[i - 1] + shocks[i]

    def at(day: date) -> float:
        return float(day.toordinal())

    def bump(center: date, width: float, amp: float) -> np.ndarray:
        return amp * np.exp(-0.5 * ((ordinal - at(center)) / width) ** 2)

    def climb(mid: date, scale: float, amp: float) -> np.ndarray:
        return amp * _logistic((ordinal - at(mid)) / scale)

    script = {
        "blokecore": bump(date(2026, 6, 28), 24, 0.55),
        "retro-sport": bump(date(2026, 5, 10), 30, 0.25),
        "gorpcore": climb(date(2026, 10, 28), 16, 0.60),
        "y2k": climb(date(2026, 9, 25), 20, -0.50),
        "coquette": climb(date(2026, 11, 22), 8, 0.75),
        "grunge": 0.12 * np.cos(2 * np.pi * (doy - 10) / 365.0),
    }
    for name, path in script.items():
        log_g[:, STYLES.index(name)] += path
    return np.exp(log_g)


def _anomalies(
    settings: SimSettings, launch_day: np.ndarray, open_day: np.ndarray, rng: np.random.Generator
) -> tuple[np.ndarray, list[dict]]:
    """Unpredictable shocks: viral product spikes and one-day store outages."""
    t_hist, t_all = settings.history_days, settings.total_days
    n_stores, n_skus = len(open_day), len(launch_day)
    mult = np.ones((t_all, n_stores, n_skus), dtype=np.float32)
    events: list[dict] = []

    def spike(day: int, sku: int | None = None) -> None:
        live = np.nonzero(launch_day <= day - 14)[0]
        if len(live) == 0:
            return
        k = int(rng.choice(live)) if sku is None else sku
        length = int(rng.integers(2, 5))
        m = float(rng.uniform(3.0, 5.0))
        end = min(day + length, t_all)
        mult[day:end, :, k] *= m
        events.append({"type": "viral_spike", "sku": k, "start": day, "end": end - 1, "multiplier": round(m, 2)})

    def outage(day: int) -> None:
        live = np.nonzero(open_day <= day - 14)[0]
        if len(live) == 0:
            return
        s = int(rng.choice(live))
        m = float(rng.uniform(0.25, 0.40))
        mult[day, s, :] *= m
        events.append({"type": "outage", "store": s, "start": day, "end": day, "multiplier": round(m, 2)})

    for day in rng.integers(50, t_hist - 25, size=max(t_hist // 40, 2)):
        spike(int(day))
    for day in rng.integers(50, t_hist - 25, size=max(t_hist // 70, 2)):
        outage(int(day))
    spike(t_hist - 10)  # one recent spike so the latest weeks contain a live example
    outage(t_hist - 5)
    spike(t_hist + 9)  # one shock inside the hidden future: nobody can forecast it
    events.sort(key=lambda e: e["start"])
    return mult, events


def expected_demand(
    settings: SimSettings,
    stores: pd.DataFrame,
    catalog: pd.DataFrame,
    calendar: pd.DataFrame,
    weather: pd.DataFrame,
    discount: np.ndarray,
    trend: np.ndarray,
    anomaly: np.ndarray,
    rng: np.random.Generator,
) -> np.ndarray:
    """Expected daily demand per store and product, shape ``[T + H, S, K]``."""
    t_all = settings.total_days
    n_stores, n_skus = len(stores), len(catalog)
    specs = STORES[:n_stores]
    t = np.arange(t_all)
    cat = np.array([list(CATEGORIES).index(c) for c in catalog["category"].astype(str)])
    style = np.array([STYLES.index(s) for s in catalog["style"]])
    doy = calendar["doy"].to_numpy()[:t_all]
    dow = calendar["dow"].to_numpy()[:t_all]

    # Store-by-day factors.
    open_day = stores["open_day"].to_numpy()
    age_store = t[:, None] - open_day[None, :]
    ramp = np.where(age_store >= 0, 1.0 - 0.75 * np.exp(-np.maximum(age_store, 0) / 40.0), 0.0)
    growth = np.exp(0.0006 * t)[:, None]
    weekday = np.zeros((7, n_stores))
    base_week = np.array([0.90, 0.88, 0.92, 0.97, 1.08, 1.00, 1.00])
    for s, spec in enumerate(specs):
        w = base_week.copy()
        w[5] *= spec.weekend_lift
        w[6] *= spec.weekend_lift * 0.94
        weekday[:, s] = w / w.mean()
    rain = weather["rain_mm"].to_numpy()
    rain_mult = np.where(rain >= 30, 0.78, np.where(rain >= 12, 0.94, 1.0))[:, None]
    pay = 1.0 + 0.10 * calendar["payday"].to_numpy()[:t_all] - 0.05 * calendar["month_end"].to_numpy()[:t_all]
    shock = rng.lognormal(0.0, 0.07, (t_all, n_stores))
    store_part = (
        np.array([sp.scale for sp in specs])[None, :] * ramp * growth * weekday[dow] * rain_mult * pay[:, None] * shock
    )

    # Product-by-day factors.
    age = t[:, None] - catalog["launch_day"].to_numpy()[None, :]
    live = age >= 0
    age_pos = np.maximum(age, 0)
    lifecycle = (1.0 - np.exp(-(age_pos + 1) / 10.0)) * 0.5 ** (
        np.maximum(age_pos - catalog["_plateau"].to_numpy()[None, :], 0) / catalog["_half_life"].to_numpy()[None, :]
    )
    amp, peak = _SEASON[cat, 0], _SEASON[cat, 1]
    season = 1.0 + amp[None, :] * np.cos(2 * np.pi * (doy[:, None] - peak[None, :]) / 365.0)
    color_season = np.ones((t_all, n_skus))
    for color, (c_amp, c_peak) in _COLOR_SEASON.items():
        mask = (catalog["color"] == color).to_numpy()
        color_season[:, mask] = (1.0 + c_amp * np.cos(2 * np.pi * (doy - c_peak) / 365.0))[:, None]
    cal = {c: calendar[c].to_numpy()[:t_all][:, None] for c in ("festive", "party", "sale", "sports", "campus")}
    events = (
        (1.0 + cal["festive"] * _FESTIVE_AFF[cat][None, :])
        * (1.0 + cal["party"] * _PARTY_AFF[cat][None, :])
        * (1.0 + cal["sports"] * _SPORTS_AFF[cat][None, :])
        * (1.0 + cal["campus"] * _CAMPUS_AFF[cat][None, :])
        * (1.0 + cal["sale"])
    )
    anom_t = weather["tmax"].to_numpy() - weather["tmax_normal"].to_numpy()
    cold = 1.0 + 0.05 * np.clip(-anom_t, 0.0, 3.0)
    weather_cat = np.where((cat == list(CATEGORIES).index("outerwear"))[None, :], cold[:, None], 1.0)
    sku_part = catalog["_base"].to_numpy()[None, :] * lifecycle * live * season * color_season * events * weather_cat

    # Store-by-product interactions.
    affinity = np.array([sp.affinity for sp in specs])[:, cat]  # [S, K]
    lam = store_part[:, :, None] * sku_part[:, None, :] * affinity[None]
    lead = trend.shape[0] - t_all
    for s, spec in enumerate(specs):
        shifted = np.clip(t - spec.trend_delay, 0, t_all + lead - 1)
        lam[:, s, :] *= trend[shifted][:, style]
        elasticity = catalog["_elasticity"].to_numpy()[None, :] * spec.price_sensitivity
        lam[:, s, :] *= (1.0 - discount[:t_all]) ** (-elasticity)
    return lam * anomaly


def _size_shares(stores: pd.DataFrame, catalog: pd.DataFrame, rng: np.random.Generator) -> np.ndarray:
    """True size mix per store and product, shape ``[S, K, Z]``."""
    cat = np.array([list(CATEGORIES).index(c) for c in catalog["category"].astype(str)])
    base = category_size_curves()[cat]  # [K, Z]
    z = np.arange(len(SIZES)) - (len(SIZES) - 1) / 2.0
    store_shift = np.array([sp.size_shift for sp in STORES[: len(stores)]])
    logits = (
        np.log(base)[None, :, :]
        + (store_shift[:, None, None] + catalog["_fit_shift"].to_numpy()[None, :, None]) * z[None, None, :]
    )
    mean = np.exp(logits)
    mean /= mean.sum(axis=-1, keepdims=True)
    noisy = rng.gamma(mean * 60.0, 1.0)
    return noisy / noisy.sum(axis=-1, keepdims=True)


def _draw_demand(lam: np.ndarray, share: np.ndarray, dispersion: float, rng: np.random.Generator) -> np.ndarray:
    """Negative-binomial demand split into sizes, shape ``[T, S, K, Z]``."""
    mix = rng.gamma(dispersion, 1.0 / dispersion, lam.shape)
    total = rng.poisson(lam * mix)
    out = np.zeros((*lam.shape, share.shape[-1]), dtype=np.int32)
    remaining = total.copy()
    left = np.ones(share.shape[:-1])
    for z in range(share.shape[-1] - 1):
        p = np.clip(share[..., z] / np.maximum(left, 1e-12), 0.0, 1.0)
        take = rng.binomial(remaining, np.broadcast_to(p, remaining.shape))
        out[..., z] = take
        remaining -= take
        left = left - share[..., z]
    out[..., -1] = remaining
    return out


def generate_world(settings: SimSettings | None = None) -> World:
    """Generate the world: true demand, then the sales history a status-quo policy produces."""
    settings = settings or SimSettings()
    rng_cat, rng_cal, rng_trend, rng_anom, rng_lam, rng_dem, rng_buzz = np.random.default_rng(settings.seed).spawn(7)
    t_hist, t_all = settings.history_days, settings.total_days

    stores = stores_frame(settings)
    catalog = build_catalog(settings, rng_cat)
    n_stores, n_skus, n_sizes = len(stores), len(catalog), len(SIZES)
    plan_days = t_all + settings.future_days + 7  # room for re-forecasts made inside the hidden future
    calendar = build_calendar(settings.start, plan_days)
    weather = build_weather(settings.start, t_all, rng_cal)
    discount = discount_plan(
        settings.start,
        plan_days,
        catalog["launch_day"].to_numpy(),
        catalog["_markdown_day"].to_numpy(),
        catalog["_markdown_depth"].to_numpy(),
        rng_cal,
    )
    lead = settings.buzz_lead_days
    trend = _style_trends(settings.start, t_all + lead, rng_trend)
    anomaly, events = _anomalies(settings, catalog["launch_day"].to_numpy(), stores["open_day"].to_numpy(), rng_anom)
    lam = expected_demand(settings, stores, catalog, calendar, weather, discount, trend, anomaly, rng_lam)
    share = _size_shares(stores, catalog, rng_dem)
    demand = _draw_demand(lam, share, settings.nb_dispersion, rng_dem)

    # Social buzz: a noisy, smoothed reading of where each trend will be ``lead`` days from now.
    raw = 50.0 * trend[lead : lead + t_all] ** 1.3 * rng_buzz.lognormal(0.0, 0.07, (t_all, len(STYLES)))
    kernel = np.ones(3) / 3.0
    buzz = np.stack(
        [np.convolve(np.pad(raw[:, j], (2, 0), mode="edge"), kernel, mode="valid") for j in range(len(STYLES))], axis=1
    )

    # Opening allocations arrive the day a product first goes live in a store.
    start_day = np.maximum(
        np.maximum(stores["open_day"].to_numpy()[:, None], catalog["launch_day"].to_numpy()[None, :]), 0
    )
    receipts = np.zeros((t_all, n_stores, n_skus, n_sizes), dtype=np.int32)
    s_idx, k_idx = np.nonzero(start_day < t_all)
    receipts[start_day[s_idx, k_idx], s_idx, k_idx, :] = np.array(settings.initial_pack, dtype=np.int32)

    def active(day: int) -> np.ndarray:
        return start_day <= day

    state0 = InventoryState(
        on_hand=np.zeros((n_stores, n_skus, n_sizes), dtype=np.int32),
        pipeline=np.zeros((settings.lead_time_days, n_stores, n_skus, n_sizes), dtype=np.int32),
    )
    first_weekday = settings.start.weekday()
    policy = BaselinePolicy(cover_days=settings.baseline_cover_days, active=active)
    log, state = run_inventory(demand[:t_hist], state0, policy, first_weekday=first_weekday, receipts=receipts[:t_hist])
    # The panel shows the position before the as-of evening's decision, so that every policy
    # (status quo or agent) makes that decision itself in the evaluation.
    if log.decisions and log.decisions[-1][0] == t_hist - 1:
        state.pipeline[-1] -= log.decisions[-1][1].orders

    public = [c for c in catalog.columns if not c.startswith("_")]
    catalog_public = catalog[public].copy()
    catalog_public["category"] = catalog_public["category"].astype(str)
    panel = Panel(
        start=settings.start,
        sales=log.sales,
        sod=log.sod,
        eod=log.eod,
        stores=stores,
        catalog=catalog_public,
        calendar=calendar,
        discount=discount,
        weather=weather.iloc[:t_hist].reset_index(drop=True),
        buzz=buzz[:t_hist],
        styles=list(STYLES),
        on_hand=state.on_hand,
        pipeline=state.pipeline,
        categories=list(CATEGORIES),
        sizes=list(SIZES),
        attributes=["subtype", "style", "color", "fit", "label"],
        trend_attribute="style",
        category_labels=dict(CATEGORY_LABELS),
        currency="INR",
        source="simulated",
    )
    truth = Truth(
        demand=demand,
        lam=lam,
        size_share=share,
        trend=trend[:t_all],
        buzz=buzz,
        weather=weather,
        anomalies=events,
        history_days=t_hist,
    )
    return World(settings, panel, truth)


def history_log(panel: Panel, days: int = 56) -> RunLog:
    """The last ``days`` of observed history as an engine log, for policies that look back."""
    return RunLog(sales=panel.sales[-days:], sod=panel.sod[-days:], eod=panel.eod[-days:])


__all__ = ["COLORS", "Truth", "World", "expected_demand", "generate_world", "history_log"]
