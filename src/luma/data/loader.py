"""Build a panel from a brand's own tables.

Two tables are required and the rest are optional. Each optional table switches on more of
the system:

==========  ==================================================  ==================================
Table       Columns                                             What it enables
==========  ==================================================  ==================================
sales       date, sku_id, units_sold                            Required. Trends, forecasts,
            optional: store_id, size, stock_open, stock_close   anomalies. With the stock columns,
                                                                stockouts are detected and corrected
catalog     sku_id, price                                       Required. Every other text column
            optional: name, category, unit_cost, launch_date    (style, colour, fabric, ...) becomes
                                                                an attribute tested for trends
stock       sku_id, on_hand                                     Stockout risk and order plans
            optional: store_id, size, inbound_d1 ... inbound_dN
stores      store_id                                            Store names; with lat and lon, the
            optional: name, lat, lon, open_date                 map and store-to-store transfers
events      date, event                                         Festivals, sales and launches the
            optional: end_date, channel, intensity              forecast should know about
discounts   date, sku_id, discount                              Planned markdowns, past and future
buzz        date, then one column per trend-attribute value     A leading signal for trends
weather     date, rain_mm                                       Explains weather-driven anomalies
==========  ==================================================  ==================================

Files may be CSV or Parquet. Column names are matched case-insensitively and common synonyms
(``quantity``, ``product_id``, ``location``) are accepted.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from luma.data.panel import Panel

CHANNELS: tuple[str, ...] = ("festive", "party", "sale", "sports", "campus")
MIN_DAYS = 110  # shortest history that can train a model and forecast 28 days
PLAN_AHEAD = 63  # days of calendar and discount plan kept beyond the last sales day
UNKNOWN_STOCK = 9999  # stock level assumed when daily stock is not recorded (never sold out)
STANDARD_SIZES = ("XXXS", "XXS", "XS", "S", "M", "L", "XL", "XXL", "2XL", "XXXL", "3XL", "4XL", "5XL")

SYNONYMS: dict[str, tuple[str, ...]] = {
    "date": ("date", "day", "order_date", "sale_date"),
    "store_id": ("store_id", "store", "location", "location_id", "shop", "channel_id"),
    "sku_id": ("sku_id", "sku", "product_id", "item_id", "style_id", "article"),
    "size": ("size", "variant_size", "size_name"),
    "units_sold": ("units_sold", "units", "quantity", "qty", "sold"),
    "stock_open": ("stock_open", "opening_stock", "stock_start"),
    "stock_close": ("stock_close", "closing_stock", "stock_end", "stock_eod"),
    "on_hand": ("on_hand", "stock", "inventory", "available"),
    "price": ("price", "retail_price", "mrp", "list_price"),
    "unit_cost": ("unit_cost", "cost", "cogs"),
    "name": ("name", "title", "product_name", "description"),
    "category": ("category", "department", "product_type", "class"),
}
RESERVED = {"sku_id", "name", "category", "price", "unit_cost", "launch_day", "launch_date"}


class DataError(ValueError):
    """The supplied tables cannot be turned into a panel; the message says what to fix."""


def read_tables(folder: Path) -> dict[str, pd.DataFrame]:
    """Read every CSV and Parquet file in ``folder``, keyed by file name without extension."""
    folder = Path(folder)
    if not folder.is_dir():
        raise DataError(f"{folder} is not a folder")
    frames: dict[str, pd.DataFrame] = {}
    for path in sorted(folder.iterdir()):
        if path.suffix == ".parquet":
            frames[path.stem.lower()] = pd.read_parquet(path)
        elif path.suffix == ".csv":
            frames[path.stem.lower()] = pd.read_csv(path)
    if not frames:
        raise DataError(f"no CSV or Parquet files found in {folder}")
    return frames


def load_panel(folder: Path, **options) -> Panel:
    """Read the tables in ``folder`` and build a panel (see :func:`build_panel` for options)."""
    return build_panel(read_tables(folder), **options)


def _standardise(frame: pd.DataFrame, table: str, required: tuple[str, ...]) -> pd.DataFrame:
    """Lower-case column names, map synonyms to canonical names, and check required columns."""
    out = frame.copy()
    out.columns = [str(c).strip().lower().replace(" ", "_") for c in out.columns]
    rename = {}
    for canonical, names in SYNONYMS.items():
        if canonical in out.columns:
            continue
        match = next((n for n in names if n in out.columns), None)
        if match:
            rename[match] = canonical
    out = out.rename(columns=rename)
    missing = [c for c in required if c not in out.columns]
    if missing:
        raise DataError(f"the {table} table needs the column(s) {', '.join(missing)}; found {', '.join(out.columns)}")
    return out


def _days(values: pd.Series, start: np.datetime64) -> np.ndarray:
    parsed = pd.to_datetime(values, errors="coerce")
    if parsed.isna().any():
        bad = values[parsed.isna()].iloc[0]
        raise DataError(f"could not read {bad!r} as a date")
    return (parsed.to_numpy().astype("datetime64[D]") - start).astype(np.int64)


def _codes(values: pd.Series, known: list[str], what: str, table: str) -> np.ndarray:
    """Position of each value in ``known``; unknown values are an error that names them."""
    index = {v: i for i, v in enumerate(known)}
    text = values.astype(str)
    codes = text.map(index).fillna(-1).to_numpy().astype(np.int64)
    if (codes < 0).any():
        unknown = sorted(set(text[codes < 0]))[:5]
        master = {"sku_id": "catalog", "store_id": "stores", "size": "sales"}.get(what, "master")
        raise DataError(
            f"the {table} table has {what} values that the {master} table does not list: {', '.join(unknown)}"
        )
    return codes


def order_sizes(sizes: list[str]) -> list[str]:
    """Order sizes the way a rail is ordered: XS to XL, or numerically, else as first seen."""
    upper = [s.upper() for s in sizes]
    if all(u in STANDARD_SIZES for u in upper):
        return [s for _, s in sorted(zip(upper, sizes, strict=True), key=lambda p: STANDARD_SIZES.index(p[0]))]
    try:
        return [s for _, s in sorted((float(s), s) for s in sizes)]
    except ValueError:
        return sizes


def generic_calendar(start: date, n_days: int, events: pd.DataFrame | None = None) -> pd.DataFrame:
    """A calendar with weekday, pay cycle and any events the brand supplies.

    ``events`` rows have a ``date`` and an ``event`` name, and optionally ``end_date``, a
    ``channel`` (festive, sale, party, sports or campus; default festive) and an ``intensity``
    between 0 and 1 (default 0.5).
    """
    days = [start + timedelta(days=i) for i in range(n_days)]
    cal = pd.DataFrame(
        {
            "date": pd.to_datetime(days),
            "dow": [d.weekday() for d in days],
            "doy": [d.timetuple().tm_yday for d in days],
            **{c: np.zeros(n_days) for c in CHANNELS},
            "payday": [float(d.day <= 5) for d in days],
            "month_end": [float(d.day >= 26) for d in days],
            "event": [""] * n_days,
        }
    )
    if events is None or not len(events):
        return cal
    ev = _standardise(events, "events", ("date", "event"))
    day0 = np.datetime64(start, "D")
    first = _days(ev["date"], day0)
    last = _days(ev["end_date"], day0) if "end_date" in ev.columns else first
    channel = ev["channel"].astype(str).str.lower() if "channel" in ev.columns else pd.Series(["festive"] * len(ev))
    intensity = pd.to_numeric(ev["intensity"], errors="coerce").fillna(0.5) if "intensity" in ev.columns else None
    strength = np.zeros(n_days)
    names = cal["event"].to_numpy(dtype=object).copy()
    for i in range(len(ev)):
        lo, hi = max(int(first[i]), 0), min(int(last[i]), n_days - 1)
        if hi < lo:
            continue
        ch = channel.iloc[i] if channel.iloc[i] in CHANNELS else "festive"
        level = float(intensity.iloc[i]) if intensity is not None else 0.5
        col = cal[ch].to_numpy().copy()
        col[lo : hi + 1] = np.maximum(col[lo : hi + 1], level)
        cal[ch] = col
        stronger = level > strength[lo : hi + 1]
        names[lo : hi + 1] = np.where(stronger, str(ev["event"].iloc[i]), names[lo : hi + 1])
        strength[lo : hi + 1] = np.maximum(strength[lo : hi + 1], level)
    cal["event"] = names.astype(str)
    return cal


def build_panel(
    frames: dict[str, pd.DataFrame],
    *,
    lead_time: int | None = None,
    currency: str | None = None,
    attributes: list[str] | None = None,
    trend_attribute: str | None = None,
    max_days: int | None = None,
    max_pairs: int | None = None,
) -> Panel:
    """Build a panel from tables.

    Parameters
    ----------
    frames : tables by name. ``sales`` and ``catalog`` are required.
    lead_time : supplier lead time in days (default 7, or the ``meta`` table's value).
    currency : ISO code used for display, such as "INR", "USD" or "EUR".
    attributes : catalogue columns to treat as product attributes. Default: every text column
        with between 2 and 40 distinct values.
    trend_attribute : the attribute the buzz table is keyed by. Default "style" when present.
    max_days : keep only the most recent days of sales history.
    max_pairs : refuse data with more product and store pairs than this, before any large array
        is built.
    """
    frames = {k.lower(): v for k, v in frames.items()}
    for needed in ("sales", "catalog"):
        if needed not in frames:
            raise DataError(f"a {needed} table is required; found {', '.join(sorted(frames)) or 'no tables'}")
    meta = frames["meta"].iloc[0].to_dict() if "meta" in frames and len(frames["meta"]) else {}

    def meta_list(key: str) -> list[str] | None:
        value = meta.get(key)
        return [v for v in str(value).split("|") if v] if isinstance(value, str) else None

    # ---- sales: dates and keys -------------------------------------------------------------
    sales = _standardise(frames["sales"], "sales", ("date", "sku_id", "units_sold"))
    sales["date"] = pd.to_datetime(sales["date"], errors="coerce")
    if sales["date"].isna().any():
        raise DataError("the sales table has dates that could not be read")
    if max_days is not None and "start" not in meta:
        sales = sales[sales["date"] > sales["date"].max() - pd.Timedelta(days=max_days)]
    start = date.fromisoformat(str(meta["start"])) if "start" in meta else sales["date"].min().date()
    day0 = np.datetime64(start, "D")
    t = (sales["date"].to_numpy().astype("datetime64[D]") - day0).astype(np.int64)
    n_days = int(meta["n_days"]) if "n_days" in meta else int(t.max()) + 1
    if n_days < MIN_DAYS:
        raise DataError(f"the sales table covers {n_days} days; at least {MIN_DAYS} are needed to train and forecast")
    if "store_id" not in sales.columns:
        sales["store_id"] = "ALL"
    if "size" not in sales.columns:
        sales["size"] = "One size"
    for col in ("store_id", "sku_id", "size"):
        sales[col] = sales[col].astype(str)

    # ---- catalogue -------------------------------------------------------------------------
    catalog = _standardise(frames["catalog"], "catalog", ("sku_id", "price")).reset_index(drop=True)
    catalog["sku_id"] = catalog["sku_id"].astype(str)
    if catalog["sku_id"].duplicated().any():
        dup = catalog.loc[catalog["sku_id"].duplicated(), "sku_id"].iloc[0]
        raise DataError(f"the catalog table lists {dup} more than once")
    if "name" not in catalog.columns:
        catalog["name"] = catalog["sku_id"]
    if "category" not in catalog.columns:
        catalog["category"] = "All"
    catalog["category"] = catalog["category"].astype(str)
    catalog["price"] = pd.to_numeric(catalog["price"], errors="coerce")
    if catalog["price"].isna().any() or (catalog["price"] <= 0).any():
        raise DataError("every product in the catalog table needs a positive price")
    if "unit_cost" not in catalog.columns:
        # No cost supplied: assume a 55% gross margin so that margins and budgets can be computed.
        catalog["unit_cost"] = (catalog["price"] * 0.45).round(2)
    sku_ids = list(catalog["sku_id"])
    if max_pairs is not None:
        n_stores = len(frames["stores"]) if "stores" in frames else sales["store_id"].nunique()
        if n_stores * len(sku_ids) > max_pairs:
            raise DataError(
                f"the data has {len(sku_ids):,} products in {n_stores:,} store(s), {n_stores * len(sku_ids):,} "
                f"pairs in all; this version handles up to {max_pairs:,}. Upload your best sellers, or run Luma "
                "on your own computer for the full catalog"
            )
    k = _codes(sales["sku_id"], sku_ids, "sku_id", "sales")
    if "launch_day" not in catalog.columns:
        if "launch_date" in catalog.columns:
            catalog["launch_day"] = _days(catalog["launch_date"], day0)
        else:
            first_sale = pd.Series(t).groupby(k).min()
            catalog["launch_day"] = [int(first_sale.get(i, n_days - 1)) for i in range(len(catalog))]
    catalog = catalog.drop(columns=[c for c in ("launch_date",) if c in catalog.columns])
    catalog["launch_day"] = catalog["launch_day"].astype(np.int64)

    if attributes is None:
        attributes = meta_list("attributes")
    if attributes is None:
        attributes = [
            c
            for c in catalog.columns
            if c not in RESERVED
            and not pd.api.types.is_numeric_dtype(catalog[c])
            and 2 <= catalog[c].astype(str).nunique() <= 40
        ]
    attributes = [a for a in attributes if a in catalog.columns]
    for a in attributes:
        catalog[a] = catalog[a].astype(str)
    if trend_attribute is None:
        trend_attribute = meta.get("trend_attribute") if isinstance(meta.get("trend_attribute"), str) else None
    if trend_attribute is None or trend_attribute not in attributes:
        trend_attribute = "style" if "style" in attributes else (attributes[0] if attributes else "")
    categories = meta_list("categories") or list(dict.fromkeys(catalog["category"]))
    labels = json.loads(meta["category_labels"]) if isinstance(meta.get("category_labels"), str) else {}
    labels = {c: labels.get(c, c.replace("_", " ").capitalize() if c.islower() else c) for c in categories}

    # ---- stores ----------------------------------------------------------------------------
    if "stores" in frames:
        stores = _standardise(frames["stores"], "stores", ("store_id",)).reset_index(drop=True)
        stores["store_id"] = stores["store_id"].astype(str)
    else:
        stores = pd.DataFrame({"store_id": list(dict.fromkeys(sales["store_id"]))})
    store_ids = list(stores["store_id"])
    s = _codes(sales["store_id"], store_ids, "store_id", "sales")
    if "name" not in stores.columns:
        stores["name"] = ["All stores" if i == "ALL" else i for i in store_ids]
    for col in ("lat", "lon"):
        stores[col] = pd.to_numeric(stores[col], errors="coerce") if col in stores.columns else np.nan
    if "open_day" not in stores.columns:
        if "open_date" in stores.columns:
            stores["open_day"] = _days(stores["open_date"], day0)
        else:
            first_sale = pd.Series(t).groupby(s).min()
            stores["open_day"] = [int(first_sale.get(i, 0)) for i in range(len(stores))]
    stores = stores.drop(columns=[c for c in ("open_date",) if c in stores.columns])
    stores["open_day"] = stores["open_day"].astype(np.int64)

    # ---- sizes and dense arrays ------------------------------------------------------------
    sizes = meta_list("sizes") or order_sizes(list(dict.fromkeys(sales["size"])))
    z = _codes(sales["size"], sizes, "size", "sales")
    shape = (n_days, len(store_ids), len(sku_ids), len(sizes))
    inside = (t >= 0) & (t < n_days)
    units = pd.to_numeric(sales["units_sold"], errors="coerce").fillna(0).to_numpy()
    sold = np.zeros(shape, dtype=np.int32)
    np.add.at(sold, (t[inside], s[inside], k[inside], z[inside]), np.maximum(units[inside], 0).astype(np.int32))

    has_history = {"stock_open", "stock_close"} <= set(sales.columns)
    if has_history:
        sod = np.zeros(shape, dtype=np.int32)
        eod = np.zeros(shape, dtype=np.int32)
        sod[t[inside], s[inside], k[inside], z[inside]] = (
            pd.to_numeric(sales["stock_open"]).fillna(0).to_numpy()[inside]
        )
        eod[t[inside], s[inside], k[inside], z[inside]] = (
            pd.to_numeric(sales["stock_close"]).fillna(0).to_numpy()[inside]
        )
    else:
        sod = np.full(shape, UNKNOWN_STOCK, dtype=np.int32)
        eod = np.full(shape, UNKNOWN_STOCK, dtype=np.int32)

    # ---- current stock ---------------------------------------------------------------------
    lead = int(lead_time or meta.get("lead_time") or 7)
    on_hand = np.zeros(shape[1:], dtype=np.int32)
    pipeline = np.zeros((lead, *shape[1:]), dtype=np.int32)
    has_stock = "stock" in frames or has_history
    if "stock" in frames:
        st = _standardise(frames["stock"], "stock", ("sku_id", "on_hand"))
        if "store_id" not in st.columns:
            if len(store_ids) > 1:
                raise DataError("the stock table needs a store_id column when there is more than one store")
            st["store_id"] = store_ids[0]
        if "size" not in st.columns:
            if len(sizes) > 1:
                raise DataError("the stock table needs a size column when products come in more than one size")
            st["size"] = sizes[0]
        ps = _codes(st["store_id"].astype(str), store_ids, "store_id", "stock")
        pk = _codes(st["sku_id"].astype(str), sku_ids, "sku_id", "stock")
        pz = _codes(st["size"].astype(str), sizes, "size", "stock")
        on_hand[ps, pk, pz] = pd.to_numeric(st["on_hand"], errors="coerce").fillna(0).to_numpy()
        for d in range(lead):
            col = f"inbound_d{d + 1}"
            if col in st.columns:
                pipeline[d, ps, pk, pz] = pd.to_numeric(st[col], errors="coerce").fillna(0).to_numpy()
    elif has_history:
        on_hand = eod[-1].copy()

    # ---- calendar, discounts, weather, buzz ------------------------------------------------
    plan_days = max(int(meta.get("plan_days", 0) or 0), n_days + PLAN_AHEAD)
    full = {"date", "dow", "doy", "payday", "month_end", "event", *CHANNELS}
    if "calendar" in frames and full <= set(frames["calendar"].columns) and len(frames["calendar"]) >= plan_days:
        calendar = frames["calendar"].reset_index(drop=True)
        calendar["event"] = calendar["event"].fillna("").astype(str)
    else:
        calendar = generic_calendar(start, plan_days, frames.get("events"))

    discount = np.zeros((plan_days, len(sku_ids)))
    if "discounts" in frames and len(frames["discounts"]):
        dsc = _standardise(frames["discounts"], "discounts", ("date", "sku_id", "discount"))
        dt = _days(dsc["date"], day0)
        dk = _codes(dsc["sku_id"].astype(str), sku_ids, "sku_id", "discounts")
        ok = (dt >= 0) & (dt < plan_days)
        discount[dt[ok], dk[ok]] = np.clip(
            pd.to_numeric(dsc["discount"], errors="coerce").fillna(0).to_numpy()[ok], 0, 0.95
        )

    dates = pd.to_datetime([start + timedelta(days=i) for i in range(n_days)])
    weather = pd.DataFrame({"date": dates, "tmax": 0.0, "tmax_normal": 0.0, "rain_mm": 0.0})
    if "weather" in frames and len(frames["weather"]):
        w = _standardise(frames["weather"], "weather", ("date",))
        w["date"] = pd.to_datetime(w["date"])
        w = w.drop_duplicates("date").set_index("date").reindex(dates)
        for col in ("tmax", "tmax_normal", "rain_mm"):
            if col in w.columns:
                weather[col] = pd.to_numeric(w[col], errors="coerce").fillna(0.0).to_numpy()

    styles = meta_list("styles") or (sorted(catalog[trend_attribute].unique()) if trend_attribute else ["all"])
    buzz = np.full((n_days, len(styles)), 50.0)
    if "buzz" in frames and len(frames["buzz"]) and trend_attribute:
        b = frames["buzz"].copy()
        b.columns = [str(c).strip() for c in b.columns]
        date_col = next((c for c in b.columns if c.lower() == "date"), None)
        if date_col is None:
            raise DataError("the buzz table needs a date column")
        b[date_col] = pd.to_datetime(b[date_col])
        b = b.drop_duplicates(date_col).set_index(date_col).reindex(dates).ffill().bfill()
        lookup = {c.lower(): c for c in b.columns}
        for j, value in enumerate(styles):
            if value.lower() in lookup:
                buzz[:, j] = pd.to_numeric(b[lookup[value.lower()]], errors="coerce").fillna(50.0).to_numpy()

    return Panel(
        start=start,
        sales=sold,
        sod=sod,
        eod=eod,
        stores=stores,
        catalog=catalog,
        calendar=calendar,
        discount=discount,
        weather=weather,
        buzz=buzz,
        styles=[str(v) for v in styles],
        on_hand=on_hand,
        pipeline=pipeline,
        categories=[str(c) for c in categories],
        sizes=[str(v) for v in sizes],
        attributes=list(attributes),
        trend_attribute=trend_attribute,
        category_labels=labels,
        has_stock=has_stock,
        has_stock_history=has_history,
        has_buzz="buzz" in frames and bool(len(frames["buzz"])) and bool(trend_attribute),
        currency=str(currency or meta.get("currency") or "USD"),
        source=str(meta.get("source") or "tables"),
    )
