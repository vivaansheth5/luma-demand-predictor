"""Central configuration.

Every tunable number in the system lives in this file, so a run is fully described by
one ``Settings`` object and one random seed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

# Sizes and categories of the simulated demo world. A panel built from a brand's own tables
# carries its own sizes, categories and attributes; nothing outside ``luma.sim`` reads these.
SIZES: tuple[str, ...] = ("S", "M", "L", "XL")

CATEGORIES: tuple[str, ...] = ("tees", "outerwear", "jerseys", "pants", "womens_tops")

CATEGORY_LABELS: dict[str, str] = {
    "tees": "Graphic tees",
    "outerwear": "Hoodies & jackets",
    "jerseys": "Jerseys",
    "pants": "Pants",
    "womens_tops": "Women's tops",
}

# Forecast targets are demand totals over windows (start_offset, length), in days after the
# forecast origin. Every length is a multiple of 7, so each window holds the same weekday mix.
WINDOWS: tuple[tuple[int, int], ...] = (
    (1, 7),
    (1, 14),
    (1, 21),
    (1, 28),
    (8, 7),
    (8, 14),
    (8, 21),
    (15, 7),
    (15, 14),
    (22, 7),
)

QUANTILES: tuple[float, ...] = (0.1, 0.25, 0.5, 0.75, 0.9, 0.95)

HORIZON_DAYS: int = 28


def window_index(start: int, length: int) -> int:
    """Position of a (start, length) window in ``WINDOWS``."""
    return WINDOWS.index((start, length))


@dataclass(frozen=True)
class SimSettings:
    """Shape of the simulated demo world (a fashion retailer in Bangalore)."""

    seed: int = 7
    start: date = date(2025, 5, 5)  # a Monday
    history_days: int = 560  # 80 weeks; the as-of date is the last history day (a Sunday)
    future_days: int = HORIZON_DAYS  # hidden from the agent; used only to score its decisions
    n_stores: int = 8
    sku_counts: tuple[tuple[str, int], ...] = (
        ("tees", 30),
        ("outerwear", 18),
        ("jerseys", 12),
        ("pants", 20),
        ("womens_tops", 20),
    )
    nb_dispersion: float = 8.0  # gamma-Poisson shape; variance = mean + mean^2 / shape
    lead_time_days: int = 7  # supplier to store
    review_days: int = 7  # stores place replenishment orders every Sunday evening
    initial_pack: tuple[int, ...] = (3, 6, 6, 3)  # opening units per SKU per store, by size
    baseline_cover_days: float = 21.0  # status-quo policy: stock this many days of recent sales
    buzz_lead_days: int = 10  # social buzz leads sales by this many days

    @property
    def n_skus(self) -> int:
        return sum(n for _, n in self.sku_counts)

    @property
    def total_days(self) -> int:
        return self.history_days + self.future_days


@dataclass(frozen=True)
class ModelSettings:
    """Forecasting model configuration."""

    origin_step: int = 7  # days between training forecast origins
    min_history_days: int = 56  # skip origins earlier than this
    row_subsample: float = 0.4  # overlapping windows make rows redundant; train on a random share
    n_estimators_mean: int = 150
    n_estimators_quantile: int = 110
    learning_rate: float = 0.1
    num_leaves: int = 31
    min_data_in_leaf: int = 60
    feature_fraction: float = 0.6
    bagging_fraction: float = 0.8
    lambda_l2: float = 2.0
    tweedie_power: float = 1.2
    level_prior_days: float = 7.0  # shrinkage strength of the per-series demand level
    backtest_folds: int = 4  # rolling origins, 28 days apart; the oldest only seeds calibration
    seed: int = 11
    num_threads: int = field(default_factory=lambda: max(1, min(8, os.cpu_count() or 1)))


@dataclass(frozen=True)
class PolicySettings:
    """Inventory decision configuration."""

    service_levels: tuple[tuple[str, float], ...] = (("A", 0.95), ("B", 0.90), ("C", 0.80))
    abc_cuts: tuple[float, float] = (0.6, 0.9)  # cumulative revenue share boundaries for A and B
    protection_days: int = 14  # lead time + review period
    transfer_horizon_days: int = 7  # demand a transfer must cover before a supplier order lands
    transfer_unit_cost: float = 14.0  # INR handling per unit moved
    transfer_cost_per_km: float = 1.2  # INR per unit per km
    lane_fixed_cost: float = 140.0  # INR per rider trip
    lane_cost_per_km: float = 11.0  # INR per km per trip
    lane_capacity: int = 45  # units a rider carries per trip
    max_trips_per_lane: int = 1
    replenishment_budget: float = 1_800_000.0  # cap per ordering cycle (demo scale, in rupees)
    approval_threshold: float = 15_000.0  # larger single order lines need a person (demo scale)
    overstock_weeks: float = 12.0  # cover above this is flagged as overstock
    milp_time_limit: float = 12.0


@dataclass(frozen=True)
class Settings:
    sim: SimSettings = field(default_factory=SimSettings)
    model: ModelSettings = field(default_factory=ModelSettings)
    policy: PolicySettings = field(default_factory=PolicySettings)
    data_dir: Path = Path("data")
    artifacts_dir: Path = Path("artifacts")


def small_settings(seed: int = 3) -> Settings:
    """A tiny world for tests and CI: runs the whole pipeline in a few seconds."""
    return Settings(
        sim=SimSettings(
            seed=seed,
            history_days=280,
            n_stores=3,
            sku_counts=(("tees", 6), ("outerwear", 4), ("jerseys", 3), ("pants", 4), ("womens_tops", 4)),
        ),
        model=ModelSettings(
            n_estimators_mean=60,
            n_estimators_quantile=40,
            num_leaves=15,
            min_data_in_leaf=20,
            row_subsample=1.0,
            backtest_folds=2,
        ),
        policy=PolicySettings(replenishment_budget=600_000.0, milp_time_limit=10.0),
    )
