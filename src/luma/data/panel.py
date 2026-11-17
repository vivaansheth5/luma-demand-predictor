"""The observable data panel.

``Panel`` is everything the agent is allowed to see on the as-of date: sales, stock, the product
and store masters, the known-ahead calendar and discount plan, weather to date, and the buzz
index to date. Nothing downstream depends on where a panel came from. The simulator builds one
for the demo; :func:`luma.data.loader.load_panel` builds one from a brand's own tables, with
any categories, sizes, product attributes and currency.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from functools import cached_property
from pathlib import Path

import numpy as np
import pandas as pd


@dataclass
class Panel:
    start: date  # date of day index 0
    sales: np.ndarray  # [T, S, K, Z] units sold
    sod: np.ndarray  # [T, S, K, Z] stock at start of day
    eod: np.ndarray  # [T, S, K, Z] stock at end of day
    stores: pd.DataFrame  # store_id, name, lat, lon, open_day
    catalog: pd.DataFrame  # sku_id, name, category, price, unit_cost, launch_day, then attribute columns
    calendar: pd.DataFrame  # daily, covers the history and the forecast horizon
    discount: np.ndarray  # [>= T + horizon, K] planned discount depth
    weather: pd.DataFrame  # daily, history only
    buzz: np.ndarray  # [T, J] buzz index per value of the trend attribute
    styles: list[str]  # values of the trend attribute, in buzz column order
    on_hand: np.ndarray  # [S, K, Z] stock now
    pipeline: np.ndarray  # [L, S, K, Z] inbound stock by days-to-arrival
    categories: list[str]  # category keys in display order
    sizes: list[str]  # size names in display order
    attributes: list[str] = field(default_factory=list)  # catalogue columns tested for trends and fed to the model
    trend_attribute: str = "style"  # the attribute the buzz index is keyed by ("" when there is none)
    category_labels: dict[str, str] = field(default_factory=dict)
    has_stock: bool = True  # current stock is known, so orders and transfers can be planned
    has_stock_history: bool = True  # daily stock is known, so stockouts can be detected and corrected
    has_buzz: bool = True  # a buzz index was supplied for the trend attribute
    currency: str = "INR"
    source: str = "simulated"  # "simulated" or "tables"

    # ---- shape ---------------------------------------------------------------------------
    @property
    def n_days(self) -> int:
        return self.sales.shape[0]

    @property
    def n_stores(self) -> int:
        return self.sales.shape[1]

    @property
    def n_skus(self) -> int:
        return self.sales.shape[2]

    @property
    def asof(self) -> date:
        return self.start + timedelta(days=self.n_days - 1)

    @property
    def lead_time(self) -> int:
        return self.pipeline.shape[0]

    def date_of(self, day: int) -> date:
        return self.start + timedelta(days=int(day))

    @cached_property
    def dates(self) -> np.ndarray:
        return np.array([self.start + timedelta(days=i) for i in range(self.n_days)], dtype="datetime64[D]")

    # ---- product and store attributes as integer codes -------------------------------------
    @cached_property
    def open_day(self) -> np.ndarray:
        return self.stores["open_day"].to_numpy(dtype=np.int64)

    @cached_property
    def launch_day(self) -> np.ndarray:
        return self.catalog["launch_day"].to_numpy(dtype=np.int64)

    @cached_property
    def start_day(self) -> np.ndarray:
        """First day each (store, product) pair could sell, shape ``[S, K]``."""
        return np.maximum(np.maximum(self.open_day[:, None], self.launch_day[None, :]), 0)

    def active_at(self, day: int) -> np.ndarray:
        """Pairs that are open and launched on ``day``, shape ``[S, K]``."""
        return self.start_day <= day

    @cached_property
    def category_code(self) -> np.ndarray:
        cats = list(self.categories)
        return np.array([cats.index(c) for c in self.catalog["category"].astype(str)], dtype=np.int64)

    @cached_property
    def style_code(self) -> np.ndarray:
        """Index into ``styles`` of each product's value of the trend attribute."""
        if not self.trend_attribute:
            return np.zeros(self.n_skus, dtype=np.int64)
        return np.array([self.styles.index(str(s)) for s in self.catalog[self.trend_attribute]], dtype=np.int64)

    def category_label(self, key: str) -> str:
        return self.category_labels.get(key, key)

    @property
    def n_sizes(self) -> int:
        return len(self.sizes)

    @cached_property
    def has_map(self) -> bool:
        """Stores have coordinates, so distances (and transfer costs) are known."""
        return bool(self.stores[["lat", "lon"]].notna().all().all())

    @cached_property
    def price(self) -> np.ndarray:
        return self.catalog["price"].to_numpy(dtype=float)

    @cached_property
    def unit_cost(self) -> np.ndarray:
        return self.catalog["unit_cost"].to_numpy(dtype=float)

    # ---- derived series --------------------------------------------------------------------
    @cached_property
    def censored(self) -> np.ndarray:
        """Cell-days whose sales were capped by stock (sold out by the end of the day)."""
        if not self.has_stock_history:
            return np.zeros(self.sales.shape, dtype=bool)
        active = np.arange(self.n_days)[:, None, None] >= self.start_day[None]
        return (self.eod == 0) & active[..., None]

    def extended(
        self,
        sales: np.ndarray,
        sod: np.ndarray,
        eod: np.ndarray,
        on_hand: np.ndarray,
        pipeline: np.ndarray,
        buzz: np.ndarray,
        weather: pd.DataFrame,
    ) -> Panel:
        """A new panel with extra observed days appended (used by closed-loop evaluation)."""
        return replace(
            self,
            sales=np.concatenate([self.sales, sales]),
            sod=np.concatenate([self.sod, sod]),
            eod=np.concatenate([self.eod, eod]),
            on_hand=on_hand,
            pipeline=pipeline,
            buzz=np.concatenate([self.buzz, buzz]),
            weather=pd.concat([self.weather, weather], ignore_index=True),
        )

    # ---- persistence -----------------------------------------------------------------------
    def to_frames(self) -> dict[str, pd.DataFrame]:
        """Long, database-style tables. ``sales`` has one row per active cell-day."""
        n_z = len(self.sizes)
        t, s, k = np.nonzero(np.arange(self.n_days)[:, None, None] >= self.start_day[None])
        n = len(t)
        z = np.tile(np.arange(n_z), n)
        t, s, k = np.repeat(t, n_z), np.repeat(s, n_z), np.repeat(k, n_z)
        sales = pd.DataFrame(
            {
                "date": self.dates[t],
                "store_id": pd.Categorical.from_codes(s, categories=list(self.stores["store_id"])),
                "sku_id": pd.Categorical.from_codes(k, categories=list(self.catalog["sku_id"])),
                "size": pd.Categorical.from_codes(z, categories=list(self.sizes)),
                "units_sold": self.sales[t, s, k, z].astype(np.int32),
            }
        )
        if self.has_stock_history:
            sales["stock_open"] = self.sod[t, s, k, z].astype(np.int32)
            sales["stock_close"] = self.eod[t, s, k, z].astype(np.int32)
        n_plan = self.discount.shape[0]
        dt, dk = np.nonzero(self.discount > 0)
        discounts = pd.DataFrame(
            {
                "date": np.array([self.start + timedelta(days=int(i)) for i in dt], dtype="datetime64[D]"),
                "sku_id": self.catalog["sku_id"].to_numpy()[dk],
                "discount": self.discount[dt, dk],
            }
        )
        buzz = pd.DataFrame(self.buzz, columns=self.styles)
        buzz.insert(0, "date", self.dates)
        ps, pk, pz = np.nonzero(self.on_hand + self.pipeline.sum(axis=0))
        stock = pd.DataFrame(
            {
                "store_id": self.stores["store_id"].to_numpy()[ps],
                "sku_id": self.catalog["sku_id"].to_numpy()[pk],
                "size": np.array(self.sizes)[pz],
                "on_hand": self.on_hand[ps, pk, pz],
                **{f"inbound_d{d + 1}": self.pipeline[d, ps, pk, pz] for d in range(self.lead_time)},
            }
        )
        meta = pd.DataFrame(
            [
                {
                    "start": self.start.isoformat(),
                    "n_days": self.n_days,
                    "plan_days": n_plan,
                    "lead_time": self.lead_time,
                    "sizes": "|".join(self.sizes),
                    "categories": "|".join(self.categories),
                    "category_labels": json.dumps(self.category_labels),
                    "attributes": "|".join(self.attributes),
                    "trend_attribute": self.trend_attribute,
                    "styles": "|".join(self.styles),
                    "currency": self.currency,
                    "source": self.source,
                }
            ]
        )
        frames = {
            "sales": sales,
            "stores": self.stores,
            "catalog": self.catalog,
            "calendar": self.calendar,
            "discounts": discounts,
            "weather": self.weather,
            "buzz": buzz,
            "meta": meta,
        }
        if self.has_stock:
            frames["stock"] = stock
        return frames

    def save(self, folder: Path, fmt: str | None = None) -> None:
        """Write the tables as Parquet or CSV files (Parquet by default when pyarrow is installed)."""
        folder.mkdir(parents=True, exist_ok=True)
        fmt = fmt or ("parquet" if parquet_available() else "csv")
        for name, frame in self.to_frames().items():
            if fmt == "csv":
                frame.to_csv(folder / f"{name}.csv", index=False)
            else:
                frame.to_parquet(folder / f"{name}.parquet", index=False)

    @classmethod
    def load(cls, folder: Path) -> Panel:
        from luma.data.loader import load_panel

        return load_panel(folder)

    @classmethod
    def from_frames(cls, frames: dict[str, pd.DataFrame]) -> Panel:
        """Build a panel from tables. Only ``sales`` and ``catalog`` are required."""
        from luma.data.loader import build_panel

        return build_panel(frames)


def parquet_available() -> bool:
    """True when pyarrow is installed (the ``parquet`` extra)."""
    try:
        import pyarrow  # noqa: F401
    except ImportError:
        return False
    return True


def clean_json(obj: object) -> object:
    """Convert numpy types, dates and non-finite floats into plain JSON values."""
    if isinstance(obj, dict):
        return {str(k): clean_json(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [clean_json(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return clean_json(obj.tolist())
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, float | np.floating):
        value = float(obj)
        return value if np.isfinite(value) else None
    if isinstance(obj, date | pd.Timestamp):
        return obj.isoformat()[:10]
    return obj


def save_json(path: Path, payload: object) -> None:
    """Write strict JSON (no NaN or Infinity, which browsers reject)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(clean_json(payload), separators=(",", ":"), allow_nan=False))
