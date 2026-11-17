"""Read Shopify's own CSV exports.

A brand can upload the orders export (Orders, then Export) on its own, or together with the
products export (Products, then Export). Each Shopify variant is a product and size pair in
Luma: the size option becomes the size, and every other option (usually colour) becomes part
of the product. Without the products export, sizes and colours are read from line item names
such as "Boxy Tee - Black / M".
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

from luma.data.loader import DataError

SIZE_WORDS = ("size", "taille", "talla", "größe", "groesse", "maat", "taglia")
COLOUR_WORDS = ("color", "colour", "couleur", "farbe")
SIZE_PATTERN = re.compile(
    r"^(x{0,3}s|m|x{0,4}l|[2-6]xl|one ?size|os|free ?size|\d{1,2}(\.5)?|\d{2}[a-h]{1,2}|"
    r"(uk|us|eu|it|fr) ?\d{1,2}(\.5)?|w ?\d{2}( ?l ?\d{2})?|\d{2} ?/ ?\d{2}|petite|tall|regular|plus)$",
    re.IGNORECASE,
)
ORDER_FIELDS = ("Created at", "Paid at", "Cancelled at", "Currency", "Location", "Financial Status")


def _lower(frame: pd.DataFrame) -> set[str]:
    return {str(c).strip().lower() for c in frame.columns}


def is_orders(frame: pd.DataFrame) -> bool:
    """True for Shopify's orders export: one row per line item."""
    cols = _lower(frame)
    return "lineitem quantity" in cols and ("lineitem name" in cols or "lineitem sku" in cols)


def is_products(frame: pd.DataFrame) -> bool:
    """True for Shopify's products export: one row per variant."""
    cols = _lower(frame)
    return "handle" in cols and ("variant sku" in cols or "variant price" in cols)


def _text(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip()


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "item"


def _looks_like_size(value: str) -> bool:
    return bool(SIZE_PATTERN.match(value.strip()))


def _split_variant(variant: str) -> tuple[str, list[str]]:
    """Split a variant title such as "Black / M" into the size and the other option values."""
    parts = [p.strip() for p in variant.split(" / ") if p.strip() and p.strip() != "Default Title"]
    sizes = [p for p in parts if _looks_like_size(p)]
    size = sizes[-1] if sizes else "One size"
    others = [p for p in parts if p != size or not sizes]
    return size, others


def _variants(products: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, tuple[str, str]], dict[str, tuple[str, str]]]:
    """One row per Shopify variant, with Luma's product key and size, plus lookup tables."""
    p = products.copy()
    p.columns = [str(c).strip() for c in p.columns]
    if "Handle" not in p:
        raise DataError("the Shopify products export needs its Handle column")
    product_fields = [c for c in p.columns if not c.startswith("Variant") and not c.startswith("Option")]
    product_fields += [f"Option{i} Name" for i in (1, 2, 3) if f"Option{i} Name" in p]
    for col in product_fields:
        if col != "Handle":
            p[col] = p[col].replace("", np.nan)
            p[col] = p.groupby("Handle")[col].transform("first")
    value_cols = [f"Option{i} Value" for i in (1, 2, 3) if f"Option{i} Value" in p]
    p = p[(_text(p[value_cols[0]]) != "") | (_text(p.get("Variant SKU", p["Handle"])) != "")] if value_cols else p

    rows, by_sku, by_name = [], {}, {}
    for _, r in p.iterrows():
        handle = str(r["Handle"]).strip()
        title = str(r.get("Title") or handle).strip()
        options = []
        for i in (1, 2, 3):
            value = str(r.get(f"Option{i} Value") or "").strip()
            if value and value.lower() != "nan" and value != "Default Title":
                options.append((str(r.get(f"Option{i} Name") or "").strip().lower(), value))
        size_at = next((j for j, (n, _) in enumerate(options) if any(w in n for w in SIZE_WORDS)), None)
        if size_at is None:
            size_at = next((j for j, (_, v) in enumerate(options) if _looks_like_size(v)), None)
        size = options[size_at][1] if size_at is not None else "One size"
        others = [v for j, (_, v) in enumerate(options) if j != size_at]
        key = handle + "".join(f"-{_slug(v)}" for v in others)
        colour = next((v for n, v in options if any(w in n for w in COLOUR_WORDS)), "")
        variant_title = " / ".join(v for _, v in options)
        category = str(r.get("Type") or "").strip()
        if (not category or category == "nan") and isinstance(r.get("Product Category"), str):
            category = r["Product Category"].split(">")[-1].strip()
        rows.append(
            {
                "sku_id": key,
                "name": title + (f", {' / '.join(others)}" if others else ""),
                "category": category if category and category != "nan" else "All",
                "price": pd.to_numeric(r.get("Variant Price"), errors="coerce"),
                "unit_cost": pd.to_numeric(r.get("Cost per item"), errors="coerce"),
                "color": colour,
                "vendor": str(r.get("Vendor") or "").strip(),
                "size": size,
                "on_hand": pd.to_numeric(r.get("Variant Inventory Qty"), errors="coerce"),
            }
        )
        sku = str(r.get("Variant SKU") or "").strip()
        if sku and sku.lower() != "nan":
            by_sku[sku] = (key, size)
        by_name[f"{title} - {variant_title}" if variant_title else title] = (key, size)
    if not rows:
        raise DataError("the Shopify products export has no variants")
    return pd.DataFrame(rows), by_sku, by_name


def convert(orders: pd.DataFrame, products: pd.DataFrame | None = None) -> tuple[dict[str, pd.DataFrame], dict]:
    """Turn Shopify exports into Luma's sales and catalog tables (and stock, when it is known).

    Returns the tables and a short report: the currency, how many orders and line items were
    read, and how many line items could not be matched to a product.
    """
    o = orders.copy()
    o.columns = [str(c).strip() for c in o.columns]
    if "Name" in o:
        for col in ORDER_FIELDS:
            if col in o:
                o[col] = o[col].replace("", np.nan)
                o[col] = o.groupby("Name")[col].transform("first")
    if "Cancelled at" in o:
        o = o[_text(o["Cancelled at"]).isin(["", "nan"])]
    if "Financial Status" in o:
        o = o[_text(o["Financial Status"]).str.lower() != "voided"]
    if "Lineitem requires shipping" in o:
        o = o[_text(o["Lineitem requires shipping"]).str.lower() != "false"]
    when = "Created at" if "Created at" in o else ("Paid at" if "Paid at" in o else None)
    if when is None:
        raise DataError("the Shopify orders export needs its Created at column")
    o["date"] = pd.to_datetime(_text(o[when]).str[:10], errors="coerce")
    o["qty"] = pd.to_numeric(o["Lineitem quantity"], errors="coerce")
    o = o[o["date"].notna() & (o["qty"] > 0)]
    if o.empty:
        raise DataError("the Shopify orders export has no line items with a date and a quantity")
    names = _text(o.get("Lineitem name", pd.Series("", index=o.index)))
    skus = _text(o.get("Lineitem sku", pd.Series("", index=o.index)))
    prices = pd.to_numeric(o.get("Lineitem price", pd.Series(np.nan, index=o.index)), errors="coerce")
    location = _text(o["Location"]) if "Location" in o else pd.Series("", index=o.index)
    o["store_id"] = location.where(~location.isin(["", "nan"]), "Online")

    if products is not None:
        variants, by_sku, by_name = _variants(products)
        matched = [by_sku.get(s) or by_name.get(n) for s, n in zip(skus, names, strict=True)]
        catalog = variants.groupby("sku_id", sort=False).agg(
            name=("name", "first"),
            category=("category", "first"),
            price=("price", "median"),
            unit_cost=("unit_cost", "median"),
            color=("color", "first"),
            vendor=("vendor", "first"),
        )
        catalog = catalog.reset_index()
    else:
        variants, matched, rows = None, [], {}
        for name, price in zip(names, prices, strict=True):
            title, _, variant = name.rpartition(" - ") if " - " in name else (name, "", "")
            size, others = _split_variant(variant)
            key = _slug(title) + "".join(f"-{_slug(v)}" for v in others)
            matched.append((key, size))
            row = rows.setdefault(key, {"sku_id": key, "name": title + (f", {' / '.join(others)}" if others else "")})
            row.setdefault("prices", []).append(price)
            row["color"] = others[0] if len(others) == 1 else row.get("color", "")
        catalog = pd.DataFrame(
            [
                {
                    "sku_id": r["sku_id"],
                    "name": r["name"],
                    "category": "All",
                    "price": float(np.nanmedian(r["prices"])) if np.isfinite(r["prices"]).any() else np.nan,
                    "color": r.get("color", ""),
                }
                for r in rows.values()
            ]
        )

    ok = np.array([m is not None for m in matched])
    if not ok.any():
        raise DataError(
            "none of the line items in the Shopify orders export match a product in the products export; "
            "check that both files come from the same store"
        )
    lines = o[ok].copy()
    lines["sku_id"] = [m[0] for m in matched if m is not None]
    lines["size"] = [m[1] for m in matched if m is not None]
    sales = lines.groupby(["date", "store_id", "sku_id", "size"], as_index=False)["qty"].sum()
    sales = sales.rename(columns={"qty": "units_sold"})
    sales["date"] = sales["date"].dt.strftime("%Y-%m-%d")

    # A product needs a price. Fill gaps from what customers paid, and keep sold products only.
    paid = pd.Series(prices[ok].to_numpy(), index=lines["sku_id"].to_numpy()).groupby(level=0).median()
    catalog["price"] = catalog["price"].fillna(catalog["sku_id"].map(paid))
    catalog = catalog[catalog["sku_id"].isin(set(sales["sku_id"])) & (catalog["price"] > 0)]
    sales = sales[sales["sku_id"].isin(set(catalog["sku_id"]))]
    if "unit_cost" in catalog and catalog["unit_cost"].isna().all():
        catalog = catalog.drop(columns="unit_cost")
    elif "unit_cost" in catalog:
        catalog["unit_cost"] = catalog["unit_cost"].fillna((catalog["price"] * 0.45).round(2))
    for col in ("color", "vendor"):
        if col in catalog and catalog[col].replace("", np.nan).nunique() < 2:
            catalog = catalog.drop(columns=col)
    if "category" in catalog and catalog["category"].nunique() < 2:
        catalog["category"] = "All"

    tables = {"sales": sales, "catalog": catalog.reset_index(drop=True)}
    if variants is not None and variants["on_hand"].notna().any() and sales["store_id"].nunique() == 1:
        stock = variants[variants["sku_id"].isin(set(catalog["sku_id"]))].copy()
        stock["store_id"] = sales["store_id"].iloc[0]
        stock["on_hand"] = stock["on_hand"].fillna(0).clip(lower=0)
        tables["stock"] = stock.groupby(["store_id", "sku_id", "size"], as_index=False)["on_hand"].sum()

    currency = None
    if "Currency" in o and o["Currency"].notna().any():
        currency = str(o["Currency"].mode().iloc[0]).upper()
    report = {
        "format": "shopify",
        "currency": currency,
        "orders": int(o["Name"].nunique()) if "Name" in o else None,
        "lineItems": int(len(o)),
        "unmatchedLineItems": int((~ok).sum()),
        "products": int(len(tables["catalog"])),
    }
    return tables, report
