"""End-to-end pipeline: load or simulate, forecast, evaluate, decide, and report."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace

import numpy as np

from luma.agent.loop import AgentRun, run_agent
from luma.agent.workspace import Workspace, build_workspace
from luma.config import WINDOWS, PolicySettings, Settings
from luma.data.panel import Panel
from luma.evaluation.backtest import pool, score_run, summarise
from luma.evaluation.extras import size_curve_report, trend_report, uncensoring_report
from luma.evaluation.policy_eval import run_policy_evaluation
from luma.models.pipeline import RollingResult, rolling_forecasts, window_totals
from luma.signals.anomaly import score_detection
from luma.sim.world import Truth, World, generate_world


@dataclass
class PipelineResult:
    settings: Settings
    panel: Panel
    truth: Truth | None
    rolling: RollingResult
    workspace: Workspace
    agent: AgentRun
    evaluation: dict | None  # forecast accuracy tables
    extras: dict  # uncensoring, size curves, trends, anomalies against truth
    policy: dict | None  # closed-loop policy evaluation
    aggregate_sd: dict[str, float]  # relative forecast error at coarser levels
    timings: dict[str, float] = field(default_factory=dict)


def aggregate_error_sd(rolling: RollingResult, actual_daily: np.ndarray, panel: Panel) -> dict[str, float]:
    """Spread of weekly forecast error after summing to category, store and network level.

    Product-level quantiles cannot be added up into a network interval, because errors partly
    cancel. The spread of past errors at each level is measured directly instead and used to
    draw intervals around aggregated forecasts.
    """
    weekly = [i for i, (_, n) in enumerate(WINDOWS) if n == 7]
    cat = panel.category_code
    errs: dict[str, list[float]] = {"network": [], "category": [], "store": []}
    for run in rolling.folds:
        actual = window_totals(actual_daily, run.cutoff)
        pred = run.forecast.mean
        live = run.forecast.active[..., None] & np.isfinite(pred) & np.isfinite(actual)
        a = np.where(live, actual, 0.0)[..., weekly]
        f = np.where(live, pred, 0.0)[..., weekly]
        groups = {
            "network": (a.sum(axis=(0, 1)), f.sum(axis=(0, 1))),
            "store": (a.sum(axis=1).ravel(), f.sum(axis=1).ravel()),
            "category": (
                np.concatenate([a[:, cat == c].sum(axis=(0, 1)) for c in range(len(panel.categories))]),
                np.concatenate([f[:, cat == c].sum(axis=(0, 1)) for c in range(len(panel.categories))]),
            ),
        }
        for name, (act, fc) in groups.items():
            ok = (act > 0) & (fc > 0)
            errs[name].extend(np.log(act[ok] / fc[ok]).tolist())
    fallback = {"network": 0.08, "category": 0.12, "store": 0.12}
    return {k: float(np.sqrt(np.mean(np.square(v)))) if len(v) >= 4 else fallback[k] for k, v in errs.items()}


def policy_for(
    panel: Panel, policy: PolicySettings, budget: float | None = None, approval_threshold: float | None = None
) -> PolicySettings:
    """Scale the money settings to a brand's own data.

    The built-in defaults are sized for the demo. Without an explicit budget, the budget is the
    cost of the last 14 days of sales, which is what one order cycle has to replace. Without an
    explicit approval threshold, order lines above 1% of the budget go to a person.
    """
    if budget is None:
        recent = panel.sales[-14:].sum(axis=(0, 1, 3)).astype(float)
        budget = max(float(np.ceil((recent * panel.unit_cost).sum() / 100.0) * 100.0), 100.0)
    if approval_threshold is None:
        approval_threshold = max(round(0.01 * budget), 1.0)
    return replace(policy, replenishment_budget=float(budget), approval_threshold=float(approval_threshold))


def run_pipeline(
    settings: Settings | None = None,
    *,
    mode: str = "rules",
    world: World | None = None,
    panel: Panel | None = None,
    with_policy: bool = True,
    progress: Callable[[str], None] | None = None,
) -> PipelineResult:
    """Run everything and return every intermediate result.

    Pass ``panel`` to run on a brand's own data. Forecasts are then scored against
    stockout-corrected sales on earlier forecast dates. Without ``panel`` the simulated demo
    world is used, which also holds true demand and a hidden future, so every component and the
    agent's decisions can be scored against the truth.
    """
    settings = settings or Settings()
    timings: dict[str, float] = {}

    def say(msg: str) -> None:
        if progress:
            progress(msg)

    def timed(name: str, started: float) -> None:
        timings[name] = round(time.perf_counter() - started, 2)

    truth: Truth | None = None
    if panel is None:
        t0 = time.perf_counter()
        if world is None:
            say("Simulating the demo network")
            world = generate_world(settings.sim)
        panel, truth = world.panel, world.truth
        timed("simulate", t0)

    t0 = time.perf_counter()
    say("Training and replaying forecasts")
    rolling = rolling_forecasts(panel, settings.model, progress=say)
    timed("forecast", t0)

    t0 = time.perf_counter()
    if truth is not None:
        say("Scoring forecasts against true demand")
        actual_daily = truth.demand.sum(axis=-1).astype(float)
        runs = [*rolling.folds, rolling.live]
        expected = truth.lam
    else:
        say("Scoring earlier forecasts against what was sold next")
        actual_daily = rolling.live.uncensored.by_sku.astype(float)
        runs = list(rolling.folds)
        expected = None
    labels = {i: panel.date_of(r.cutoff).isoformat() for i, r in enumerate(runs)}
    evaluation = None
    if runs:
        scored = pool([score_run(r, i, actual_daily, expected) for i, r in enumerate(runs)])
        if len(scored.actual):
            evaluation = summarise(scored, panel, labels)
            evaluation["folds"] = [
                {"origin": labels[i], "calibrated": r.calibrated, "train_rows": r.train_rows, "live": r is rolling.live}
                for i, r in enumerate(runs)
            ]
    sd = aggregate_error_sd(rolling, actual_daily, panel)
    timed("evaluate", t0)

    t0 = time.perf_counter()
    say("Detecting trends and anomalies, fitting size curves")
    ws = build_workspace(panel, settings, rolling.live.uncensored, rolling.model, rolling.live.forecast)
    if truth is not None:
        extras = {
            "uncensoring": uncensoring_report(panel, rolling.live.uncensored, truth),
            "size_curves": size_curve_report(panel, ws.size_curves, truth),
            "trends": trend_report(panel, ws.trends, truth),
            "anomalies": score_detection(ws.anomalies, truth.anomalies, panel.n_days),
        }
    else:
        unc = rolling.live.uncensored
        extras = {
            "uncensoring": {"censored_share": unc.censored_share, "uplift": unc.uplift}
            if panel.has_stock_history
            else None,
            "size_curves": None,
            "trends": None,
            "anomalies": None,
        }
    timed("signals", t0)

    policy = None
    if with_policy and truth is not None:
        t0 = time.perf_counter()
        say("Running policies through the hidden 28 days")
        policy = run_policy_evaluation(
            panel, truth, rolling.model, rolling.live.forecast, ws.size_curves.share, settings, progress=say
        )
        timed("policy_evaluation", t0)

    t0 = time.perf_counter()
    say("Running the agent")
    agent = run_agent(ws, mode=mode)
    timed("agent", t0)

    return PipelineResult(
        settings=settings,
        panel=panel,
        truth=truth,
        rolling=rolling,
        workspace=ws,
        agent=agent,
        evaluation=evaluation,
        extras=extras,
        policy=policy,
        aggregate_sd=sd,
        timings=timings,
    )
