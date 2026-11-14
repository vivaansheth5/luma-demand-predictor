"""The observable data panel.

``Panel`` is everything the agent is allowed to see on the as-of date: sales, stock, the product
and store masters, the known-ahead calendar and discount plan, weather to date, and the social
buzz index to date. To run the agent on real data, build a ``Panel`` from your own tables with
:meth:`Panel.from_frames`; nothing downstream depends on the simulator.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import date, timedelta
from functools import cached_property
from pathlib import Path

import numpy as np
import pandas as pd

from demand_agent.config import CATEGORIES, SIZES


@dataclass
class Panel:
    start: date  # date of day index 0
    sales: np.ndarray  # [T, S, K, Z] units sold
    sod: np.ndarray  # [T, S, K, Z] stock at start of day
    eod: np.ndarray  # [T, S, K, Z] stock at end of day
    stores: pd.DataFrame  # store_id, name, lat, lon, open_day
    catalog: pd.DataFrame  # sku_id, name, category, subtype, style, color, fit, label, price, unit_cost, launch_day
    calendar: pd.DataFrame  # daily, covers the history and the forecast horizon
    discount: np.ndarray  # [>= T + horizon, K] planned discount depth
    weather: pd.DataFrame  # daily, history only
    buzz: np.ndarray  # [T, J] social buzz index per style
    styles: list[str]
    on_hand: np.ndarray  # [S, K, Z] stock now
    pipeline: np.ndarray  # [L, S, K, Z] inbound stock by days-to-arrival

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
        cats = list(CATEGORIES)
        return np.array([cats.index(c) for c in self.catalog["category"].astype(str)], dtype=np.int64)

    @cached_property
    def style_code(self) -> np.ndarray:
        return np.array([self.styles.index(s) for s in self.catalog["style"]], dtype=np.int64)

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
        t, s, k = np.nonzero(np.arange(self.n_days)[:, None, None] >= self.start_day[None])
        n = len(t)
        z = np.tile(np.arange(len(SIZES)), n)
        t, s, k = np.repeat(t, len(SIZES)), np.repeat(s, len(SIZES)), np.repeat(k, len(SIZES))
        sales = pd.DataFrame(
            {
                "date": self.dates[t],
                "store_id": pd.Categorical.from_codes(s, categories=list(self.stores["store_id"])),
                "sku_id": pd.Categorical.from_codes(k, categories=list(self.catalog["sku_id"])),
                "size": pd.Categorical.from_codes(z, categories=list(SIZES)),
                "units_sold": self.sales[t, s, k, z].astype(np.int16),
                "stock_open": self.sod[t, s, k, z].astype(np.int16),
                "stock_close": self.eod[t, s, k, z].astype(np.int16),
            }
        )
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
                "size": np.array(SIZES)[pz],
                "on_hand": self.on_hand[ps, pk, pz],
                **{f"inbound_d{d + 1}": self.pipeline[d, ps, pk, pz] for d in range(self.lead_time)},
            }
        )
        meta = pd.DataFrame(
            [{"start": self.start.isoformat(), "n_days": self.n_days, "plan_days": n_plan, "lead_time": self.lead_time}]
        )
        return {
            "sales": sales,
            "stores": self.stores,
            "catalog": self.catalog,
            "calendar": self.calendar,
            "discounts": discounts,
            "weather": self.weather,
            "buzz": buzz,
            "stock": stock,
            "meta": meta,
        }

    def save(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        for name, frame in self.to_frames().items():
            frame.to_parquet(folder / f"{name}.parquet", index=False)

    @classmethod
    def load(cls, folder: Path) -> Panel:
        frames = {p.stem: pd.read_parquet(p) for p in folder.glob("*.parquet")}
        return cls.from_frames(frames)

    @classmethod
    def from_frames(cls, frames: dict[str, pd.DataFrame]) -> Panel:
        """Build a panel from long tables (the inverse of :meth:`to_frames`)."""
        meta = frames["meta"].iloc[0]
        start = date.fromisoformat(str(meta["start"]))
        n_days, plan_days, lead = int(meta["n_days"]), int(meta["plan_days"]), int(meta["lead_time"])
        stores = frames["stores"].reset_index(drop=True)
        catalog = frames["catalog"].reset_index(drop=True)
        store_ids, sku_ids = list(stores["store_id"]), list(catalog["sku_id"])
        s_n, k_n, z_n = len(store_ids), len(sku_ids), len(SIZES)

        sales = frames["sales"]
        day0 = np.datetime64(start, "D")
        t = (sales["date"].to_numpy().astype("datetime64[D]") - day0).astype(np.int64)
        s = pd.Categorical(sales["store_id"].astype(str), categories=store_ids).codes.astype(np.int64)
        k = pd.Categorical(sales["sku_id"].astype(str), categories=sku_ids).codes.astype(np.int64)
        z = pd.Categorical(sales["size"].astype(str), categories=list(SIZES)).codes.astype(np.int64)
        arrays = {}
        for col in ("units_sold", "stock_open", "stock_close"):
            a = np.zeros((n_days, s_n, k_n, z_n), dtype=np.int32)
            a[t, s, k, z] = sales[col].to_numpy()
            arrays[col] = a

        discount = np.zeros((plan_days, k_n))
        d = frames["discounts"]
        if len(d):
            dt = (d["date"].to_numpy().astype("datetime64[D]") - day0).astype(np.int64)
            dk = pd.Categorical(d["sku_id"].astype(str), categories=sku_ids).codes.astype(np.int64)
            discount[dt, dk] = d["discount"].to_numpy()

        buzz = frames["buzz"]
        styles = [c for c in buzz.columns if c != "date"]
        on_hand = np.zeros((s_n, k_n, z_n), dtype=np.int32)
        pipeline = np.zeros((lead, s_n, k_n, z_n), dtype=np.int32)
        st = frames["stock"]
        if len(st):
            ps = pd.Categorical(st["store_id"].astype(str), categories=store_ids).codes.astype(np.int64)
            pk = pd.Categorical(st["sku_id"].astype(str), categories=sku_ids).codes.astype(np.int64)
            pz = pd.Categorical(st["size"].astype(str), categories=list(SIZES)).codes.astype(np.int64)
            on_hand[ps, pk, pz] = st["on_hand"].to_numpy()
            for dd in range(lead):
                pipeline[dd, ps, pk, pz] = st[f"inbound_d{dd + 1}"].to_numpy()

        return cls(
            start=start,
            sales=arrays["units_sold"],
            sod=arrays["stock_open"],
            eod=arrays["stock_close"],
            stores=stores,
            catalog=catalog,
            calendar=frames["calendar"].reset_index(drop=True),
            discount=discount,
            weather=frames["weather"].reset_index(drop=True),
            buzz=buzz[styles].to_numpy(dtype=float),
            styles=styles,
            on_hand=on_hand,
            pipeline=pipeline,
        )


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
