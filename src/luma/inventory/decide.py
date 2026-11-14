"""One decision cycle: from a forecast and a stock position to a costed plan.

The same function drives the agent's recommendations on the as-of date and its behaviour inside
the closed-loop evaluation, so what is recommended is exactly what is tested.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from luma.config import PolicySettings
from luma.data.panel import Panel
from luma.inventory.policy import (
    ReplenishmentPlan,
    StockView,
    build_stock_view,
    expected_lost_units,
    plan_replenishment,
)
from luma.inventory.transfers import TransferPlan, _empty, plan_transfers
from luma.models.pipeline import Forecast, nb_sf


@dataclass
class Impact:
    """Expected effect of a plan, before it is executed."""

    lost_units_week_before: float  # expected unmet demand this week with no action
    lost_units_week_after: float  # after transfers
    lost_units_cycle_before: float  # expected unmet demand over the protection interval, no action
    lost_units_cycle_after: float  # after transfers and supplier orders
    revenue_protected: float  # INR of expected sales saved over the protection interval
    cells_at_risk_before: int  # cells with more than 50% stockout probability this cycle
    cells_at_risk_after: int


@dataclass
class InventoryPlan:
    view: StockView
    transfers: TransferPlan
    replenishment: ReplenishmentPlan
    impact: Impact
    service_levels: tuple[float, float, float]
    budget: float | None

    @property
    def position_after(self) -> np.ndarray:
        return self.transfers.apply(self.view.position) + self.replenishment.orders


def decide(
    panel: Panel,
    forecast: Forecast,
    size_share: np.ndarray,
    distance_km: np.ndarray,
    settings: PolicySettings,
    *,
    service_levels: tuple[float, float, float] | None = None,
    use_transfers: bool = True,
    budget: float | None = None,
) -> InventoryPlan:
    """Plan transfers first (they act within a day), then supplier orders on what remains."""
    levels = service_levels or tuple(v for _, v in settings.service_levels)
    view = build_stock_view(panel, forecast, size_share, settings, levels)
    transfers = plan_transfers(view, distance_km, settings) if use_transfers else _empty()
    position = transfers.apply(view.position)
    replenishment = plan_replenishment(view, panel.unit_cost, position=position, budget=budget)

    avail_before = view.on_hand + view.inbound_soon
    avail_after = transfers.apply(avail_before)
    week_before = expected_lost_units(view, avail_before, "short")
    week_after = expected_lost_units(view, avail_after, "short")
    cycle_before = expected_lost_units(view, view.position, "long")
    final = position + replenishment.orders
    cycle_after = expected_lost_units(view, final, "long")
    price = panel.price * (
        1.0 - panel.discount[forecast.origin + 1 : forecast.origin + 1 + view.horizon_long].mean(axis=0)
    )
    live = view.active[..., None]
    risk_after = np.where(live, nb_sf(final, view.mean_long, view.shape_long), 0.0)
    impact = Impact(
        lost_units_week_before=float(week_before.sum()),
        lost_units_week_after=float(week_after.sum()),
        lost_units_cycle_before=float(cycle_before.sum()),
        lost_units_cycle_after=float(cycle_after.sum()),
        revenue_protected=float(((cycle_before - cycle_after) * price[None, :, None]).sum()),
        cells_at_risk_before=int((view.risk_long > 0.5).sum()),
        cells_at_risk_after=int((risk_after > 0.5).sum()),
    )
    return InventoryPlan(view, transfers, replenishment, impact, tuple(levels), budget)
