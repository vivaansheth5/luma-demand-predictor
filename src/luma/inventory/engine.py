"""Inventory dynamics.

One engine moves stock through days for every (store, product, size) cell at once. The same
engine generates the simulated history and scores candidate policies on the hidden future, so
every policy is judged by identical mechanics and identical demand.

Day order: receive inbound stock, sell (sales = min(demand, on hand)), then let the policy place
supplier orders and store-to-store transfers in the evening.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np


@dataclass
class InventoryState:
    """Stock position. ``pipeline[d]`` arrives at the start of day ``today + d + 1``."""

    on_hand: np.ndarray  # [S, K, Z]
    pipeline: np.ndarray  # [L, S, K, Z]

    def copy(self) -> InventoryState:
        return InventoryState(self.on_hand.copy(), self.pipeline.copy())

    @property
    def inbound(self) -> np.ndarray:
        return self.pipeline.sum(axis=0)


@dataclass
class Decision:
    """What a policy does on one evening."""

    orders: np.ndarray | None = None  # [S, K, Z] units ordered from the supplier
    transfers: np.ndarray | None = None  # rows of (src_store, dst_store, sku, size, units)


@dataclass
class RunLog:
    """Per-day record of a run. The first ``prefix`` days are history carried in for look-back."""

    sales: np.ndarray  # [P + D, S, K, Z]
    sod: np.ndarray  # stock at start of day, after receipts
    eod: np.ndarray  # stock at end of day
    prefix: int = 0
    orders: np.ndarray | None = None  # [D, S, K, Z]
    first_orders: np.ndarray | None = None  # orders placed by the pre-run decision, if any
    transfer_units: int = 0
    transfer_trips: int = 0
    decisions: list = field(default_factory=list)

    def window(self, name: str) -> np.ndarray:
        """The run's own days of ``name`` (history prefix removed)."""
        return getattr(self, name)[self.prefix :]


@dataclass
class PolicyContext:
    """What a policy may look at: observed sales and stock so far, never demand."""

    day: int  # index within the run, 0-based
    abs_day: int  # index into ``log.sales`` (prefix included)
    weekday: int
    state: InventoryState
    log: RunLog


Policy = Callable[[PolicyContext], Decision | None]


def run_inventory(
    demand: np.ndarray,
    state: InventoryState,
    policy: Policy | None,
    *,
    first_weekday: int = 0,
    receipts: np.ndarray | None = None,
    prefix: RunLog | None = None,
    transfer_lead: int = 1,
    decide_first: bool = False,
) -> tuple[RunLog, InventoryState]:
    """Run ``demand.shape[0]`` days of inventory dynamics.

    Parameters
    ----------
    demand : true demand per day, shape ``[D, S, K, Z]``. Only the engine sees it.
    state : starting stock position; not modified.
    policy : called every evening with a :class:`PolicyContext`.
    first_weekday : weekday (0 = Monday) of the run's first day.
    receipts : optional exogenous receipts per day (opening allocations), ``[D, S, K, Z]``.
    prefix : earlier log whose sales and stock are prepended so policies can look back.
    transfer_lead : days until a transfer is available at the destination.
    decide_first : also call the policy once before the first day, on the evening of the last
        prefix day (``ctx.day == -1``). Used when a run starts from a pre-decision state.
    """
    n_days = demand.shape[0]
    state = state.copy()
    lead = state.pipeline.shape[0]
    p = 0 if prefix is None else prefix.sales.shape[0]
    shape = (p + n_days, *demand.shape[1:])
    log = RunLog(
        sales=np.zeros(shape, dtype=np.int32),
        sod=np.zeros(shape, dtype=np.int32),
        eod=np.zeros(shape, dtype=np.int32),
        prefix=p,
        orders=np.zeros(demand.shape, dtype=np.int32),
    )
    if prefix is not None:
        log.sales[:p], log.sod[:p], log.eod[:p] = prefix.sales, prefix.sod, prefix.eod

    def act(d: int) -> None:
        if policy is None:
            return
        ctx = PolicyContext(d, p + d, (first_weekday + d) % 7, state, log)
        decision = policy(ctx)
        if decision is None:
            return
        log.decisions.append((d, decision))
        if decision.transfers is not None and len(decision.transfers):
            moves = np.asarray(decision.transfers, dtype=np.int64)
            src, dst, sku, size, units = moves.T
            np.subtract.at(state.on_hand, (src, sku, size), units)
            if (state.on_hand < 0).any():
                raise ValueError("transfer plan ships more units than a store holds")
            np.add.at(state.pipeline[transfer_lead - 1], (dst, sku, size), units)
            log.transfer_units += int(units.sum())
            log.transfer_trips += len({(a, b) for a, b in zip(src.tolist(), dst.tolist(), strict=True)})
        if decision.orders is not None:
            orders = np.asarray(decision.orders, dtype=np.int32)
            state.pipeline[lead - 1] += orders
            if d >= 0:
                log.orders[d] += orders
            else:
                log.first_orders = orders.copy()

    if decide_first:
        act(-1)
    for d in range(n_days):
        state.on_hand += state.pipeline[0]
        state.pipeline[:-1] = state.pipeline[1:]
        state.pipeline[-1] = 0
        if receipts is not None:
            state.on_hand += receipts[d]
        log.sod[p + d] = state.on_hand
        sold = np.minimum(demand[d], state.on_hand)
        state.on_hand -= sold
        log.sales[p + d] = sold
        log.eod[p + d] = state.on_hand
        act(d)
    return log, state
