"""Export pipeline results as one JSON document for the dashboard and as flat tables."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

from luma.agent.tools import Toolbox, driver_effects
from luma.config import QUANTILES, WINDOWS
from luma.data.panel import save_json
from luma.models.pipeline import nb_sf
from luma.run import PipelineResult
from luma.signals.anomaly import expected_series

HISTORY_WEEKS = 26
Z80 = 1.2816  # standard normal quantile for an 80% interval
WEEKLY = [WINDOWS.index((1 + 7 * i, 7)) for i in range(4)]
Q = {q: i for i, q in enumerate(QUANTILES)}


def _weekly(daily: np.ndarray, weeks: int) -> np.ndarray:
    """Sum the last ``weeks * 7`` days of ``daily`` into weeks along axis 0."""
    tail = daily[-weeks * 7 :]
    return tail.reshape(weeks, 7, *tail.shape[1:]).sum(axis=1)


def _r(x, digits: int = 1):
    """Round arrays or scalars and convert to plain Python for JSON."""
    a = np.round(np.asarray(x, dtype=float), digits)
    return a.tolist()


def _band(mean: np.ndarray, sd: float) -> tuple[list, list]:
    return _r(mean * np.exp(-Z80 * sd)), _r(mean * np.exp(Z80 * sd))


def _coordinate(value) -> float | None:
    return float(value) if pd.notna(value) else None


def build_dashboard(res: PipelineResult, brand: str | None = None) -> dict:
    """Assemble everything the dashboard shows."""
    ws, panel, fc = res.workspace, res.panel, res.rolling.live.forecast
    t = panel.n_days
    n_cat = len(panel.categories)
    weeks = min(HISTORY_WEEKS, t // 7)
    demand_daily = res.rolling.live.uncensored.by_sku.astype(float)  # [T, S, K]
    sales_daily = panel.sales.sum(axis=-1).astype(float)
    demand_w = _weekly(demand_daily, weeks)  # [W, S, K]
    sales_w = _weekly(sales_daily, weeks)
    mean_w = np.nan_to_num(fc.mean[:, :, WEEKLY], nan=0.0)  # [S, K, 4]
    q_w = np.nan_to_num(fc.quantiles[:, :, WEEKLY, :], nan=0.0)  # [S, K, 4, Q]
    cat = panel.category_code
    price, cost = panel.price, panel.unit_cost
    catalog, stores = panel.catalog, panel.stores
    toolbox = Toolbox(ws)
    view = toolbox._view_only().view
    committed = ws.committed or {"orders": [], "transfers": [], "lanes": [], "summary": None, "guardrails": []}

    week_end = [panel.date_of(t - 1 - 7 * (weeks - 1 - i)).isoformat() for i in range(weeks)]
    week_end += [panel.date_of(t - 1 + 7 * (i + 1)).isoformat() for i in range(4)]
    cal = panel.calendar
    events = []
    for i in range(weeks + 4):
        last = t - 1 - 7 * (weeks - 1 - i)
        names = [e for e in cal["event"].iloc[max(last - 6, 0) : last + 1] if e and not e.startswith("After ")]
        if names:
            events.append({"week": i, "name": max(set(names), key=names.count)})

    # ---- network, categories, stores -------------------------------------------------------
    net_mean = mean_w.sum(axis=(0, 1))
    lo, hi = _band(net_mean, res.aggregate_sd["network"])
    network = {
        "weeks": week_end,
        "sales": _r(sales_w.sum(axis=(1, 2)), 0),
        "demand": _r(demand_w.sum(axis=(1, 2)), 0),
        "forecast": {"mean": _r(net_mean), "p10": lo, "p90": hi},
        "events": events,
    }
    demand28 = demand_daily[-28:].sum(axis=0)  # [S, K]
    mean28 = np.nan_to_num(fc.window(1, 28)[0], nan=0.0)
    # Totals rounded once, so the headline figures agree with the agent's own brief.
    network |= {
        "demand28": round(float(demand28.sum())),
        "forecast28": round(float(mean28.sum())),
        "revenue28": round(float((mean28 * price[None, :]).sum())),
    }
    risk_cells = (view.risk_short > 0.5).sum(axis=(1, 2))

    categories = []
    for c, key in enumerate(panel.categories):
        m = cat == c
        cm = mean_w[:, m].sum(axis=(0, 1))
        lo, hi = _band(cm, res.aggregate_sd["category"])
        categories.append(
            {
                "key": key,
                "label": panel.category_label(key),
                "skus": int(m.sum()),
                "demand28": round(float(demand28[:, m].sum())),
                "forecast28": round(float(mean28[:, m].sum())),
                "history": _r(demand_w[:, :, m].sum(axis=(1, 2)), 0),
                "forecast": {"mean": _r(cm), "p10": lo, "p90": hi},
            }
        )

    total_by_cat = np.array([demand28[:, cat == c].sum() for c in range(n_cat)])
    styles = panel.styles
    style_code = panel.style_code
    total_by_style = np.array([demand28[:, style_code == j].sum() for j in range(len(styles))])
    dow_profile = []
    store_rows = []
    for s in range(panel.n_stores):
        sm = mean_w[s].sum(axis=0)
        lo, hi = _band(sm, res.aggregate_sd["store"])
        by_cat = np.array([demand28[s, cat == c].sum() for c in range(n_cat)])
        by_style = np.array([demand28[s, style_code == j].sum() for j in range(len(styles))])
        lq_cat = (by_cat / max(by_cat.sum(), 1e-9)) / np.maximum(total_by_cat / max(total_by_cat.sum(), 1e-9), 1e-9)
        lq_style = (by_style / max(by_style.sum(), 1e-9)) / np.maximum(
            total_by_style / max(total_by_style.sum(), 1e-9), 1e-9
        )
        recent = min(56, t)
        d = demand_daily[-recent:, s].sum(axis=1)
        dow = np.array([(panel.start.weekday() + t - recent + i) % 7 for i in range(recent)])
        prof = np.array([d[dow == w].mean() for w in range(7)])
        dow_profile.append(_r(prof / max(prof.mean(), 1e-9), 3))
        store_rows.append(
            {
                "id": str(stores["store_id"].iloc[s]),
                "name": str(stores["name"].iloc[s]),
                "lat": _coordinate(stores["lat"].iloc[s]),
                "lon": _coordinate(stores["lon"].iloc[s]),
                "weeksOpen": int((t - max(int(panel.open_day[s]), 0)) // 7),
                "demand28": round(float(demand28[s].sum())),
                "sales28": int(panel.sales[-28:, s].sum()),
                "forecast28": round(float(mean28[s].sum())),
                "revenue28": round(float((mean28[s] * price).sum())),
                "stockUnits": int(panel.on_hand[s].sum()) if panel.has_stock else None,
                "stockValue": round(float((panel.on_hand[s].sum(axis=-1) * cost).sum())) if panel.has_stock else None,
                "riskCells": int(risk_cells[s]) if panel.has_stock else None,
                "history": _r(demand_w[:, s].sum(axis=1), 0),
                "forecast": {"mean": _r(sm), "p10": lo, "p90": hi},
                "lqCategory": _r(lq_cat, 2),
                "lqStyle": _r(lq_style, 2),
            }
        )

    # ---- products and pairs ----------------------------------------------------------------
    trend_by = panel.trend_attribute
    trend_status = {tr.value: tr.status for tr in ws.trends if tr.attribute == trend_by}
    net_demand28 = demand28.sum(axis=0)
    net_mean28 = mean28.sum(axis=0)
    skus = []
    for k in range(panel.n_skus):
        skus.append(
            {
                "id": str(catalog["sku_id"].iloc[k]),
                "name": str(catalog["name"].iloc[k]),
                "category": str(catalog["category"].iloc[k]),
                "attributes": {a: str(catalog[a].iloc[k]) for a in panel.attributes},
                "trendValue": str(catalog[trend_by].iloc[k]) if trend_by else None,
                "price": float(price[k]),
                "weeksLive": int(max(t - max(int(panel.launch_day[k]), 0), 0) // 7),
                "demand28": round(float(net_demand28[k]), 1),
                "forecast28": round(float(net_mean28[k]), 1),
                "trend": trend_status.get(str(catalog[trend_by].iloc[k]), "Stable") if trend_by else None,
            }
        )

    orders = ws.draft.replenishment.orders if ws.draft is not None and ws.committed else np.zeros_like(panel.on_hand)
    final = ws.draft.position_after if ws.draft is not None and ws.committed else view.position
    risk_after = np.where(view.active[..., None], nb_sf(final, view.mean_long, view.shape_long), 0.0)
    cum = [WINDOWS.index((1, n)) for n in (7, 14, 21, 28)]
    pairs = []
    for s, k in zip(*np.nonzero(fc.active), strict=True):
        pairs.append(
            {
                "s": int(s),
                "k": int(k),
                "level": round(float(fc.level[s, k]), 2),
                "age": int(t - panel.start_day[s, k]),
                "abc": "ABC"[int(view.abc[s, k])],
                "sales": sales_w[:, s, k].astype(int).tolist(),
                "demand": _r(demand_w[:, s, k]),
                "mean": _r(mean_w[s, k]),
                "p10": _r(q_w[s, k, :, Q[0.1]]),
                "p25": _r(q_w[s, k, :, Q[0.25]]),
                "p50": _r(q_w[s, k, :, Q[0.5]]),
                "p75": _r(q_w[s, k, :, Q[0.75]]),
                "p90": _r(q_w[s, k, :, Q[0.9]]),
                "cumMean": _r(np.nan_to_num(fc.mean[s, k, cum])),
                "cumP90": _r(np.nan_to_num(fc.quantiles[s, k, cum, Q[0.9]])),
                "drivers": _r(np.nan_to_num(fc.drivers[s, k], nan=1.0), 3),
                "share": _r(ws.size_curves.share[s, k], 3),
                "exp14": _r(view.mean_long[s, k], 2),
            }
        )
        if panel.has_stock:
            pairs[-1] |= {
                "stock": view.on_hand[s, k].astype(int).tolist(),
                "inbound": view.inbound[s, k].astype(int).tolist(),
                "risk14": _r(view.risk_long[s, k], 2),
                "riskWeek": _r(view.risk_short[s, k], 2),
                "riskAfter": _r(risk_after[s, k], 2),
                "order": orders[s, k].astype(int).tolist(),
                "cover": None
                if not np.isfinite(view.cover_days[s, k])
                else round(float(min(view.cover_days[s, k], 999)), 1),
            }

    risk_rows = toolbox.risk_table(horizon="week") if panel.has_stock else []
    risks = [
        {k: v for k, v in r.items() if not k.startswith("_")} | {"s": r["_s"], "k": r["_k"]} for r in risk_rows[:40]
    ]
    risk_week = (
        {
            "cells": int((view.risk_short > 0.5).sum()),
            "lostRevenue": round(sum(r["expected_lost_revenue"] for r in risk_rows)),
            "pairs": len(risk_rows),
        }
        if panel.has_stock
        else None
    )
    overstock = [
        {k: v for k, v in r.items() if not k.startswith("_")} | {"s": r["_s"], "k": r["_k"]}
        for r in ws.notes.get("overstock", [])[:30]
    ]

    # ---- signals ---------------------------------------------------------------------------
    trends = [asdict(tr) for tr in ws.trends]
    for row in trends:
        row["top_skus"] = [str(catalog["sku_id"].iloc[k]) for k in row["top_skus"]]
    span = max(min(120, t - 30), 28)
    city = demand_daily.sum(axis=(1, 2))
    expected = expected_series(city[:, None])[:, 0]
    anomalies = [
        {
            "scope": a.scope,
            "name": a.name,
            "entity": a.entity,
            "start": panel.date_of(a.start).isoformat(),
            "end": panel.date_of(a.end).isoformat(),
            "day": a.start - (t - span),
            "days": a.end - a.start + 1,
            "direction": a.direction,
            "z": round(a.z, 1),
            "actual": round(a.actual),
            "expected": round(a.expected),
            "cause": a.explained_by,
        }
        for a in ws.anomalies
        if a.end >= t - span
    ]
    anomaly_series = {
        "dates": [panel.date_of(t - span + i).isoformat() for i in range(span)],
        "actual": _r(city[-span:], 0),
        "expected": _r(np.nan_to_num(expected[-span:]), 0),
        "rain": panel.weather["rain_mm"].iloc[-span:].round(0).tolist(),
        "events": [str(e) for e in cal["event"].iloc[t - span : t]],
    }

    # Seasonal shape per category: weekly demand per live product-store pair, indexed to 100.
    all_weeks = t // 7
    live_pairs = (np.arange(t)[:, None, None] >= panel.start_day[None]).astype(float)
    season = {}
    for c, key in enumerate(panel.categories):
        m = cat == c
        units = _weekly(demand_daily[:, :, m].sum(axis=(1, 2)), all_weeks)
        exposure = _weekly(live_pairs[:, :, m].sum(axis=(1, 2)), all_weeks)
        rate = units / np.maximum(exposure, 1.0)
        season[key] = _r(100.0 * rate / max(rate[rate > 0].mean() if (rate > 0).any() else 1.0, 1e-9), 0)
    seasonality = {
        "weeks": [panel.date_of(t - 1 - 7 * (all_weeks - 1 - i)).isoformat() for i in range(all_weeks)],
        "index": season,
        "weekday": dow_profile,
    }

    # ---- agent -----------------------------------------------------------------------------
    agent = {
        "mode": res.agent.mode,
        "model": res.agent.model,
        "status": res.agent.status,
        "seconds": round(res.agent.seconds, 1),
        "brief": res.agent.brief,
        "steps": [
            {
                "n": st.index,
                "thought": st.thought,
                "tool": st.tool,
                "args": st.args,
                "observation": st.observation,
                "seconds": round(st.seconds, 2),
            }
            for st in res.agent.steps
        ],
        "tools": [{"name": spec["name"], "description": spec["description"]} for spec in toolbox.specs()],
    }

    model = res.rolling.model
    meta = {
        "asOf": panel.asof.isoformat(),
        "horizonDays": 28,
        "brand": brand or ("Demo store" if panel.source == "simulated" else "Your store"),
        "currency": panel.currency,
        "source": panel.source,
        "simulated": res.truth is not None,
        "hasStock": panel.has_stock,
        "hasStockHistory": panel.has_stock_history,
        "hasMap": panel.has_map,
        "hasBuzz": panel.has_buzz,
        "canTransfer": ws.can_transfer,
        "trendAttribute": trend_by or None,
        "attributes": list(panel.attributes),
        "seed": res.settings.sim.seed if res.truth is not None else None,
        "counts": {
            "stores": panel.n_stores,
            "skus": panel.n_skus,
            "sizes": panel.n_sizes,
            "days": t,
            "pairs": int(fc.active.sum()),
            "salesRows": int((np.arange(t)[:, None, None] >= panel.start_day[None]).sum() * panel.n_sizes),
        },
        "model": {
            "features": len(model.feature_names),
            "trainRows": model.train_rows,
            "quantiles": list(QUANTILES),
            "importance": res.rolling.live.importance,
            "driverNames": fc.driver_names,
            "offsets": {str(k): _r(v, 3) for k, v in model.offsets.items()},
        },
        "timings": res.timings,
        "policy": {
            "serviceLevels": dict(res.settings.policy.service_levels),
            "budget": res.settings.policy.replenishment_budget,
            "approvalThreshold": res.settings.policy.approval_threshold,
            "leadTimeDays": panel.lead_time,
            "protectionDays": res.settings.policy.protection_days,
            "laneCapacity": res.settings.policy.lane_capacity,
        },
        "sizes": list(panel.sizes),
        "styles": styles,
        "categories": [{"key": c, "label": panel.category_label(c)} for c in panel.categories],
    }

    return {
        "meta": meta,
        "network": network,
        "categories": categories,
        "stores": store_rows,
        "skus": skus,
        "pairs": pairs,
        "risks": risks,
        "riskWeek": risk_week,
        "overstock": overstock,
        "plan": committed,
        "trends": trends,
        "anomalies": anomalies,
        "anomalySeries": anomaly_series,
        "seasonality": seasonality,
        "evaluation": {"forecast": res.evaluation, **res.extras, "policy": res.policy, "aggregateSd": res.aggregate_sd},
        "agent": agent,
    }


def forecast_table(res: PipelineResult) -> pd.DataFrame:
    """Long table of the live forecast: one row per store, product and window."""
    panel, fc = res.panel, res.rolling.live.forecast
    s, k = np.nonzero(fc.active)
    frames = []
    for w, (start, length) in enumerate(WINDOWS):
        frame = pd.DataFrame(
            {
                "store_id": panel.stores["store_id"].to_numpy()[s],
                "sku_id": panel.catalog["sku_id"].to_numpy()[k],
                "window_start": panel.date_of(fc.origin + start).isoformat(),
                "window_days": length,
                "expected_units": fc.mean[s, k, w].round(2),
            }
        )
        for q, j in Q.items():
            frame[f"p{int(q * 100)}"] = fc.quantiles[s, k, w, j].round(2)
        frames.append(frame)
    return pd.concat(frames, ignore_index=True)


def trend_table(res: PipelineResult) -> pd.DataFrame:
    """One row per tested attribute value, most significant first."""
    rows = [asdict(tr) for tr in res.workspace.trends]
    ids = res.panel.catalog["sku_id"].to_numpy()
    for row in rows:
        row["top_skus"] = " ".join(str(ids[k]) for k in row["top_skus"])
    return pd.DataFrame(rows)


def write_outputs(res: PipelineResult, folder: Path, brand: str | None = None) -> dict[str, Path]:
    """Write the dashboard JSON, the flat tables and the saved workspace; returns the paths written."""
    folder.mkdir(parents=True, exist_ok=True)
    paths = {
        "dashboard": folder / "dashboard.json",
        "forecast": folder / "forecast.parquet",
        "trends": folder / "trends.csv",
        "orders": folder / "orders.csv",
        "transfers": folder / "transfers.csv",
        "agent_trace": folder / "agent_trace.json",
        "workspace": folder / "workspace.pkl",
    }
    payload = build_dashboard(res, brand)
    save_json(paths["dashboard"], payload)
    forecast_table(res).to_parquet(paths["forecast"], index=False)
    trend_table(res).to_csv(paths["trends"], index=False)
    res.workspace.save(paths["workspace"])
    plan = res.workspace.committed or {"orders": [], "transfers": []}
    orders = pd.DataFrame(plan["orders"])
    if len(orders):
        orders["sizes"] = orders["sizes"].map(lambda d: " ".join(f"{k}:{v}" for k, v in d.items()))
    orders.to_csv(paths["orders"], index=False)
    pd.DataFrame(plan["transfers"]).to_csv(paths["transfers"], index=False)
    save_json(paths["agent_trace"], payload["agent"])
    return paths


__all__ = ["build_dashboard", "driver_effects", "forecast_table", "trend_table", "write_outputs"]
