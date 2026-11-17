"""The web app's path: Shopify exports, uploaded CSV text, and the serverless limits."""

import io

import numpy as np
import pandas as pd
import pytest

from luma.data import shopify
from luma.data.loader import DataError, build_panel
from luma.web import analyze


@pytest.fixture(scope="module")
def shopify_exports(world):
    """The small demo world written out the way Shopify exports orders and products."""
    frames = world.panel.to_frames()
    catalog = frames["catalog"]
    sales = frames["sales"]
    sales = sales[sales.units_sold > 0].reset_index(drop=True)
    names = dict(zip(catalog.sku_id, catalog.name, strict=True))
    prices = dict(zip(catalog.sku_id, catalog.price, strict=True))

    products = []
    for _, row in catalog.iterrows():
        for i, size in enumerate(world.panel.sizes):
            first = i == 0
            products.append(
                {
                    "Handle": row.sku_id.lower(),
                    "Title": row["name"] if first else "",
                    "Type": row.category if first else "",
                    "Vendor": row.label if first else "",
                    "Option1 Name": "Size" if first else "",
                    "Option1 Value": size,
                    "Variant SKU": f"{row.sku_id}-{size}",
                    "Variant Price": row.price,
                    "Cost per item": row.unit_cost,
                }
            )
    orders = pd.DataFrame(
        {
            "Name": [f"#{1000 + i}" for i in range(len(sales))],
            "Created at": sales["date"].astype(str) + " 12:30:00 +0530",
            "Currency": "INR",
            "Lineitem quantity": sales.units_sold,
            "Lineitem name": [f"{names[s]} - {z}" for s, z in zip(sales.sku_id, sales["size"], strict=True)],
            "Lineitem sku": [f"{s}-{z}" for s, z in zip(sales.sku_id, sales["size"], strict=True)],
            "Lineitem price": [prices[s] for s in sales.sku_id],
            "Lineitem requires shipping": "true",
            "Location": sales.store_id,
            "Cancelled at": "",
        }
    )
    extra = pd.DataFrame(
        [
            # A cancelled order and a gift card are not demand.
            {
                **orders.iloc[0].to_dict(),
                "Name": "#X1",
                "Cancelled at": "2026-01-02 10:00:00 +0530",
                "Lineitem quantity": 50,
            },
            {
                **orders.iloc[1].to_dict(),
                "Name": "#X2",
                "Lineitem name": "Gift card - 2000",
                "Lineitem sku": "",
                "Lineitem requires shipping": "false",
                "Lineitem quantity": 3,
            },
        ]
    )
    return pd.concat([orders, extra], ignore_index=True), pd.DataFrame(products), sales


def test_shopify_exports_are_recognised(shopify_exports):
    orders, products, _ = shopify_exports
    assert shopify.is_orders(orders) and not shopify.is_products(orders)
    assert shopify.is_products(products) and not shopify.is_orders(products)


def test_orders_and_products_exports_become_luma_tables(shopify_exports, world):
    orders, products, sales = shopify_exports
    tables, report = shopify.convert(orders, products)
    assert report["currency"] == "INR" and report["unmatchedLineItems"] == 0
    assert tables["sales"]["units_sold"].sum() == sales.units_sold.sum()  # cancelled and gift cards dropped
    panel = build_panel(tables, currency=report["currency"])
    demo = world.panel
    assert panel.n_skus == demo.n_skus and set(panel.sizes) == set(demo.sizes)
    assert panel.sales.sum() == demo.sales.sum()
    assert "vendor" in panel.attributes and set(panel.categories) == set(demo.categories)
    np.testing.assert_allclose(np.sort(panel.price), np.sort(demo.price))


def test_orders_export_alone_reads_sizes_from_line_item_names(shopify_exports, world):
    orders, _, sales = shopify_exports
    tables, report = shopify.convert(orders)
    assert tables["sales"]["units_sold"].sum() == sales.units_sold.sum()
    assert set(tables["sales"]["size"]) == set(world.panel.sizes)
    assert len(tables["catalog"]) == world.panel.n_skus and (tables["catalog"]["price"] > 0).all()


def test_variant_titles_split_into_size_and_colour():
    assert shopify._split_variant("Black / M") == ("M", ["Black"])
    assert shopify._split_variant("UK 10 / Sage") == ("UK 10", ["Sage"])
    assert shopify._split_variant("Default Title") == ("One size", [])
    assert shopify._split_variant("32 / Indigo / Regular")[0] in {"32", "Regular"}


def _csv(frame: pd.DataFrame) -> str:
    buffer = io.StringIO()
    frame.to_csv(buffer, index=False)
    return buffer.getvalue()


def test_analyze_runs_on_uploaded_shopify_csv(shopify_exports):
    orders, products, _ = shopify_exports
    payload = analyze(
        {"shopify_orders": _csv(orders), "shopify_products": _csv(products)},
        brand="Test brand",
    )
    meta = payload["meta"]
    assert meta["brand"] == "Test brand" and meta["currency"] == "INR" and meta["upload"]["format"] == "shopify"
    assert payload["agent"]["status"] == "forecast_only" and payload["trends"]
    assert payload["evaluation"]["forecast"] is not None


def test_analyze_refuses_what_one_call_cannot_handle(shopify_exports):
    orders, products, _ = shopify_exports
    with pytest.raises(DataError, match="orders export too"):
        analyze({"shopify_products": _csv(products)})
    with pytest.raises(DataError, match="unknown table"):
        analyze({"receipts": "a,b\n1,2\n"})
    tables, _ = shopify.convert(orders, products)
    with pytest.raises(DataError, match="handles up to 10"):
        build_panel(tables, max_pairs=10)
    trimmed = build_panel(tables, max_days=120)
    assert trimmed.n_days == 120
