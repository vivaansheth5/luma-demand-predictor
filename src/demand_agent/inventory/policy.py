"""Stock risk and replenishment.

The forecast is a distribution, so every stock question has a probabilistic answer.

* Stockout risk of a cell is the probability that demand over the coming days exceeds what the
  store holds.
* The order-up-to level is the demand quantile at the cell's service level over the protection
  interval (supplier lead time plus the gap until the next order). Fast, high-revenue lines get
  a higher service level than slow ones, because the cost of an empty shelf scales with sales.
* When the budget cannot fund every order, units are bought in order of expected margin earned
  per rupee spent. The n-th extra unit of a cell only earns its margin if demand reaches it, so
  its value is ``margin * P(demand >= position + n)``; taking units in descending order of that
  ratio is the optimal way to spend a fixed budget on independent cells.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from demand_agent.config import PolicySettings
from demand_agent.data.panel import Panel
from demand_agent.models.pipeline import Forecast, expected_shortage, nb_ppf, nb_sf


@dataclass
class StockView:
    """Size-level demand distribution and stock position for every cell, ``[S, K, Z]``."""

    on_hand: np.ndarray
    inbound: np.ndarray  # all units on the way
    inbound_soon: np.ndarray  # units arriving within three days
    mean_short: np.ndarray  # expected demand over the transfer horizon
    shape_short: np.ndarray  # negative-binomial shape (per pair, broadcast over sizes)
    mean_long: np.ndarray  # expected demand over the protection interval
    shape_long: np.ndarray
    risk_short: np.ndarray  # P(stockout before a supplier order could arrive)
    risk_long: np.ndarray  # P(stockout within the protection interval at the current position)
    shortage: np.ndarray  # expected units of unmet demand over the protection interval
    cover_days: np.ndarray  # [S, K] days of stock at the forecast rate
    abc: np.ndarray  # [S, K] 0 = A, 1 = B, 2 = C
    service_level: np.ndarray  # [S, K]
    active: np.ndarray  # [S, K]
    margin: np.ndarray  # [K] price net of current discount minus unit cost
    horizon_short: int
    horizon_long: int

    @property
    def position(self) -> np.ndarray:
        return self.on_hand + self.inbound


def classify_abc(revenue: np.ndarray, active: np.ndarray, cuts: tuple[float, float]) -> np.ndarray:
    """ABC class per (store, product) by share of each store's forecast revenue."""
    abc = np.full(revenue.shape, 2, dtype=np.int64)
    for s in range(revenue.shape[0]):
        r = np.where(active[s], revenue[s], 0.0)
        order = np.argsort(-r)
        share = np.cumsum(r[order]) / max(r.sum(), 1e-9)
        abc[s, order[share <= cuts[0]]] = 0
        abc[s, order[(share > cuts[0]) & (share <= cuts[1])]] = 1
        if len(order):
            abc[s, order[0]] = 0
    return abc


def build_stock_view(
    panel: Panel,
    forecast: Forecast,
    size_share: np.ndarray,
    settings: PolicySettings,
    service_levels: tuple[float, float, float] | None = None,
) -> StockView:
    """Combine the forecast, the size curves and the stock position into cell-level risk."""
    h_short, h_long = settings.transfer_horizon_days, settings.protection_days
    mean_s, shape_s = forecast.negbin(1, h_short)
    mean_l, shape_l = forecast.negbin(1, h_long)
    active = forecast.active
    live = active[..., None]

    def by_size(mean: np.ndarray, shape: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        return np.where(live, mean[..., None] * size_share, 1e-6), np.broadcast_to(shape[..., None], size_share.shape)

    mean_short, shape_short = by_size(mean_s, shape_s)
    mean_long, shape_long = by_size(mean_l, shape_l)
    on_hand = panel.on_hand.astype(np.int64)
    inbound = panel.pipeline.sum(axis=0).astype(np.int64)
    inbound_soon = panel.pipeline[:3].sum(axis=0).astype(np.int64)

    risk_short = np.where(live, nb_sf(on_hand + inbound_soon, mean_short, shape_short), 0.0)
    risk_long = np.where(live, nb_sf(on_hand + inbound, mean_long, shape_long), 0.0)
    shortage = np.where(live, expected_shortage(on_hand + inbound, mean_long, shape_long), 0.0)

    discount = panel.discount[forecast.origin + 1 : forecast.origin + 1 + h_long].mean(axis=0)
    price_now = panel.price * (1.0 - discount)
    margin = price_now - panel.unit_cost
    mean28, _ = forecast.window(1, 28)
    revenue = np.nan_to_num(mean28, nan=0.0) * price_now[None, :]
    abc = classify_abc(revenue, active, settings.abc_cuts)
    levels = np.array(service_levels if service_levels is not None else [v for _, v in settings.service_levels])
    daily = np.maximum(np.nan_to_num(mean_l, nan=0.0) / h_long, 1e-6)
    cover = np.where(active, on_hand.sum(axis=-1) / daily, np.nan)
    return StockView(
        on_hand=on_hand,
        inbound=inbound,
        inbound_soon=inbound_soon,
        mean_short=mean_short,
        shape_short=shape_short,
        mean_long=mean_long,
        shape_long=shape_long,
        risk_short=risk_short,
        risk_long=risk_long,
        shortage=shortage,
        cover_days=cover,
        abc=abc,
        service_level=levels[abc],
        active=active,
        margin=margin,
        horizon_short=h_short,
        horizon_long=h_long,
    )


@dataclass
class ReplenishmentPlan:
    orders: np.ndarray  # [S, K, Z] units to order
    target: np.ndarray  # [S, K, Z] order-up-to level before any budget cut
    position: np.ndarray  # [S, K, Z] stock position the plan started from
    units: int
    cost: float
    unconstrained_cost: float
    budget: float | None
    budget_binding: bool
    marginal_cutoff: float | None  # expected margin per rupee of the last unit bought

    @property
    def lines(self) -> int:
        """Order lines: one per (store, product) with any size ordered."""
        return int((self.orders.sum(axis=-1) > 0).sum())


def plan_replenishment(
    view: StockView,
    unit_cost: np.ndarray,
    position: np.ndarray | None = None,
    budget: float | None = None,
) -> ReplenishmentPlan:
    """Order every cell up to its service-level quantile, within budget.

    ``position`` overrides the stock position (for example after transfers have been planned).
    """
    position = view.position if position is None else position
    live = view.active[..., None]
    level = np.broadcast_to(view.service_level[..., None], position.shape)
    target = np.where(live, nb_ppf(level, view.mean_long, view.shape_long), 0).astype(np.int64)
    # Keep at least one unit of every live size on the shelf: a missing size sells nothing.
    target = np.where(live, np.maximum(target, 1), 0)
    need = np.maximum(target - position, 0)
    cost_cell = np.broadcast_to(unit_cost[None, :, None], position.shape)
    full_cost = float((need * cost_cell).sum())

    if budget is None or full_cost <= budget:
        return ReplenishmentPlan(
            orders=need,
            target=target,
            position=position,
            units=int(need.sum()),
            cost=full_cost,
            unconstrained_cost=full_cost,
            budget=budget,
            budget_binding=False,
            marginal_cutoff=None,
        )

    # Budget binds: rank every candidate unit by expected margin per rupee and buy from the top.
    s, k, z = np.nonzero(need)
    reps = need[s, k, z]
    cell = np.repeat(np.arange(len(s)), reps)
    nth = np.concatenate([np.arange(1, r + 1) for r in reps])
    cs, ck, cz = s[cell], k[cell], z[cell]
    p_sell = nb_sf(position[cs, ck, cz] + nth - 1, view.mean_long[cs, ck, cz], view.shape_long[cs, ck, cz])
    value = p_sell * np.maximum(view.margin[ck], 0.0) / unit_cost[ck]
    order = np.argsort(-value, kind="stable")
    spend = np.cumsum(unit_cost[ck][order])
    take = order[spend <= budget]
    orders = np.zeros_like(need)
    np.add.at(orders, (cs[take], ck[take], cz[take]), 1)
    return ReplenishmentPlan(
        orders=orders,
        target=target,
        position=position,
        units=int(orders.sum()),
        cost=float((orders * cost_cell).sum()),
        unconstrained_cost=full_cost,
        budget=budget,
        budget_binding=True,
        marginal_cutoff=float(value[take[-1]]) if len(take) else None,
    )


def expected_lost_units(view: StockView, position: np.ndarray, horizon: str = "long") -> np.ndarray:
    """Expected unmet demand per cell at ``position`` over the short or long horizon."""
    mean, shape = (view.mean_long, view.shape_long) if horizon == "long" else (view.mean_short, view.shape_short)
    return np.where(view.active[..., None], expected_shortage(position, mean, shape), 0.0)
