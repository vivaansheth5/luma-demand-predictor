"""Starter tables for a brand's own data: headers, a few example rows, and a column guide."""

from __future__ import annotations

from pathlib import Path

REQUIRED = ("sales.csv", "catalog.csv")

TEMPLATES: dict[str, str] = {
    "sales.csv": (
        "date,store_id,sku_id,size,units_sold,stock_open,stock_close\n"
        "2026-01-05,downtown,TEE-001,M,3,12,9\n"
        "2026-01-05,downtown,TEE-001,L,1,6,5\n"
        "2026-01-05,riverside,DRS-014,S,2,2,0\n"
    ),
    "catalog.csv": (
        "sku_id,name,category,price,unit_cost,launch_date,style,color,fit\n"
        "TEE-001,Boxy pocket tee,Tops,35,14,2025-09-01,minimal,black,boxy\n"
        "DRS-014,Tiered midi dress,Dresses,89,34,2025-11-15,romantic,sage,relaxed\n"
    ),
    "stores.csv": (
        "store_id,name,lat,lon,open_date\n"
        "downtown,Downtown,40.7128,-74.0060,2024-03-01\n"
        "riverside,Riverside,40.7812,-73.9665,2025-02-10\n"
    ),
    "stock.csv": (
        "store_id,sku_id,size,on_hand,inbound_d1,inbound_d2,inbound_d3\n"
        "downtown,TEE-001,M,9,0,6,0\n"
        "riverside,DRS-014,S,0,4,0,0\n"
    ),
    "events.csv": (
        "date,end_date,event,channel,intensity\n"
        "2026-11-27,2026-11-30,Black Friday weekend,sale,1.0\n"
        "2026-12-12,2026-12-24,Holiday gifting,festive,0.7\n"
    ),
    "discounts.csv": ("date,sku_id,discount\n2026-11-27,TEE-001,0.20\n2026-11-28,TEE-001,0.20\n"),
    "weather.csv": ("date,tmax,tmax_normal,rain_mm\n2026-01-05,4.5,3.9,0.0\n"),
    "buzz.csv": ("date,minimal,romantic\n2026-01-05,48,61\n2026-01-06,49,64\n"),
}

GUIDE = """# Your data for Luma

Replace the example rows in `sales.csv` and `catalog.csv` with your own, then run:

    luma run --data . --currency USD --brand "Your brand"

Every other table is optional and unlocks more of the agent. The examples are in `optional/`,
where Luma does not read them. Fill one in and move it up beside `sales.csv` to use it.

Column names are matched loosely: `sku`, `product_id` and `item_id` all read as `sku_id`;
`qty`, `quantity` and `units` read as `units_sold`; `store`, `location` and `shop` read as
`store_id`. Files can be CSV or Parquet.

## Required

**sales** has one row per day, product and size (and store, when you have more than one).
Days without a row count as zero sales. At least 110 days of history are needed.

| column | required | meaning |
|---|---|---|
| date | yes | the sales day |
| sku_id | yes | product identifier, matching the catalog |
| units_sold | yes | units sold that day |
| store_id | no | store or channel; leave out for a single store |
| size | no | size label; leave out for one-size products |
| stock_open, stock_close | no | units in stock at the start and end of the day |

**catalog** has one row per product.

| column | required | meaning |
|---|---|---|
| sku_id | yes | product identifier |
| price | yes | selling price |
| name, category | no | used for display and for learning across similar products |
| unit_cost | no | cost per unit; assumed to be 45% of price when missing |
| launch_date | no | first day on sale; taken from the first sale when missing |
| any text column | no | a product attribute such as style, color, fit, fabric or collection |

Every text column with between 2 and 40 distinct values is treated as an attribute. Luma tests
each attribute value for a rising or falling share of demand, so these columns are what trend
detection runs on.

## Optional

| table | columns | what it unlocks |
|---|---|---|
| stock | store_id, sku_id, size, on_hand, inbound_d1 ... | stockout risk, order plans, transfers |
| stores | store_id, name, lat, lon, open_date | store names, the map, transfer distances |
| events | date, end_date, event, channel, intensity | planned events in the forecast |
| discounts | date, sku_id, discount | planned markdowns (0.2 means 20% off) |
| weather | date, tmax, tmax_normal, rain_mm | weather effects |
| buzz | date, then one column per value of the trend attribute | early trend signal |

`channel` in the events table is one of festive, sale, party, sports or campus. `intensity`
runs from 0 to 1. `inbound_d1` is the stock arriving tomorrow, `inbound_d2` the day after.
The buzz table holds any outside interest index, such as search or social volume, with one
column per value of the trend attribute (by default the `style` column of the catalog).

## What each level of data gives you

1. Sales and catalog: demand forecasts with calibrated ranges, size curves, trends, anomalies.
2. Add `stock.csv`: stockout risk, a budgeted order plan, and transfers between stores.
3. Add `stock_open` and `stock_close` to sales: Luma estimates the demand you lost while a size
   was sold out, so that forecasts stop learning from empty shelves.
"""


def write_templates(folder: Path, overwrite: bool = False) -> list[Path]:
    """Write the starter tables and the column guide into ``folder``; returns the files written.

    The two required tables go in ``folder``. Optional tables go in ``folder/optional``, which
    the loader does not read, so their example rows cannot be picked up by mistake.
    """
    written = []
    for name, text in {**TEMPLATES, "README.md": GUIDE}.items():
        path = folder / name if name in REQUIRED or name == "README.md" else folder / "optional" / name
        if path.exists() and not overwrite:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        written.append(path)
    return written
