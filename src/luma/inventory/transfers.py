"""Store-to-store transfers as a mixed-integer programme.

A supplier order takes a week to land. Until then, the only way to save a sale in a store that
is about to run out is to move a unit from a store that will not miss it. This module decides
which units to move.

Every unit has a probability of selling where it sits and a probability of selling where it
could go. Moving the n-th spare unit of a donor to become the m-th extra unit of a receiver is
worth

    margin * (P(receiver sells it) - P(donor would have sold it)) - handling cost.

Units are not free to move one at a time: a rider trip between two stores has a fixed cost and
a capacity. The plan therefore chooses which lanes to open (binary variables) and how many units
of each product and size to send down each open lane (integer flows), maximising expected margin
recovered net of all transfer costs. Each unit's value is its own decision variable with a value
that falls as stock rises, which keeps the objective linear while respecting diminishing
returns. The model is solved to optimality with HiGHS through SciPy.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix

from luma.config import PolicySettings
from luma.inventory.policy import StockView
from luma.models.pipeline import nb_sf


@dataclass
class TransferPlan:
    moves: np.ndarray  # rows of (src_store, dst_store, sku, size, units)
    lanes: list[dict] = field(default_factory=list)
    units: int = 0
    cost: float = 0.0  # handling plus trip costs, INR
    expected_gain: float = 0.0  # expected margin recovered before costs, INR
    status: str = "no candidates"
    gap: float = 0.0  # relative optimality gap reported by the solver
    solve_seconds: float = 0.0
    cells_considered: int = 0
    variables: int = 0

    @property
    def net_gain(self) -> float:
        return self.expected_gain - self.cost

    def apply(self, position: np.ndarray) -> np.ndarray:
        """Stock position after the transfers land."""
        out = position.copy()
        if len(self.moves):
            src, dst, sku, size, units = self.moves.T
            np.subtract.at(out, (src, sku, size), units)
            np.add.at(out, (dst, sku, size), units)
        return out


def _empty() -> TransferPlan:
    return TransferPlan(moves=np.zeros((0, 5), dtype=np.int64))


def plan_transfers(
    view: StockView,
    distance_km: np.ndarray,
    settings: PolicySettings,
    max_units: int = 6,
) -> TransferPlan:
    """Choose the transfers that maximise expected margin recovered net of transfer costs."""
    started = time.perf_counter()
    n_stores = view.on_hand.shape[0]
    live = view.active[..., None]
    avail = view.on_hand + view.inbound_soon
    margin = np.maximum(view.margin, 0.0)
    unit_cost = settings.transfer_unit_cost + settings.transfer_cost_per_km * distance_km
    lane_cost = settings.lane_fixed_cost + settings.lane_cost_per_km * distance_km
    min_cost = float(unit_cost[~np.eye(n_stores, dtype=bool)].min()) if n_stores > 1 else np.inf

    n = np.arange(1, max_units + 1)
    # P(the n-th unit added to a receiver sells) and P(the n-th unit taken from a donor would have sold).
    gain = np.where(
        live[..., None], nb_sf(avail[..., None] + n - 1, view.mean_short[..., None], view.shape_short[..., None]), 0.0
    )
    loss = nb_sf(avail[..., None] - n, view.mean_short[..., None], view.shape_short[..., None])
    loss = np.where((view.on_hand[..., None] >= n) & live[..., None], loss, np.inf)

    best_gain = gain[..., 0].max(axis=0)  # [K, Z]
    best_loss = loss[..., 0].min(axis=0)
    worth = margin[:, None] * (best_gain - best_loss) > min_cost
    cells = np.argwhere(worth)
    if len(cells) == 0 or n_stores < 2:
        return _empty()

    obj: list[float] = []
    lower: list[float] = []
    upper: list[float] = []
    integer: list[int] = []
    rows: list[int] = []
    cols: list[int] = []
    vals: list[float] = []
    n_eq = 0
    flows: list[tuple[int, int, int, int, int]] = []  # (var, src, dst, sku, size)
    unit_vars: list[tuple[int, float]] = []  # (var, signed expected margin)

    def add_var(cost: float, ub: float, is_int: int) -> int:
        obj.append(cost)
        lower.append(0.0)
        upper.append(ub)
        integer.append(is_int)
        return len(obj) - 1

    def balance_entry(row: int, var: int, coef: float) -> None:
        rows.append(row)
        cols.append(var)
        vals.append(coef)

    for k, z in cells:
        g, l_ = gain[:, k, z, :], loss[:, k, z, :]  # [S, N]
        mid = 0.5 * (best_gain[k, z] + best_loss[k, z])
        donors = [i for i in range(n_stores) if l_[i, 0] < mid]
        receivers = [j for j in range(n_stores) if g[j, 0] > mid and j not in donors]
        if not donors or not receivers:
            continue
        m = float(margin[k])
        donor_rows, receiver_rows = {}, {}
        for i in donors:
            donor_rows[i] = n_eq
            n_eq += 1
            for u in range(max_units):
                if not np.isfinite(l_[i, u]) or l_[i, u] >= best_gain[k, z]:
                    break
                v = add_var(m * l_[i, u], 1.0, 0)
                unit_vars.append((v, -m * l_[i, u]))
                balance_entry(donor_rows[i], v, -1.0)
        for j in receivers:
            receiver_rows[j] = n_eq
            n_eq += 1
            for u in range(max_units):
                if g[j, u] <= best_loss[k, z]:
                    break
                v = add_var(-m * g[j, u], 1.0, 0)
                unit_vars.append((v, m * g[j, u]))
                balance_entry(receiver_rows[j], v, -1.0)
        for i in donors:
            for j in receivers:
                v = add_var(float(unit_cost[i, j]), float(max_units), 1)
                flows.append((v, i, j, int(k), int(z)))
                balance_entry(donor_rows[i], v, 1.0)
                balance_entry(receiver_rows[j], v, 1.0)

    if not flows:
        return _empty()

    # Lane variables: one binary per ordered store pair that carries any candidate flow.
    lane_var: dict[tuple[int, int], int] = {}
    lane_row: dict[tuple[int, int], int] = {}
    lane_rows: list[int] = []
    lane_cols: list[int] = []
    lane_vals: list[float] = []
    capacity = float(settings.lane_capacity * settings.max_trips_per_lane)
    for v, i, j, _, _ in flows:
        if (i, j) not in lane_var:
            lane_row[(i, j)] = len(lane_var)
            lane_var[(i, j)] = add_var(float(lane_cost[i, j]), 1.0, 1)
        lane_rows.append(lane_row[(i, j)])
        lane_cols.append(v)
        lane_vals.append(1.0)
    for lane, y in lane_var.items():
        lane_rows.append(lane_row[lane])
        lane_cols.append(y)
        lane_vals.append(-capacity)

    n_var = len(obj)
    balance = coo_matrix((vals, (rows, cols)), shape=(n_eq, n_var)).tocsr()
    lanes = coo_matrix((lane_vals, (lane_rows, lane_cols)), shape=(len(lane_var), n_var)).tocsr()
    result = milp(
        c=np.array(obj),
        constraints=[LinearConstraint(balance, 0.0, 0.0), LinearConstraint(lanes, -np.inf, 0.0)],
        integrality=np.array(integer),
        bounds=Bounds(np.array(lower), np.array(upper)),
        options={"time_limit": settings.milp_time_limit, "mip_rel_gap": 0.01, "disp": False},
    )
    elapsed = time.perf_counter() - started
    if result.x is None:
        plan = _empty()
        plan.status = f"solver returned no solution ({result.message})"
        plan.solve_seconds = elapsed
        return plan

    x = result.x
    moves = [(i, j, k, z, int(round(x[v]))) for v, i, j, k, z in flows if x[v] > 0.5]
    moves_arr = np.array(moves, dtype=np.int64).reshape(-1, 5)
    expected_gain = float(sum(value * x[v] for v, value in unit_vars))
    by_lane: dict[tuple[int, int], dict] = {}
    for i, j, _, _, units in moves:
        lane = by_lane.setdefault(
            (i, j), {"src": i, "dst": j, "units": 0, "km": float(distance_km[i, j]), "cost": float(lane_cost[i, j])}
        )
        lane["units"] += units
        lane["cost"] += units * float(unit_cost[i, j])
    return TransferPlan(
        moves=moves_arr,
        lanes=sorted(by_lane.values(), key=lambda d: -d["units"]),
        units=int(moves_arr[:, 4].sum()) if len(moves_arr) else 0,
        cost=float(sum(d["cost"] for d in by_lane.values())),
        expected_gain=expected_gain,
        status="optimal" if result.status == 0 else "feasible (time limit reached)",
        gap=float(getattr(result, "mip_gap", 0.0) or 0.0),
        solve_seconds=elapsed,
        cells_considered=len(cells),
        variables=n_var,
    )
