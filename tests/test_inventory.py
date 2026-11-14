import numpy as np
import pytest

from demand_agent.inventory.decide import decide
from demand_agent.inventory.policy import classify_abc, plan_replenishment
from demand_agent.models.pipeline import nb_ppf


@pytest.fixture(scope="module")
def plan(result):
    ws = result.workspace
    return decide(ws.panel, ws.forecast, ws.size_curves.share, ws.distance, ws.settings.policy)


def test_abc_classes_cover_every_pair():
    revenue = np.array([[100.0, 50.0, 30.0, 10.0, 5.0, 5.0]])
    abc = classify_abc(revenue, np.ones_like(revenue, dtype=bool), (0.6, 0.9))
    assert abc[0, 0] == 0 and abc[0, -1] == 2
    assert set(np.unique(abc)) <= {0, 1, 2}


def test_unconstrained_orders_reach_the_service_quantile(plan):
    view, rep = plan.view, plan.replenishment
    level = np.broadcast_to(view.service_level[..., None], rep.position.shape)
    target = np.maximum(nb_ppf(level, view.mean_long, view.shape_long), 1)
    live = np.broadcast_to(view.active[..., None], rep.position.shape)
    assert (rep.position + rep.orders >= target)[live].all()
    assert rep.orders[~live].sum() == 0
    assert not rep.budget_binding


def test_budget_is_respected_and_cuts_the_least_valuable_units(plan, result):
    cost = result.panel.unit_cost
    budget = 0.5 * plan.replenishment.cost
    capped = plan_replenishment(plan.view, cost, position=plan.replenishment.position, budget=budget)
    assert capped.budget_binding and capped.cost <= budget
    assert capped.cost > 0.9 * budget  # the budget is actually used
    assert (capped.orders <= plan.replenishment.orders).all()
    assert capped.marginal_cutoff is not None and capped.marginal_cutoff > 0


def test_transfers_are_feasible(plan, result):
    moves = plan.transfers.moves
    policy = result.settings.policy
    view = plan.view
    shipped = np.zeros_like(view.on_hand)
    np.add.at(shipped, (moves[:, 0], moves[:, 2], moves[:, 3]), moves[:, 4])
    assert (shipped <= view.on_hand).all()
    assert (moves[:, 0] != moves[:, 1]).all() and (moves[:, 4] > 0).all()
    for lane in plan.transfers.lanes:
        assert lane["units"] <= policy.lane_capacity * policy.max_trips_per_lane
    assert plan.transfers.apply(view.position).sum() == view.position.sum()


def test_transfers_pay_for_themselves(plan):
    if plan.transfers.units:
        assert plan.transfers.net_gain > 0


def test_plan_reduces_expected_lost_sales(plan):
    i = plan.impact
    assert i.lost_units_week_after <= i.lost_units_week_before + 1e-6
    assert i.lost_units_cycle_after < i.lost_units_cycle_before
    assert i.cells_at_risk_after <= i.cells_at_risk_before


def test_closed_loop_results_are_consistent(result):
    for r in result.policy["results"]:
        assert r["sold_units"] + r["lost_units"] == r["demand_units"]
        assert 0 < r["fill_rate"] <= 1
    baseline = [r for r in result.policy["results"] if r["kind"] == "baseline"]
    by_cover = sorted(baseline, key=lambda r: float(r["dial"].split()[0]))
    fills = [r["fill_rate"] for r in by_cover]
    assert fills == sorted(fills)  # more cover never lowers service
    demand = {r["demand_units"] for r in result.policy["results"]}
    assert len(demand) == 1  # every policy faced the same demand
