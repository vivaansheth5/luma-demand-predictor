"""Closed-loop policy evaluation.

Forecast accuracy is a means. The end is fewer lost sales for the same money tied up in stock,
and that can only be measured by letting a policy run. The simulator holds 28 days of true
demand that no policy has seen. Each policy starts from the same shelves on the as-of evening,
places its own orders every Sunday, and sells into the same demand.

The agent inside the loop is the real one: every Sunday it sees only the sales and stock its own
decisions produced, uncensors them, re-forecasts with the model trained at the as-of date, and
re-plans. Nothing is retrained and nothing from the future leaks in.

One run can always look good by simply buying more. So both policies are swept across their one
dial (days of cover for the status quo, service level for the agent) to trace how much service
each buys per rupee of inventory. A better policy is one whose whole curve sits higher.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass

import numpy as np

from demand_agent.config import PolicySettings, Settings
from demand_agent.data.panel import Panel
from demand_agent.features.build import FeatureBuilder
from demand_agent.inventory.baseline import BaselinePolicy
from demand_agent.inventory.decide import decide
from demand_agent.inventory.engine import Decision, InventoryState, PolicyContext, RunLog, run_inventory
from demand_agent.models.gbm import DemandForecaster
from demand_agent.models.pipeline import Forecast, make_forecast
from demand_agent.models.uncensor import uncensor
from demand_agent.sim.catalog import distance_matrix
from demand_agent.sim.world import Truth, history_log

HOLDING_RATE = 0.24  # annual cost of holding stock, as a share of its cost value


@dataclass
class PolicyResult:
    name: str
    kind: str  # "baseline" or "agent"
    dial: str
    fill_rate: float
    demand_units: int
    sold_units: int
    lost_units: int
    revenue: float
    lost_revenue: float
    gross_margin: float
    avg_stock_units: float
    avg_stock_value: float
    end_stock_value: float
    stockout_share: float  # share of live cell-days that ended empty
    units_ordered: int
    transfer_units: int
    transfer_trips: int
    transfer_cost: float
    holding_cost: float
    net_contribution: float  # gross margin - transfer cost - holding cost
    daily_fill: list[float]
    daily_stock: list[float]


class AgentPolicy:
    """The agent as an inventory policy: re-forecast and re-plan on every review day."""

    def __init__(
        self,
        panel: Panel,
        model: DemandForecaster,
        size_share: np.ndarray,
        truth: Truth,
        settings: Settings,
        *,
        service_levels: tuple[float, float, float] | None = None,
        use_transfers: bool = True,
        first_forecast: Forecast | None = None,
        review_weekday: int = 6,
        budget: float | None = None,
    ) -> None:
        self.panel = panel
        self.model = model
        self.size_share = size_share
        self.truth = truth
        self.settings = settings
        self.service_levels = service_levels
        self.use_transfers = use_transfers
        self.first_forecast = first_forecast
        self.review_weekday = review_weekday
        self.budget = budget
        self.distance = distance_matrix(panel.stores)
        self.transfer_cost = 0.0
        self.plans: list = []

    def _panel_at(self, ctx: PolicyContext) -> Panel:
        new = ctx.day + 1
        if new == 0:
            return self.panel
        p, t = ctx.log.prefix, self.panel.n_days
        return self.panel.extended(
            sales=ctx.log.sales[p : p + new],
            sod=ctx.log.sod[p : p + new],
            eod=ctx.log.eod[p : p + new],
            on_hand=ctx.state.on_hand.copy(),
            pipeline=ctx.state.pipeline.copy(),
            buzz=self.truth.buzz[t : t + new],
            weather=self.truth.weather.iloc[t : t + new],
        )

    def __call__(self, ctx: PolicyContext) -> Decision | None:
        if ctx.weekday != self.review_weekday:
            return None
        panel = self._panel_at(ctx)
        if ctx.day == -1 and self.first_forecast is not None:
            forecast = self.first_forecast
        else:
            unc = uncensor(panel)
            builder = FeatureBuilder(panel, unc.by_sku, self.settings.model.level_prior_days)
            forecast = make_forecast(self.model, builder)
        plan = decide(
            panel,
            forecast,
            self.size_share,
            self.distance,
            self.settings.policy,
            service_levels=self.service_levels,
            use_transfers=self.use_transfers,
            budget=self.budget,
        )
        self.transfer_cost += plan.transfers.cost
        self.plans.append(plan)
        return Decision(orders=plan.replenishment.orders.astype(np.int32), transfers=plan.transfers.moves)


def measure(
    name: str,
    kind: str,
    dial: str,
    log: RunLog,
    demand: np.ndarray,
    panel: Panel,
    transfer_cost: float = 0.0,
) -> PolicyResult:
    """Score one run against true demand."""
    sales = log.window("sales").astype(float)
    eod = log.window("eod").astype(float)
    n_days = demand.shape[0]
    t = panel.n_days
    price = panel.price[None, :] * (1.0 - panel.discount[t : t + n_days])  # [D, K]
    cost = panel.unit_cost
    lost = demand - sales
    revenue = float((sales.sum(axis=(1, 3)) * price).sum())
    lost_revenue = float((lost.sum(axis=(1, 3)) * price).sum())
    margin = float((sales.sum(axis=(1, 3)) * (price - cost[None, :])).sum())
    stock_value = (eod.sum(axis=(1, 3)) * cost[None, :]).sum(axis=1)  # [D]
    holding = float(stock_value.mean() * HOLDING_RATE * n_days / 365.0)
    live = (panel.start_day <= t)[None, :, :, None]
    ordered = int(log.orders.sum()) + (int(log.first_orders.sum()) if log.first_orders is not None else 0)
    day_demand = demand.sum(axis=(1, 2, 3))
    return PolicyResult(
        name=name,
        kind=kind,
        dial=dial,
        fill_rate=float(sales.sum() / max(demand.sum(), 1)),
        demand_units=int(demand.sum()),
        sold_units=int(sales.sum()),
        lost_units=int(lost.sum()),
        revenue=revenue,
        lost_revenue=lost_revenue,
        gross_margin=margin,
        avg_stock_units=float(eod.sum(axis=(1, 2, 3)).mean()),
        avg_stock_value=float(stock_value.mean()),
        end_stock_value=float(stock_value[-1]),
        stockout_share=float(((eod == 0) & live).sum() / max(np.broadcast_to(live, eod.shape).sum(), 1)),
        units_ordered=ordered,
        transfer_units=log.transfer_units,
        transfer_trips=log.transfer_trips,
        transfer_cost=transfer_cost,
        holding_cost=holding,
        net_contribution=margin - transfer_cost - holding,
        daily_fill=(sales.sum(axis=(1, 2, 3)) / np.maximum(day_demand, 1)).round(4).tolist(),
        daily_stock=eod.sum(axis=(1, 2, 3)).astype(int).tolist(),
    )


AGENT_DIALS: tuple[tuple[str, tuple[float, float, float]], ...] = (
    ("70%", (0.80, 0.70, 0.55)),
    ("80%", (0.88, 0.80, 0.68)),
    ("90%", (0.95, 0.90, 0.80)),
    ("95%", (0.975, 0.95, 0.88)),
    ("98%", (0.99, 0.975, 0.94)),
)
BASELINE_DIALS: tuple[float, ...] = (14.0, 17.5, 21.0, 24.5, 28.0, 35.0)


def run_policy_evaluation(
    panel: Panel,
    truth: Truth,
    model: DemandForecaster,
    forecast: Forecast,
    size_share: np.ndarray,
    settings: Settings,
    progress: Callable[[str], None] | None = None,
) -> dict:
    """Run the status quo and the agent over the hidden future and trace both frontiers."""
    demand = truth.future_demand
    n_days = demand.shape[0]
    t = panel.n_days
    prefix = history_log(panel, 56)
    first_weekday = (panel.start.weekday() + t) % 7
    state = InventoryState(panel.on_hand.astype(np.int32), panel.pipeline.astype(np.int32))
    policy_settings: PolicySettings = settings.policy
    default_levels = tuple(v for _, v in policy_settings.service_levels)

    def say(msg: str) -> None:
        if progress:
            progress(msg)

    def active(abs_day: int) -> np.ndarray:
        return panel.start_day <= t + (abs_day - prefix.sales.shape[0])

    results: list[PolicyResult] = []
    for cover in BASELINE_DIALS:
        say(f"status quo, {cover:g} days of cover")
        log, _ = run_inventory(
            demand, state, BaselinePolicy(cover, active), first_weekday=first_weekday, prefix=prefix, decide_first=True
        )
        is_default = cover == settings.sim.baseline_cover_days
        results.append(
            measure(
                "Status quo" if is_default else f"Status quo ({cover:g}d)",
                "baseline",
                f"{cover:g} days",
                log,
                demand,
                panel,
            )
        )

    for label, levels in AGENT_DIALS:
        say(f"agent without transfers, service level {label}")
        agent = AgentPolicy(
            panel,
            model,
            size_share,
            truth,
            settings,
            service_levels=levels,
            use_transfers=False,
            first_forecast=forecast,
        )
        log, _ = run_inventory(demand, state, agent, first_weekday=first_weekday, prefix=prefix, decide_first=True)
        results.append(measure(f"Agent, orders only, no budget ({label})", "agent_orders", label, log, demand, panel))

    budget = policy_settings.replenishment_budget
    say("agent without transfers, default service levels, weekly budget")
    agent = AgentPolicy(
        panel,
        model,
        size_share,
        truth,
        settings,
        service_levels=default_levels,
        use_transfers=False,
        first_forecast=forecast,
        budget=budget,
    )
    log, _ = run_inventory(demand, state, agent, first_weekday=first_weekday, prefix=prefix, decide_first=True)
    orders_only = measure("Agent, orders only", "agent_no_transfers", "90%", log, demand, panel)
    results.append(orders_only)

    say("agent with transfers, default service levels, weekly budget")
    agent = AgentPolicy(
        panel,
        model,
        size_share,
        truth,
        settings,
        service_levels=default_levels,
        use_transfers=True,
        first_forecast=forecast,
        budget=budget,
    )
    log, _ = run_inventory(demand, state, agent, first_weekday=first_weekday, prefix=prefix, decide_first=True)
    full = measure("Demand agent", "agent", "90%", log, demand, panel, transfer_cost=agent.transfer_cost)
    results.append(full)

    status_quo = next(r for r in results if r.name == "Status quo")
    # The status-quo setting that reaches the agent's fill rate, to compare stock needed for equal service.
    ladder = sorted((r for r in results if r.kind == "baseline"), key=lambda r: r.fill_rate)
    match = next((r for r in ladder if r.fill_rate >= full.fill_rate), ladder[-1])
    return {
        "horizon_days": n_days,
        "weekly_budget": budget,
        "results": [asdict(r) for r in results],
        "headline": {
            "status_quo": asdict(status_quo),
            "agent": asdict(full),
            "agent_orders_only": asdict(orders_only),
            "lost_revenue_cut": 1.0 - full.lost_revenue / max(status_quo.lost_revenue, 1e-9),
            "fill_rate_gain": full.fill_rate - status_quo.fill_rate,
            "stock_value_change": full.avg_stock_value / max(status_quo.avg_stock_value, 1e-9) - 1.0,
            "net_contribution_gain": full.net_contribution - status_quo.net_contribution,
            "transfer_contribution": full.net_contribution - orders_only.net_contribution,
            "same_service_baseline": asdict(match),
            "same_service_reached": match.fill_rate >= full.fill_rate,
        },
        "weekly_orders": [
            {
                "cost": p.replenishment.cost,
                "unconstrained": p.replenishment.unconstrained_cost,
                "transfer_units": p.transfers.units,
                "solver": p.transfers.status,
            }
            for p in agent.plans
        ],
    }
