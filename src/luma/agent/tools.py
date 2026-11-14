"""The agent's tools.

Each tool is a typed function over the workspace with a JSON schema, so the same set serves the
built-in planner and a language model. Tools return compact, JSON-serialisable dictionaries.

Tools read, plan, check and commit. No tool can bypass the guardrails: ``commit_plan`` refuses
any draft that fails a hard check, so a planner of either kind has to repair the plan first.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from luma.agent.workspace import Workspace
from luma.config import QUANTILES
from luma.inventory.decide import InventoryPlan, decide
from luma.models.pipeline import nb_sf
from luma.money import money

Q10, Q90 = QUANTILES.index(0.1), QUANTILES.index(0.9)


@dataclass
class Tool:
    name: str
    description: str
    schema: dict
    fn: Callable[..., dict]

    def spec(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.schema}


def _obj(properties: dict | None = None, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties or {}, "required": required or []}


def driver_effects(ws: Workspace, s: int, k: int, min_effect: float = 0.04) -> list[dict]:
    """Driver groups that move this pair's 28-day forecast by at least ``min_effect``."""
    factors = ws.forecast.drivers[s, k]
    out = []
    for name, f in zip(ws.forecast.driver_names, factors, strict=True):
        if np.isfinite(f) and abs(f - 1.0) >= min_effect:
            out.append({"driver": name, "effect_pct": round(100.0 * (float(f) - 1.0), 1)})
    out.sort(key=lambda d: -abs(d["effect_pct"]))
    return out


def reason_for(ws: Workspace, s: int, k: int) -> str:
    """One sentence on why a pair's forecast sits where it does."""
    top = [d for d in driver_effects(ws, s, k) if d["driver"] != "Forecast window"][:2]
    if not top:
        return "Forecast is in line with the recent run-rate."
    parts = [f"{d['driver'].lower()} {d['effect_pct']:+.0f}%" for d in top if round(d["effect_pct"]) != 0]
    if not parts:
        return "Forecast is in line with the recent run-rate."
    return "Forecast drivers: " + ", ".join(parts) + "."


class Toolbox:
    def __init__(self, ws: Workspace) -> None:
        self.ws = ws
        self.tools: dict[str, Tool] = {}
        self._register()

    # ---- plumbing --------------------------------------------------------------------------
    def specs(self) -> list[dict]:
        return [t.spec() for t in self.tools.values()]

    def call(self, name: str, args: dict[str, Any] | None = None) -> dict:
        if name not in self.tools:
            raise KeyError(f"unknown tool {name!r}")
        return self.tools[name].fn(**(args or {}))

    def _add(self, name: str, description: str, schema: dict, fn: Callable[..., dict]) -> None:
        self.tools[name] = Tool(name, description, schema, fn)

    def _register(self) -> None:
        self._add(
            "network_overview",
            "Network status on the as-of date: demand over the last 28 days and the forecast for the next 28, "
            "by store and by category, plus the events coming up. Start here.",
            _obj(),
            self.network_overview,
        )
        self._add(
            "scan_anomalies",
            "Recent demand anomalies at network, store and product level, each with its known cause or marked "
            "unexplained. Unexplained spikes can inflate a product's run-rate and its forecast.",
            _obj({"days": {"type": "integer", "description": "Look-back window in days (default 21)."}}),
            self.scan_anomalies,
        )
        self._add(
            "scan_trends",
            "Values of any catalog attribute (style, colour, fit, fabric and so on) whose share of demand is "
            "moving, with statistical significance and, when a buzz table was supplied, the outside interest "
            "that leads sales.",
            _obj({"status": {"type": "string", "enum": ["Emerging", "Rising", "Peaking", "Fading"]}}),
            self.scan_trends,
        )
        self._add(
            "scan_stockout_risk",
            "Products most likely to run out, ranked by expected lost revenue. horizon 'week' covers the days "
            "before a supplier order can arrive; 'cycle' covers the full 14-day protection interval.",
            _obj(
                {
                    "horizon": {"type": "string", "enum": ["week", "cycle"]},
                    "limit": {"type": "integer"},
                    "store": {"type": "string", "description": "Optional store name to filter by."},
                }
            ),
            self.scan_stockout_risk,
        )
        self._add(
            "explain_forecast",
            "Forecast for one product in one store: run-rate, forecast with interval, what drives it, and the "
            "size-level stock position.",
            _obj({"store": {"type": "string"}, "sku_id": {"type": "string"}}, ["store", "sku_id"]),
            self.explain_forecast,
        )
        self._add(
            "find_overstock",
            "Products holding far more stock than forecast demand needs: candidates for markdown or for "
            "moving to a store that sells them.",
            _obj({"limit": {"type": "integer"}}),
            self.find_overstock,
        )
        self._add(
            "draft_plan",
            "Draft an inventory plan: store-to-store transfers (optimised) then supplier orders up to the "
            "service-level target. Pass a budget, in the data's currency, to cap supplier spend; units are then "
            "bought in order of expected margin per unit of spend. Replaces any earlier draft.",
            _obj(
                {
                    "budget": {
                        "type": ["number", "null"],
                        "description": "Supplier spend cap in the data's currency, or null.",
                    },
                    "use_transfers": {"type": "boolean"},
                    "service_shift": {
                        "type": "number",
                        "description": "Add this to every service level (e.g. -0.05 to buy less).",
                    },
                }
            ),
            self.draft_plan,
        )
        self._add(
            "check_guardrails",
            "Check the current draft against the operating guardrails (budget, donor protection, lane "
            "capacity, forecast sanity). A plan with a failed check cannot be committed.",
            _obj(),
            self.check_guardrails,
        )
        self._add(
            "commit_plan",
            "Commit the draft. Lines within limits are approved automatically; large or uncertain lines are "
            "queued for human review. Fails if a guardrail check fails.",
            _obj(),
            self.commit_plan,
        )

    # ---- read tools ------------------------------------------------------------------------
    def network_overview(self) -> dict:
        ws, p, fc = self.ws, self.ws.panel, self.ws.forecast
        demand = ws.uncensored.by_sku[-28:].sum(axis=0)  # [S, K]
        sales = p.sales[-28:].sum(axis=(0, 3))
        mean28 = np.nan_to_num(fc.window(1, 28)[0], nan=0.0)
        price = p.price
        stores = []
        for s, name in enumerate(ws.store_names):
            stores.append(
                {
                    "store": name,
                    "weeks_open": int((p.n_days - max(int(p.open_day[s]), 0)) // 7),
                    "demand_last_28d": round(float(demand[s].sum())),
                    "forecast_next_28d": round(float(mean28[s].sum())),
                    "change_pct": round(100.0 * (mean28[s].sum() / max(demand[s].sum(), 1e-9) - 1.0), 1),
                    "stock_units": int(p.on_hand[s].sum()) if p.has_stock else None,
                }
            )
        cats = []
        for c, key in enumerate(p.categories):
            m = p.category_code == c
            cats.append(
                {
                    "category": p.category_label(key),
                    "demand_last_28d": round(float(demand[:, m].sum())),
                    "forecast_next_28d": round(float(mean28[:, m].sum())),
                    "change_pct": round(100.0 * (mean28[:, m].sum() / max(demand[:, m].sum(), 1e-9) - 1.0), 1),
                }
            )
        cal = p.calendar.iloc[p.n_days : p.n_days + 28]
        events = [e for e in dict.fromkeys(cal["event"]) if e and not e.startswith("After ")]
        lost = float(demand.sum() - sales.sum())
        return {
            "as_of": p.asof.isoformat(),
            "stores": stores,
            "categories": cats,
            "demand_last_28d": round(float(demand.sum())),
            "forecast_next_28d": round(float(mean28.sum())),
            "forecast_revenue_next_28d": round(float((mean28 * price[None, :]).sum())),
            "estimated_lost_to_stockouts_last_28d_pct": (
                round(100.0 * lost / max(float(demand.sum()), 1e-9), 1) if p.has_stock_history else None
            ),
            "events_next_28d": events,
            "stock_value": (
                round(float((p.on_hand.sum(axis=-1) * p.unit_cost[None, :]).sum())) if p.has_stock else None
            ),
        }

    def scan_anomalies(self, days: int = 21) -> dict:
        p = self.ws.panel
        recent = [a for a in self.ws.anomalies if a.end >= p.n_days - days]
        rows = [
            {
                "level": a.scope,
                "name": a.name,
                "date": p.date_of(a.start).isoformat(),
                "days": a.end - a.start + 1,
                "direction": a.direction,
                "actual_units": round(a.actual),
                "expected_units": round(a.expected),
                "cause": a.explained_by or "unexplained",
            }
            for a in recent
        ]
        return {
            "window_days": days,
            "count": len(rows),
            "unexplained": sum(r["cause"] == "unexplained" for r in rows),
            "anomalies": rows[:20],
        }

    def scan_trends(self, status: str | None = None) -> dict:
        signals = [t for t in self.ws.trends if t.status != "Stable" and (status is None or t.status == status)]
        return {
            "count": len(signals),
            "trends": [
                {
                    "attribute": t.attribute,
                    "value": t.value,
                    "status": t.status,
                    "share_change_4w_pct": round(100.0 * t.change_4w, 1),
                    "slope_pct_per_week": round(t.slope_pct_per_week, 1),
                    "q_value": round(t.q_value, 3),
                    "buzz_momentum_pct": None if t.buzz_momentum is None else round(100.0 * t.buzz_momentum, 1),
                    "strongest_in": t.leading_stores,
                }
                for t in signals
            ],
        }

    def risk_table(self, plan: InventoryPlan | None = None, horizon: str = "week") -> list[dict]:
        """Every pair's stockout exposure, ranked by expected lost revenue."""
        ws, p = self.ws, self.ws.panel
        view = (plan or self._view_only()).view
        short = horizon == "week"
        mean = view.mean_short if short else view.mean_long
        shape = view.shape_short if short else view.shape_long
        stock = view.on_hand + (view.inbound_soon if short else view.inbound)
        risk = view.risk_short if short else view.risk_long
        from luma.models.pipeline import expected_shortage

        shortage = np.where(view.active[..., None], expected_shortage(stock, mean, shape), 0.0)
        lost_rev = shortage.sum(axis=-1) * p.price[None, :]
        rows = []
        for s, k in zip(*np.nonzero(view.active & (lost_rev > 0)), strict=True):
            rows.append(
                {
                    "store": ws.store_names[s],
                    "sku_id": str(p.catalog["sku_id"].iloc[k]),
                    "product": str(p.catalog["name"].iloc[k]),
                    "max_size_risk": round(float(risk[s, k].max()), 3),
                    "expected_lost_units": round(float(shortage[s, k].sum()), 2),
                    "expected_lost_revenue": round(float(lost_rev[s, k])),
                    "expected_demand": round(float(mean[s, k].sum()), 1),
                    "on_hand": int(view.on_hand[s, k].sum()),
                    "sizes_out": [p.sizes[z] for z in range(p.n_sizes) if stock[s, k, z] == 0],
                    "_s": int(s),
                    "_k": int(k),
                }
            )
        rows.sort(key=lambda r: -r["expected_lost_revenue"])
        return rows

    def _view_only(self) -> InventoryPlan:
        """A plan object used only for its stock view (cached)."""
        if "view_plan" not in self.ws.notes:
            ws = self.ws
            self.ws.notes["view_plan"] = decide(
                ws.panel, ws.forecast, ws.size_curves.share, ws.distance, ws.settings.policy, use_transfers=False
            )
        return self.ws.notes["view_plan"]

    def movers(self, limit: int = 5) -> list[dict]:
        """Pairs whose 28-day forecast differs most from their last 28 days, by units."""
        ws, p, fc = self.ws, self.ws.panel, self.ws.forecast
        past = ws.uncensored.by_sku[-28:].sum(axis=0)
        ahead = np.nan_to_num(fc.window(1, 28)[0], nan=0.0)
        delta = np.where(fc.active, ahead - past, 0.0)
        order = np.argsort(-np.abs(delta), axis=None)[:limit]
        rows = []
        for s, k in zip(*np.unravel_index(order, delta.shape), strict=True):
            rows.append(
                {
                    "store": ws.store_names[s],
                    "sku_id": str(p.catalog["sku_id"].iloc[k]),
                    "product": str(p.catalog["name"].iloc[k]),
                    "last_28d": round(float(past[s, k]), 1),
                    "next_28d": round(float(ahead[s, k]), 1),
                }
            )
        return rows

    def _needs_stock(self) -> dict | None:
        if self.ws.panel.has_stock:
            return None
        return {
            "error": "No stock table was supplied, so stock risk and order plans are unavailable. "
            "Add a stock table with sku_id and on_hand."
        }

    def scan_stockout_risk(self, horizon: str = "week", limit: int = 10, store: str | None = None) -> dict:
        if (missing := self._needs_stock()) is not None:
            return missing
        rows = self.risk_table(horizon=horizon)
        if store:
            s = self.ws.store_index(store)
            rows = [r for r in rows if r["_s"] == s]
        view = self._view_only().view
        risk = view.risk_short if horizon == "week" else view.risk_long
        return {
            "horizon_days": view.horizon_short if horizon == "week" else view.horizon_long,
            "cells_above_50pct_risk": int((risk > 0.5).sum()),
            "expected_lost_revenue_total": round(sum(r["expected_lost_revenue"] for r in rows)),
            "top": [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows[:limit]],
        }

    def explain_forecast(self, store: str, sku_id: str) -> dict:
        ws, p, fc = self.ws, self.ws.panel, self.ws.forecast
        s, k = ws.store_index(store), ws.sku_index(sku_id)
        if not fc.active[s, k]:
            return {"error": f"{sku_id} is not live in {ws.store_names[s]}"}
        view = self._view_only().view
        windows = {}
        for n in (7, 14, 28):
            mean, q = fc.window(1, n)
            windows[f"next_{n}d"] = {
                "expected": round(float(mean[s, k]), 1),
                "p10": round(float(q[s, k, Q10]), 1),
                "p90": round(float(q[s, k, Q90]), 1),
            }
        attr = p.trend_attribute
        value = str(p.catalog[attr].iloc[k]) if attr else None
        trend = next((t.status for t in ws.trends if t.attribute == attr and t.value == value), "Stable")
        spikes = [
            a
            for a in ws.anomalies
            if a.scope == "product" and a.entity == k and a.end >= p.n_days - 14 and a.explained_by is None
        ]
        return {
            "store": ws.store_names[s],
            "sku_id": sku_id,
            "product": str(p.catalog["name"].iloc[k]),
            "trend_attribute": attr or None,
            "trend_value": value,
            "trend_status": trend,
            "days_live_here": int(p.n_days - p.start_day[s, k]),
            "run_rate_per_day": round(float(fc.level[s, k]), 2),
            "sold_last_7d": int(p.sales[-7:, s, k].sum()),
            "forecast": windows,
            "drivers": driver_effects(ws, s, k),
            "sizes": [
                {
                    "size": p.sizes[z],
                    "share_pct": round(100.0 * float(ws.size_curves.share[s, k, z]), 1),
                    "expected_14d": round(float(view.mean_long[s, k, z]), 1),
                    **(
                        {
                            "on_hand": int(view.on_hand[s, k, z]),
                            "inbound": int(view.inbound[s, k, z]),
                            "stockout_risk_14d": round(float(view.risk_long[s, k, z]), 2),
                        }
                        if p.has_stock
                        else {}
                    ),
                }
                for z in range(p.n_sizes)
            ],
            "caution": "Unexplained demand spike in the last 14 days; the run-rate may be inflated."
            if spikes
            else None,
        }

    def find_overstock(self, limit: int = 10) -> dict:
        if (missing := self._needs_stock()) is not None:
            return missing
        ws, p = self.ws, self.ws.panel
        view = self._view_only().view
        weeks = np.where(view.active, view.cover_days / 7.0, 0.0)
        value = view.on_hand.sum(axis=-1) * p.unit_cost[None, :]
        over = view.active & (weeks > ws.settings.policy.overstock_weeks) & (view.on_hand.sum(axis=-1) >= 6)
        rows = []
        for s, k in zip(*np.nonzero(over), strict=True):
            attr = p.trend_attribute
            value = str(p.catalog[attr].iloc[k]) if attr else None
            fading = any(t.attribute == attr and t.value == value and t.status == "Fading" for t in ws.trends)
            rows.append(
                {
                    "store": ws.store_names[s],
                    "sku_id": str(p.catalog["sku_id"].iloc[k]),
                    "product": str(p.catalog["name"].iloc[k]),
                    "on_hand": int(view.on_hand[s, k].sum()),
                    "weeks_of_cover": round(float(weeks[s, k]), 1),
                    "stock_value": round(float(value[s, k])),
                    "suggestion": f"Mark down: {value} is fading" if fading else "Hold orders; consider markdown",
                    "_s": int(s),
                    "_k": int(k),
                }
            )
        rows.sort(key=lambda r: -r["stock_value"])
        self.ws.notes["overstock"] = rows
        return {
            "pairs": len(rows),
            "stock_value_total": round(sum(r["stock_value"] for r in rows)),
            "top": [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows[:limit]],
        }

    # ---- planning tools --------------------------------------------------------------------
    def draft_plan(self, budget: float | None = None, use_transfers: bool = True, service_shift: float = 0.0) -> dict:
        if (missing := self._needs_stock()) is not None:
            return missing
        ws = self.ws
        use_transfers = use_transfers and ws.can_transfer
        base = [v for _, v in ws.settings.policy.service_levels]
        levels = tuple(float(np.clip(v + service_shift, 0.5, 0.995)) for v in base)
        plan = decide(
            ws.panel,
            ws.forecast,
            ws.size_curves.share,
            ws.distance,
            ws.settings.policy,
            service_levels=levels,
            use_transfers=use_transfers,
            budget=budget,
        )
        ws.draft = plan
        ws.committed = None
        return self.plan_summary(plan)

    def plan_summary(self, plan: InventoryPlan) -> dict:
        t, r, i = plan.transfers, plan.replenishment, plan.impact
        return {
            "service_levels": {"A": plan.service_levels[0], "B": plan.service_levels[1], "C": plan.service_levels[2]},
            "transfers": {
                "units": t.units,
                "lanes": len(t.lanes),
                "cost": round(t.cost),
                "expected_margin_recovered": round(t.expected_gain),
                "solver": t.status,
                "solve_seconds": round(t.solve_seconds, 1),
            },
            "supplier_orders": {
                "units": r.units,
                "lines": r.lines,
                "cost": round(r.cost),
                "cost_without_budget": round(r.unconstrained_cost),
                "budget": None if r.budget is None else round(r.budget),
                "budget_binding": r.budget_binding,
            },
            "expected_impact": {
                "lost_units_this_week": [round(i.lost_units_week_before), round(i.lost_units_week_after)],
                "lost_units_14d": [round(i.lost_units_cycle_before), round(i.lost_units_cycle_after)],
                "revenue_protected": round(i.revenue_protected),
                "cells_above_50pct_risk": [i.cells_at_risk_before, i.cells_at_risk_after],
            },
        }

    def check_guardrails(self) -> dict:
        ws, plan = self.ws, self.ws.draft
        if plan is None:
            return {
                "passed": False,
                "checks": [{"check": "Draft exists", "status": "fail", "detail": "No draft plan."}],
            }
        policy, p = ws.settings.policy, ws.panel
        checks = []

        def add(name: str, status: str, detail: str) -> None:
            checks.append({"check": name, "status": status, "detail": detail})

        def full(amount: float) -> str:
            return money(amount, p.currency, compact=False)

        cost = plan.replenishment.cost
        if cost <= policy.replenishment_budget:
            add("Supplier budget", "pass", f"{full(cost)} of {full(policy.replenishment_budget)}.")
        else:
            add(
                "Supplier budget",
                "fail",
                f"{full(cost)} exceeds the {full(policy.replenishment_budget)} cap by "
                f"{full(cost - policy.replenishment_budget)}. Redraft with budget={policy.replenishment_budget:.0f}.",
            )

        moves = plan.transfers.moves
        if len(moves):
            view = plan.view
            after = plan.transfers.apply(view.on_hand + view.inbound_soon)
            src, _, sku, size, _ = moves.T
            donor_risk = nb_sf(after[src, sku, size], view.mean_short[src, sku, size], view.shape_short[src, sku, size])
            exposed = int((donor_risk > 0.5).sum())
            add(
                "Donor stores stay protected",
                "pass" if exposed == 0 else "fail",
                "No donor is left with more than a 50% stockout risk this week."
                if exposed == 0
                else f"{exposed} transfer lines leave the donor above 50% stockout risk. Redraft without transfers.",
            )
            worst = max((lane["units"] for lane in plan.transfers.lanes), default=0)
            cap = policy.lane_capacity * policy.max_trips_per_lane
            add(
                "Trip capacity per lane",
                "pass" if worst <= cap else "fail",
                f"Busiest lane carries {worst} of {cap} units.",
            )
            if plan.transfers.status != "optimal":
                add("Transfer optimiser", "warn", f"Stopped at the time limit with a {plan.transfers.gap:.1%} gap.")
            else:
                add("Transfer optimiser", "pass", "Solved to optimality.")

        demand = float(ws.uncensored.by_sku[-28:].sum())
        ahead = float(np.nansum(ws.forecast.window(1, 28)[0]))
        ratio = ahead / max(demand, 1e-9)
        add(
            "Forecast sanity",
            "pass" if 0.6 <= ratio <= 1.6 else "warn",
            f"Next 28 days forecast at {ratio:.0%} of the last 28 days' demand.",
        )
        add(
            "Calibrated intervals",
            "pass" if ws.model.offsets else "warn",
            "Quantiles calibrated on past forecast errors."
            if ws.model.offsets
            else "No past errors to calibrate on yet.",
        )
        spiked = {
            a.entity
            for a in ws.anomalies
            if a.scope == "product" and a.explained_by is None and a.direction == "spike" and a.end >= p.n_days - 14
        }
        lines = int((plan.replenishment.orders[:, sorted(spiked), :].sum(axis=-1) > 0).sum()) if spiked else 0
        add(
            "Anomaly exposure",
            "warn" if lines else "pass",
            f"{lines} order lines are for products with an unexplained recent spike; routed to review."
            if lines
            else "No order depends on an unexplained spike.",
        )
        ws.notes["spiked_products"] = sorted(spiked)
        return {"passed": all(c["status"] != "fail" for c in checks), "checks": checks}

    def commit_plan(self) -> dict:
        ws, plan = self.ws, self.ws.draft
        report = self.check_guardrails()
        if plan is None or not report["passed"]:
            failed = [c["check"] for c in report["checks"] if c["status"] == "fail"]
            return {"committed": False, "error": f"Guardrail failed: {', '.join(failed)}. Repair the draft first."}
        p, view, policy = ws.panel, plan.view, ws.settings.policy
        spiked = set(ws.notes.get("spiked_products", []))
        orders = plan.replenishment.orders
        final = plan.position_after
        risk_after = np.where(view.active[..., None], nb_sf(final, view.mean_long, view.shape_long), 0.0)
        lines = []
        for s, k in zip(*np.nonzero(orders.sum(axis=-1) > 0), strict=True):
            units = orders[s, k]
            cost = float(units.sum() * p.unit_cost[k])
            review = None
            if cost > policy.approval_threshold:
                review = f"Line above {money(policy.approval_threshold, p.currency, compact=False)}"
            elif k in spiked:
                review = "Unexplained demand spike in the last 14 days"
            elif p.n_days - p.start_day[s, k] < 14:
                review = "Under two weeks of sales history"
            lines.append(
                {
                    "store": ws.store_names[s],
                    "sku_id": str(p.catalog["sku_id"].iloc[k]),
                    "product": str(p.catalog["name"].iloc[k]),
                    "category": p.category_label(str(p.catalog["category"].iloc[k])),
                    "class": "ABC"[int(view.abc[s, k])],
                    "sizes": {p.sizes[z]: int(units[z]) for z in range(p.n_sizes) if units[z] > 0},
                    "units": int(units.sum()),
                    "cost": round(cost),
                    "on_hand": int(view.on_hand[s, k].sum()),
                    "expected_14d": round(float(view.mean_long[s, k].sum()), 1),
                    "risk_before": round(float(view.risk_long[s, k].max()), 2),
                    "risk_after": round(float(risk_after[s, k].max()), 2),
                    "reason": reason_for(ws, s, k),
                    "approval": "review" if review else "auto",
                    "review_reason": review,
                }
            )
        lines.sort(key=lambda r: (-r["risk_before"], -r["cost"]))
        moves = []
        for src, dst, k, z, units in plan.transfers.moves.tolist():
            moves.append(
                {
                    "from": ws.store_names[src],
                    "to": ws.store_names[dst],
                    "sku_id": str(p.catalog["sku_id"].iloc[k]),
                    "product": str(p.catalog["name"].iloc[k]),
                    "size": p.sizes[z],
                    "units": units,
                    "donor_keeps": int(view.on_hand[src, k, z] - units),
                    "receiver_risk_before": round(float(view.risk_short[dst, k, z]), 2),
                }
            )
        moves.sort(key=lambda m: -m["receiver_risk_before"])
        review = [r for r in lines if r["approval"] == "review"]
        ws.committed = {
            "orders": lines,
            "transfers": moves,
            "lanes": [
                {
                    "from": ws.store_names[lane["src"]],
                    "to": ws.store_names[lane["dst"]],
                    "units": lane["units"],
                    "km": round(lane["km"], 1),
                    "cost": round(lane["cost"]),
                }
                for lane in plan.transfers.lanes
            ],
            "summary": self.plan_summary(plan),
            "guardrails": report["checks"],
        }
        return {
            "committed": True,
            "order_lines": len(lines),
            "auto_approved": len(lines) - len(review),
            "queued_for_review": len(review),
            "review_value": round(sum(r["cost"] for r in review)),
            "transfer_moves": len(moves),
            "transfer_lanes": len(plan.transfers.lanes),
        }
