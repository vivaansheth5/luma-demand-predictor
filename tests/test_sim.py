import numpy as np

from demand_agent.data.panel import Panel
from demand_agent.inventory.engine import Decision, InventoryState, run_inventory
from demand_agent.sim.world import generate_world


def test_world_is_deterministic(settings, world):
    again = generate_world(settings.sim)
    assert np.array_equal(world.panel.sales, again.panel.sales)
    assert np.array_equal(world.truth.demand, again.truth.demand)


def test_sales_never_exceed_demand_or_stock(world):
    panel, truth = world.panel, world.truth
    demand = truth.demand[: panel.n_days]
    assert (panel.sales <= demand).all()
    assert (panel.sales <= panel.sod).all()
    assert np.array_equal(panel.sales, np.minimum(demand, panel.sod))
    assert np.array_equal(panel.eod, panel.sod - panel.sales)
    assert (panel.eod >= 0).all()


def test_nothing_sells_before_a_store_opens_or_a_product_launches(world):
    panel = world.panel
    days = np.arange(panel.n_days)[:, None, None]
    before = days < panel.start_day[None]
    assert panel.sales.sum(axis=-1)[before].sum() == 0
    assert world.truth.demand[: panel.n_days].sum(axis=-1)[before].sum() == 0


def test_history_contains_stockouts(world):
    panel = world.panel
    lost = world.truth.demand[: panel.n_days].sum() - panel.sales.sum()
    assert lost > 0
    assert 0.01 < panel.censored.mean() < 0.5


def test_panel_round_trips_through_parquet(world, tmp_path):
    world.panel.save(tmp_path)
    loaded = Panel.load(tmp_path)
    p = world.panel
    assert loaded.start == p.start and loaded.asof == p.asof
    for name in ("sales", "sod", "eod", "on_hand", "pipeline"):
        assert np.array_equal(getattr(loaded, name), getattr(p, name)), name
    assert np.allclose(loaded.discount, p.discount)
    assert np.allclose(loaded.buzz, p.buzz)
    assert list(loaded.catalog["sku_id"]) == list(p.catalog["sku_id"])


def test_simulator_internals_stay_out_of_the_panel(world):
    assert not [c for c in world.panel.catalog.columns if c.startswith("_")]


def test_engine_conserves_units():
    rng = np.random.default_rng(0)
    demand = rng.poisson(1.0, (30, 2, 3, 4))
    state = InventoryState(np.full((2, 3, 4), 5, dtype=np.int32), np.zeros((7, 2, 3, 4), dtype=np.int32))

    def policy(ctx):
        if ctx.weekday != 6:
            return None
        moves = np.array([[0, 1, 0, 0, 1]]) if ctx.state.on_hand[0, 0, 0] > 0 else None
        return Decision(orders=np.ones((2, 3, 4), dtype=np.int32), transfers=moves)

    log, end = run_inventory(demand, state, policy)
    ordered = log.orders.sum()
    assert state.on_hand.sum() + ordered == log.window("sales").sum() + end.on_hand.sum() + end.pipeline.sum()
    assert state.on_hand.sum() == 2 * 3 * 4 * 5  # the starting state is not modified
