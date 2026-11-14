"""Run the agent on your own tables.

The pipeline needs one object, a ``Panel``, built from long tables with the schemas listed in
the README. This script loads a panel from a folder of Parquet files, trains and calibrates the
forecaster, runs the agent, and writes the plan. Nothing here touches the simulator's truth.

Try it on simulated tables first:

    demand-agent simulate --small --out data
    python examples/own_data.py data
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

from demand_agent.agent.loop import run_agent
from demand_agent.agent.workspace import build_workspace
from demand_agent.config import Settings, small_settings
from demand_agent.data.panel import Panel
from demand_agent.models.pipeline import rolling_forecasts


def main(folder: Path, small: bool) -> None:
    settings = small_settings() if small else Settings()
    panel = Panel.load(folder)
    print(f"Loaded {panel.n_days} days, {panel.n_stores} stores, {panel.n_skus} products (as of {panel.asof}).")

    # Replays earlier forecast dates to calibrate intervals, then forecasts from the latest day.
    rolling = rolling_forecasts(panel, settings.model, progress=print)
    live = rolling.live

    workspace = build_workspace(panel, settings, live.uncensored, rolling.model, live.forecast)
    run = run_agent(workspace, mode="auto")  # Claude drives the tools when ANTHROPIC_API_KEY is set

    print(f"\n{run.brief['headline']}")
    for step in run.steps:
        print(f"  {step.index:>2}. {step.tool}: {step.observation}")
    if workspace.committed:
        orders = pd.DataFrame(workspace.committed["orders"])
        orders.to_csv("orders.csv", index=False)
        pd.DataFrame(workspace.committed["transfers"]).to_csv("transfers.csv", index=False)
        print(f"\nWrote {len(orders)} order lines to orders.csv and the transfer list to transfers.csv.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: python examples/own_data.py <folder with parquet tables> [--full]")
    main(Path(sys.argv[1]), small="--full" not in sys.argv)
