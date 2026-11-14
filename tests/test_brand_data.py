"""A brand's own tables: loose column names, missing optional data, and what each level unlocks."""

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from luma.agent.tools import Toolbox
from luma.agent.workspace import Workspace
from luma.data.loader import DataError, build_panel, generic_calendar, load_panel, order_sizes, read_tables
from luma.data.templates import TEMPLATES, write_templates
from luma.export import build_dashboard
from luma.money import money
from luma.run import policy_for, run_pipeline


@pytest.fixture(scope="module")
def export(world):
    """The demo world as a brand would export it: order lines and a catalogue, loosely named."""
    frames = world.panel.to_frames()
    sales = frames["sales"]
    lines = sales[sales.units_sold > 0].rename(
        columns={"date": "Order Date", "store_id": "Location", "sku_id": "SKU", "units_sold": "Qty"}
    )[["Order Date", "Location", "SKU", "size", "Qty"]]
    catalog = frames["catalog"].rename(
        columns={"sku_id": "sku", "name": "title", "category": "product_type", "price": "retail_price"}
    )[["sku", "title", "product_type", "retail_price", "style", "color", "fit"]]
    stock = frames["stock"].rename(columns={"store_id": "location", "on_hand": "inventory"})
    return {"sales": lines, "catalog": catalog, "stock": stock}


@pytest.fixture(scope="module")
def forecast_only(export, settings):
    panel = build_panel({"sales": export["sales"], "catalog": export["catalog"]}, currency="USD")
    return run_pipeline(settings, panel=panel)


def test_loader_maps_loose_columns_and_fills_what_is_missing(export, world):
    panel = build_panel({"sales": export["sales"], "catalog": export["catalog"]})
    demo = world.panel
    assert (panel.n_days, panel.n_stores, panel.n_skus) == (demo.n_days, demo.n_stores, demo.n_skus)
    assert panel.sales.sum() == demo.sales.sum()
    assert panel.sizes == ["S", "M", "L", "XL"] and panel.attributes == ["style", "color", "fit"]
    assert panel.trend_attribute == "style" and set(panel.styles) == set(panel.catalog["style"])
    assert panel.currency == "USD" and panel.source == "tables"
    assert not panel.has_stock and not panel.has_stock_history and not panel.has_map and not panel.has_buzz
    assert not panel.censored.any()
    # Costs default to 45% of price, launches to the first sale, and the calendar to a generic one.
    np.testing.assert_allclose(panel.unit_cost, (panel.price * 0.45).round(2))
    assert len(panel.calendar) >= panel.n_days + 28 and (panel.calendar["event"] == "").all()


def test_sales_and_catalog_alone_give_forecasts_and_trends_but_no_plan(forecast_only):
    res = forecast_only
    assert res.truth is None and res.policy is None
    assert res.agent.status == "forecast_only" and res.workspace.committed is None
    assert [s.tool for s in res.agent.steps][0] == "network_overview"
    assert res.evaluation["against"] == "actual sales" and "Oracle (noise floor)" not in res.evaluation["overall"]
    assert res.evaluation["skill_vs_trailing"] is None
    assert any(f["title"].startswith("Add a stock table") for f in res.agent.brief["findings"])

    box = Toolbox(res.workspace)
    assert "error" in box.call("draft_plan", {}) and "error" in box.call("scan_stockout_risk", {})
    assert box.call("commit_plan")["committed"] is False

    d = build_dashboard(res, "Northline")
    meta = d["meta"]
    assert meta["brand"] == "Northline" and meta["currency"] == "USD" and meta["simulated"] is False
    assert not meta["hasStock"] and not meta["canTransfer"] and meta["trendAttribute"] == "style"
    assert d["riskWeek"] is None and d["risks"] == [] and d["plan"]["summary"] is None
    assert "stock" not in d["pairs"][0] and len(d["pairs"][0]["share"]) == 4
    assert all(s["lat"] is None and s["stockUnits"] is None for s in d["stores"])
    assert {t["attribute"] for t in d["trends"]} == {"style", "color", "fit"}


def test_a_stock_table_unlocks_a_plan_in_the_brands_currency(export, settings, forecast_only):
    panel = build_panel(export, currency="EUR", lead_time=5)
    assert panel.has_stock and not panel.has_stock_history and panel.lead_time == 5
    policy = policy_for(panel, settings.policy)
    recent_cost = (panel.sales[-14:].sum(axis=(0, 1, 3)) * panel.unit_cost).sum()
    assert recent_cost <= policy.replenishment_budget < recent_cost + 100
    assert policy.approval_threshold == pytest.approx(0.01 * policy.replenishment_budget, abs=0.5)

    # Reuse the trained model: the stock table changes decisions, not the forecast.
    live = forecast_only.rolling.live
    from luma.agent.loop import run_rules
    from luma.agent.workspace import build_workspace

    ws = build_workspace(
        panel, replace(settings, policy=policy), live.uncensored, forecast_only.rolling.model, live.forecast
    )
    assert not ws.can_transfer  # several stores, but no coordinates
    run = run_rules(ws)
    assert run.status == "committed" and "€" in run.brief["headline"]
    assert ws.committed["transfers"] == [] and ws.committed["orders"]
    assert sum(line["cost"] for line in ws.committed["orders"]) <= policy.replenishment_budget * 1.001


def test_one_store_one_size_and_a_bare_catalog(world, settings):
    sales = world.panel.to_frames()["sales"]
    daily = sales[sales.units_sold > 0].groupby(["date", "sku_id"], as_index=False)["units_sold"].sum()
    catalog = world.panel.catalog[["sku_id", "price"]]
    panel = build_panel({"sales": daily.rename(columns={"units_sold": "qty"}), "catalog": catalog})
    assert panel.n_stores == 1 and panel.sizes == ["One size"] and panel.categories == ["All"]
    assert panel.attributes == [] and panel.trend_attribute == ""
    res = run_pipeline(settings, panel=panel)
    d = build_dashboard(res)
    assert res.agent.status == "forecast_only" and d["trends"] == []
    assert d["meta"]["trendAttribute"] is None and d["stores"][0]["name"] == "All stores"
    assert d["skus"][0]["trend"] is None and d["skus"][0]["attributes"] == {}


def test_canonical_tables_round_trip_through_csv(world, tmp_path):
    world.panel.save(tmp_path, "csv")
    again = load_panel(tmp_path)
    demo = world.panel
    for name in ("sales", "sod", "eod", "on_hand", "pipeline", "discount", "buzz"):
        np.testing.assert_allclose(getattr(again, name), getattr(demo, name), atol=1e-6)
    assert again.sizes == demo.sizes and again.categories == demo.categories and again.styles == demo.styles
    assert again.currency == "INR" and again.has_stock and again.has_stock_history and again.has_map


def test_loader_says_exactly_what_is_wrong(export):
    with pytest.raises(DataError, match="catalog table is required"):
        build_panel({"sales": export["sales"]})
    with pytest.raises(DataError, match="needs the column"):
        build_panel({"sales": export["sales"].drop(columns=["Qty"]), "catalog": export["catalog"]})
    short = export["sales"][export["sales"]["Order Date"] < export["sales"]["Order Date"].min() + pd.Timedelta(days=40)]
    with pytest.raises(DataError, match="at least 110"):
        build_panel({"sales": short, "catalog": export["catalog"]})
    twice = pd.concat([export["catalog"], export["catalog"].head(1)])
    with pytest.raises(DataError, match="more than once"):
        build_panel({"sales": export["sales"], "catalog": twice})
    with pytest.raises(DataError, match="catalog table does not list: JER-001"):
        build_panel({"sales": export["sales"], "catalog": export["catalog"].head(5)})


def test_events_table_fills_the_calendar():
    events = pd.DataFrame(
        {
            "date": ["2026-11-27", "2026-12-12"],
            "end_date": ["2026-11-30", "2026-12-24"],
            "event": ["Black Friday weekend", "Holiday gifting"],
            "channel": ["sale", "festive"],
            "intensity": [1.0, 0.7],
        }
    )
    cal = generic_calendar(pd.Timestamp("2026-11-01").date(), 60, events)
    assert cal["sale"].sum() == 4.0 and cal["festive"].sum() == pytest.approx(13 * 0.7)
    assert cal.loc[26, "event"] == "Black Friday weekend" and cal.loc[25, "event"] == ""


def test_sizes_are_put_in_wearing_order():
    assert order_sizes(["L", "XS", "M", "XXL", "S"]) == ["XS", "S", "M", "L", "XXL"]
    assert order_sizes(["32", "28", "30"]) == ["28", "30", "32"]
    assert order_sizes(["Tall", "Petite"]) == ["Tall", "Petite"]


def test_templates_are_readable_by_the_loader(tmp_path):
    written = write_templates(tmp_path)
    assert {p.name for p in written} == {*TEMPLATES, "README.md"}
    assert write_templates(tmp_path) == []  # never overwrites a brand's own files by default
    # Optional examples sit where the loader does not look, so only the required pair is read.
    assert set(read_tables(tmp_path)) == {"sales", "catalog"}
    # Every column is recognised; the only complaint is that example rows are not a history.
    with pytest.raises(DataError, match="at least 110"):
        load_panel(tmp_path)
    # Each optional example is accepted by the loader once it sits beside real tables.
    optional = read_tables(tmp_path / "optional")
    assert set(optional) == {"stores", "stock", "events", "discounts", "weather", "buzz"}
    cal = generic_calendar(pd.Timestamp("2026-11-01").date(), 60, optional["events"])
    assert cal["sale"].sum() == 4.0


def test_workspace_survives_a_save_and_load(forecast_only, tmp_path):
    forecast_only.workspace.save(tmp_path / "workspace.pkl")
    ws = Workspace.load(tmp_path / "workspace.pkl")
    out = Toolbox(ws).call("scan_trends", {})
    assert "trends" in out or "signals" in out or out


def test_money_follows_the_currency():
    assert money(1_230_000, "INR") == "₹12.3L" and money(11_800_000, "INR") == "₹1.18Cr"
    assert money(123_456, "INR", compact=False) == "₹1,23,456"
    assert money(12_300, "USD") == "$12.3K" and money(1_200_000, "USD") == "$1.20M"
    assert money(8_498, "EUR") == "€8,498" and money(-250, "GBP") == "-£250"
    assert money(500, "SEK") == "SEK 500"
