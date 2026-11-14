"""The status-quo replenishment rule the agent is measured against.

Every review day each cell is topped up to ``cover_days`` of its recent selling rate. The rate
is averaged over days that started with stock, which is the stockout correction most retail
teams apply by hand. The rule is reactive: it sees the past only.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from demand_agent.inventory.engine import Decision, PolicyContext


@dataclass
class BaselinePolicy:
    cover_days: float
    active: np.ndarray  # [P + D, S, K] bool, or a callable day -> [S, K]
    review_weekday: int = 6
    window: int = 28
    min_stock: int = 1

    def __call__(self, ctx: PolicyContext) -> Decision | None:
        if ctx.weekday != self.review_weekday:
            return None
        hi = ctx.abs_day + 1
        lo = max(0, hi - self.window)
        sales = ctx.log.sales[lo:hi].sum(axis=0)
        in_stock_days = (ctx.log.sod[lo:hi] > 0).sum(axis=0)
        rate = sales / np.maximum(in_stock_days, 1)
        target = np.ceil(rate * self.cover_days)
        active = self.active(ctx.abs_day) if callable(self.active) else self.active[ctx.abs_day]
        target = np.where(active[..., None], np.maximum(target, self.min_stock), 0)
        position = ctx.state.on_hand + ctx.state.inbound
        return Decision(orders=np.maximum(target - position, 0).astype(np.int32))
