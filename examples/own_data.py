"""Run Luma on your own tables from Python.

The command line does the same thing (``luma run --data my-brand``); use this when you want
the results as objects. A folder needs ``sales.csv`` and ``catalog.csv``; ``luma init`` writes
starter files and a column guide. To try it without your own data:

    luma simulate --small --csv --out data
    python examples/own_data.py data
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

from luma.config import Settings
from luma.data.loader import load_panel
from luma.export import write_outputs
from luma.run import policy_for, run_pipeline


def main(folder: Path) -> None:
    panel = load_panel(folder)  # pass currency="EUR", lead_time=10 and so on to override the defaults
    print(f"Loaded {panel.n_days} days, {panel.n_stores} store(s), {panel.n_skus} products (as of {panel.asof}).")

    # The money settings ship at demo scale; this sizes the budget and approval limit to your data.
    settings = Settings()
    settings = replace(settings, policy=policy_for(panel, settings.policy))

    # Replays earlier dates to calibrate the ranges, forecasts from the latest day, runs the agent.
    result = run_pipeline(settings, mode="auto", panel=panel, progress=print)

    print(f"\n{result.agent.brief['headline']}")
    for step in result.agent.steps:
        print(f"  {step.index:>2}. {step.tool}: {step.observation}")

    rising = [t for t in result.workspace.trends if t.status in ("Rising", "Emerging")]
    for t in rising:
        print(
            f"  {t.attribute} {t.value}: {t.status.lower()}, share {t.change_4w:+.0%} in four weeks (q={t.q_value:.3f})"
        )

    paths = write_outputs(result, Path("artifacts"))
    print(f"\nWrote {', '.join(p.name for p in paths.values())} to artifacts/.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: python examples/own_data.py <folder with sales.csv and catalog.csv>")
    main(Path(sys.argv[1]))
