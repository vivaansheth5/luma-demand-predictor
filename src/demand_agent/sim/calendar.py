"""Bangalore retail calendar and weather.

The calendar is known in advance (festival dates, planned sale events, sports fixtures), so
the forecaster may use it for future dates. Weather is only known after the fact.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Festival:
    name: str
    day: date
    uplift: float  # peak demand uplift; 0.9 means demand is 1.9x at the pre-festival peak
    lead: int  # days of pre-festival shopping
    hangover: float = 0.0  # depth of the post-festival dip (demand pulled forward)
    party: float = 0.0  # extra partywear pull


@dataclass(frozen=True)
class SaleEvent:
    name: str
    start: date
    end: date
    halo: float  # traffic uplift on all products while the sale runs
    depth: tuple[float, float]  # discount range on participating products
    share: float  # share of the catalogue that participates
    min_age: int = 0  # only products at least this old participate


@dataclass(frozen=True)
class SportsSeason:
    name: str
    start: date
    end: date
    base: float
    peak: float
    peak_day: date | None = None


FESTIVALS: tuple[Festival, ...] = (
    Festival("Eid al-Adha", date(2025, 6, 7), 0.20, 6),
    Festival("Ganesh Chaturthi", date(2025, 8, 27), 0.20, 6),
    Festival("Onam", date(2025, 9, 5), 0.15, 5),
    Festival("Dasara", date(2025, 10, 2), 0.35, 8, hangover=0.08),
    Festival("Diwali", date(2025, 10, 20), 0.90, 14, hangover=0.22),
    Festival("Christmas", date(2025, 12, 25), 0.35, 10, party=0.25),
    Festival("New Year's Eve", date(2025, 12, 31), 0.30, 6, hangover=0.15, party=0.60),
    Festival("Sankranti", date(2026, 1, 14), 0.20, 6),
    Festival("Valentine's Day", date(2026, 2, 14), 0.25, 7, party=0.45),
    Festival("Holi", date(2026, 3, 4), 0.15, 5),
    Festival("Ugadi", date(2026, 3, 19), 0.30, 8, hangover=0.08),
    Festival("Eid al-Adha", date(2026, 5, 27), 0.20, 6),
    Festival("Onam", date(2026, 8, 26), 0.15, 5),
    Festival("Ganesh Chaturthi", date(2026, 9, 14), 0.20, 6),
    Festival("Dasara", date(2026, 10, 20), 0.35, 8, hangover=0.08),
    Festival("Diwali", date(2026, 11, 8), 0.90, 14, hangover=0.22),
    Festival("Christmas", date(2026, 12, 25), 0.35, 10, party=0.25),
    Festival("New Year's Eve", date(2026, 12, 31), 0.30, 6, hangover=0.15, party=0.60),
)

SALES: tuple[SaleEvent, ...] = (
    SaleEvent("End of season sale", date(2025, 7, 4), date(2025, 7, 13), 0.12, (0.20, 0.40), 0.55, 45),
    SaleEvent("Independence Day sale", date(2025, 8, 13), date(2025, 8, 17), 0.08, (0.10, 0.20), 0.45),
    SaleEvent("Black Friday", date(2025, 11, 24), date(2025, 11, 30), 0.15, (0.20, 0.30), 0.60),
    SaleEvent("End of season sale", date(2026, 1, 9), date(2026, 1, 18), 0.12, (0.20, 0.40), 0.55, 45),
    SaleEvent("Republic Day sale", date(2026, 1, 23), date(2026, 1, 26), 0.08, (0.10, 0.20), 0.40),
    SaleEvent("End of season sale", date(2026, 7, 3), date(2026, 7, 12), 0.12, (0.20, 0.40), 0.55, 45),
    SaleEvent("Independence Day sale", date(2026, 8, 13), date(2026, 8, 16), 0.08, (0.10, 0.20), 0.45),
    SaleEvent("Black Friday", date(2026, 11, 23), date(2026, 11, 29), 0.15, (0.20, 0.30), 0.60),
)

SPORTS: tuple[SportsSeason, ...] = (
    SportsSeason("IPL 2025", date(2025, 3, 22), date(2025, 6, 3), 0.20, 0.80, date(2025, 6, 3)),
    SportsSeason("IPL 2026", date(2026, 3, 28), date(2026, 5, 31), 0.20, 0.55, date(2026, 5, 31)),
    SportsSeason("FIFA World Cup 2026", date(2026, 6, 11), date(2026, 7, 19), 0.45, 1.00, date(2026, 7, 19)),
)

# Back-to-campus weeks: colleges reopen and students refresh wardrobes.
CAMPUS: tuple[tuple[date, date], ...] = (
    (date(2025, 7, 14), date(2025, 8, 10)),
    (date(2026, 7, 13), date(2026, 8, 9)),
)

# Monthly climate normals for Bangalore: (max temperature in C, probability of a wet day).
CLIMATE: dict[int, tuple[float, float]] = {
    1: (28.0, 0.03),
    2: (31.0, 0.03),
    3: (33.5, 0.05),
    4: (34.5, 0.15),
    5: (33.5, 0.30),
    6: (29.5, 0.32),
    7: (28.5, 0.38),
    8: (28.0, 0.42),
    9: (28.5, 0.45),
    10: (28.5, 0.42),
    11: (27.5, 0.22),
    12: (26.5, 0.08),
}


def _festival_shape(offset: np.ndarray, lead: int) -> np.ndarray:
    """Shopping intensity around a festival; ``offset`` is days relative to the festival day.

    Shopping builds from ``-lead`` days, peaks two days before, and falls on the day itself.
    """
    rise = np.clip((offset + lead) / max(lead - 2, 1), 0.0, 1.0)
    shape = np.where(offset <= -2, rise, 0.0)
    shape = np.where(offset == -1, 0.8, shape)
    shape = np.where(offset == 0, 0.35, shape)
    return np.where(offset < -lead, 0.0, shape)


def build_calendar(start: date, n_days: int) -> pd.DataFrame:
    """Daily calendar channels for ``n_days`` starting at ``start``.

    Columns
    -------
    festive : signed festival intensity (positive before a festival, negative in the hangover)
    party   : partywear pull (New Year's Eve, Valentine's Day, Christmas)
    sale    : traffic uplift of a running sale event
    sports  : sports-season intensity (drives jerseys)
    campus  : back-to-campus weeks
    payday  : first five days of the month
    month_end : last days of the month, when wallets are thin
    event   : name of the dominant event on that day, for explanations
    """
    days = np.array([start + timedelta(days=i) for i in range(n_days)])
    ordinal = np.array([d.toordinal() for d in days])
    festive = np.zeros(n_days)
    party = np.zeros(n_days)
    sale = np.zeros(n_days)
    sports = np.zeros(n_days)
    campus = np.zeros(n_days)
    event = np.full(n_days, "", dtype=object)
    strength = np.zeros(n_days)

    def label(mask: np.ndarray, name: str, weight: np.ndarray | float) -> None:
        w = np.broadcast_to(np.asarray(weight, dtype=float), mask.shape)
        take = mask & (w > strength)
        event[take] = name
        strength[take] = w[take]

    for f in FESTIVALS:
        off = ordinal - f.day.toordinal()
        shape = _festival_shape(off, f.lead)
        festive += f.uplift * shape
        party += f.party * shape
        label(shape > 0.15, f.name, f.uplift * shape)
        if f.hangover > 0:
            tail = np.where((off >= 1) & (off <= 10), 1.0 - (off - 1) / 10.0, 0.0)
            festive -= f.hangover * tail
            label(tail > 0.3, f"After {f.name}", 0.5 * f.hangover * tail)

    for s in SALES:
        mask = (ordinal >= s.start.toordinal()) & (ordinal <= s.end.toordinal())
        sale[mask] = np.maximum(sale[mask], s.halo)
        label(mask, s.name, 0.25)

    for sp in SPORTS:
        mask = (ordinal >= sp.start.toordinal()) & (ordinal <= sp.end.toordinal())
        span = max(sp.end.toordinal() - sp.start.toordinal(), 1)
        progress = np.clip((ordinal - sp.start.toordinal()) / span, 0.0, 1.0)
        level = sp.base + (sp.peak - sp.base) * progress**2
        sports = np.where(mask, np.maximum(sports, level), sports)
        if sp.peak_day is not None:
            after = ordinal - sp.peak_day.toordinal()
            glow = np.where((after >= 1) & (after <= 3), sp.peak * (1 - after / 4.0), 0.0)
            sports = np.maximum(sports, glow)
        label(mask & (level > 0.5), sp.name, 0.2 * level)

    for c_start, c_end in CAMPUS:
        mask = (ordinal >= c_start.toordinal()) & (ordinal <= c_end.toordinal())
        campus[mask] = 1.0
        label(mask, "Back to campus", 0.05)

    dom = np.array([d.day for d in days])
    return pd.DataFrame(
        {
            "date": pd.to_datetime(days),
            "dow": np.array([d.weekday() for d in days]),
            "doy": np.array([d.timetuple().tm_yday for d in days]),
            "festive": festive,
            "party": party,
            "sale": sale,
            "sports": sports,
            "campus": campus,
            "payday": (dom <= 5).astype(float),
            "month_end": (dom >= 26).astype(float),
            "event": event.astype(str),
        }
    )


def build_weather(start: date, n_days: int, rng: np.random.Generator) -> pd.DataFrame:
    """Daily maximum temperature and rainfall from monthly normals plus AR(1) noise."""
    days = [start + timedelta(days=i) for i in range(n_days)]
    normal = np.array([CLIMATE[d.month][0] for d in days])
    wet_p = np.array([CLIMATE[d.month][1] for d in days])
    anomaly = np.zeros(n_days)
    shocks = rng.normal(0.0, 1.1, n_days)
    for i in range(1, n_days):
        anomaly[i] = 0.7 * anomaly[i - 1] + shocks[i]
    wet = rng.random(n_days) < wet_p
    rain = np.where(wet, rng.gamma(0.8, 14.0, n_days), 0.0)
    return pd.DataFrame(
        {
            "date": pd.to_datetime(days),
            "tmax": np.round(normal + anomaly - 0.6 * wet, 1),
            "tmax_normal": normal,
            "rain_mm": np.round(rain, 1),
        }
    )


def discount_plan(
    start: date,
    n_days: int,
    launch_day: np.ndarray,
    markdown_day: np.ndarray,
    markdown_depth: np.ndarray,
    rng: np.random.Generator,
) -> np.ndarray:
    """Planned discount depth per day and product, shape ``[n_days, n_skus]``.

    Sale events discount a random share of the catalogue. Products in late life also carry a
    standing markdown from ``markdown_day`` onwards. The plan covers future dates because a
    retailer sets it in advance.
    """
    n_skus = len(launch_day)
    plan = np.zeros((n_days, n_skus))
    day_index = np.arange(n_days)[:, None]
    plan = np.where(day_index >= markdown_day[None, :], markdown_depth[None, :], plan)
    ordinal0 = start.toordinal()
    for s in SALES:
        lo = max(s.start.toordinal() - ordinal0, 0)
        hi = min(s.end.toordinal() - ordinal0, n_days - 1)
        if hi < 0 or lo > n_days - 1:
            continue
        eligible = (lo - launch_day) >= s.min_age
        chosen = eligible & (rng.random(n_skus) < s.share)
        depth = np.round(rng.uniform(s.depth[0], s.depth[1], n_skus) / 0.05) * 0.05
        plan[lo : hi + 1, chosen] = np.maximum(plan[lo : hi + 1, chosen], depth[chosen])
    return plan
