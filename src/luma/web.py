"""Run Luma on files uploaded to the web app.

The web app sends CSV text for each table. This module reads it, converts Shopify exports,
checks that the data fits in one serverless call, runs the pipeline with lighter settings, and
returns the dashboard document that the app and its chat tools read. Nothing is stored.
"""

from __future__ import annotations

import io
import os
from dataclasses import replace

import pandas as pd

from luma.config import Settings
from luma.data import shopify
from luma.data.loader import MIN_DAYS, DataError, build_panel
from luma.data.panel import clean_json
from luma.export import build_dashboard
from luma.run import policy_for, run_pipeline

MAX_PAIRS = 2000  # product and store pairs
MAX_DAYS = 730  # the most recent two years are kept
# Training time grows with pairs times days: 400 pairs over 365 days took 83 seconds on one
# serverless core. The budget keeps every analysis near three minutes, inside the 300-second limit.
WORK_BUDGET = 300_000
TABLES = ("sales", "catalog", "stock", "stores", "events", "discounts", "weather", "buzz")
SHOPIFY = ("shopify_orders", "shopify_products")


def web_settings() -> Settings:
    """Settings for one serverless call: three replayed dates and 70% of the trees."""
    s = Settings()
    model = replace(
        s.model,
        backtest_folds=3,
        n_estimators_mean=int(s.model.n_estimators_mean * 0.7),
        n_estimators_quantile=int(s.model.n_estimators_quantile * 0.7),
        num_threads=max(1, os.cpu_count() or 1),
    )
    return replace(s, model=model)


def read_tables(files: dict[str, str]) -> dict[str, pd.DataFrame]:
    """CSV text by table role to data frames. Unknown roles are an error that names them."""
    unknown = sorted(set(files) - set(TABLES) - set(SHOPIFY))
    if unknown:
        raise DataError(f"unknown table {', '.join(unknown)}; expected one of {', '.join(TABLES + SHOPIFY)}")
    frames = {}
    for role, text in files.items():
        try:
            frames[role] = pd.read_csv(io.StringIO(text), low_memory=False)
        except (pd.errors.ParserError, pd.errors.EmptyDataError, UnicodeDecodeError) as err:
            raise DataError(f"the {role.replace('_', ' ')} file could not be read as CSV ({err})") from err
    return frames


def analyze(
    files: dict[str, str],
    *,
    brand: str | None = None,
    currency: str | None = None,
    lead_time: int | None = None,
) -> dict:
    """Run the whole pipeline on uploaded tables and return the dashboard document."""
    frames = read_tables(files)
    report: dict = {"format": "tables"}
    if "shopify_orders" in frames:
        converted, report = shopify.convert(frames.pop("shopify_orders"), frames.pop("shopify_products", None))
        for name, frame in converted.items():
            frames.setdefault(name, frame)
        currency = currency or report.get("currency")
    elif "shopify_products" in frames:
        raise DataError("add the Shopify orders export too: the products export alone has no sales")
    options = {"currency": (currency or "USD").upper(), "lead_time": lead_time, "max_pairs": MAX_PAIRS}
    panel = build_panel(frames, max_days=MAX_DAYS, **options)
    pairs = panel.n_stores * panel.n_skus
    if pairs * panel.n_days > WORK_BUDGET:
        days = WORK_BUDGET // pairs
        if days < MIN_DAYS:
            raise DataError(
                f"{pairs:,} product and store pairs is more than one analysis here can handle; upload up to "
                f"{WORK_BUDGET // MIN_DAYS:,} (your best sellers, or fewer stores), or run Luma on your own computer"
            )
        panel = build_panel(frames, max_days=days, **options)
        report["historyTrimmedToDays"] = int(days)
    settings = web_settings()
    settings = replace(settings, policy=policy_for(panel, settings.policy))
    result = run_pipeline(settings, panel=panel)
    payload = build_dashboard(result, brand or "Your store")
    payload["meta"]["upload"] = report
    return clean_json(payload)
