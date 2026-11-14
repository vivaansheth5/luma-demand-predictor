"""Command line interface."""

from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from demand_agent.config import Settings, small_settings

app = typer.Typer(add_completion=False, help="Demand prediction and inventory agent for 60-minute fashion delivery.")
console = Console()


def _settings(small: bool, seed: int | None) -> Settings:
    settings = small_settings() if small else Settings()
    if seed is not None:
        settings = replace(settings, sim=replace(settings.sim, seed=seed))
    return settings


@app.command()
def run(
    out: Path = typer.Option(Path("artifacts"), help="Folder for the dashboard JSON and action tables."),
    mode: str = typer.Option("rules", help="Agent planner: rules, llm, or auto (llm when ANTHROPIC_API_KEY is set)."),
    small: bool = typer.Option(False, help="Use a tiny world that runs in seconds."),
    seed: int | None = typer.Option(None, help="Random seed of the simulated world."),
    skip_policy: bool = typer.Option(False, help="Skip the closed-loop policy evaluation."),
    save_data: Path | None = typer.Option(None, help="Also write the simulated tables to this folder."),
) -> None:
    """Simulate, forecast, evaluate, run the agent and export everything."""
    from demand_agent.export import write_outputs
    from demand_agent.run import run_pipeline
    from demand_agent.sim.world import generate_world

    settings = _settings(small, seed)
    started = time.perf_counter()

    def progress(msg: str) -> None:
        console.print(f"[dim]{time.perf_counter() - started:6.1f}s[/dim]  {msg}")

    world = generate_world(settings.sim)
    if save_data is not None:
        world.save(save_data)
        progress(f"Wrote simulated tables to {save_data}/")
    res = run_pipeline(settings, mode=mode, world=world, with_policy=not skip_policy, progress=progress)
    paths = write_outputs(res, out)
    progress(f"Wrote {', '.join(p.name for p in paths.values())} to {out}/")
    _print_summary(res)


def _print_summary(res) -> None:
    ev = res.evaluation
    table = Table(title="Forecast error against true demand (WAPE, lower is better)", title_justify="left")
    table.add_column("Method")
    table.add_column("All windows", justify="right")
    table.add_column("Bias", justify="right")
    for name, row in ev["overall"].items():
        table.add_row(name, f"{row['wape']:.1%}", f"{row['bias']:+.1%}")
    console.print(table)
    i90 = ev["coverage"]["nominal"].index(0.9)
    console.print(
        f"The agent removes [bold]{ev['skill_vs_trailing']:.0%}[/bold] of the avoidable error of a trailing "
        f"28-day average. A stated P90 holds {ev['coverage']['calibrated'][i90]:.1%} of outcomes below it; "
        f"the P10-P90 interval covers {ev['interval80']['calibrated']:.0%} (target 80%)."
    )
    if res.policy:
        table = Table(title="Closed loop over the hidden 28 days", title_justify="left")
        for col in ("Policy", "Fill rate", "Lost revenue", "Average stock", "Net contribution"):
            table.add_column(col, justify="left" if col == "Policy" else "right")
        h = res.policy["headline"]
        for key in ("status_quo", "agent_orders_only", "agent"):
            r = h[key]
            table.add_row(
                r["name"],
                f"{r['fill_rate']:.1%}",
                f"Rs {r['lost_revenue'] / 1e5:.2f}L",
                f"Rs {r['avg_stock_value'] / 1e5:.1f}L",
                f"Rs {r['net_contribution'] / 1e5:.2f}L",
            )
        console.print(table)
    console.print(
        f"\n[bold]Agent brief[/bold] ({res.agent.mode} planner, {len(res.agent.steps)} steps, {res.agent.status})"
    )
    console.print(res.agent.brief["headline"])
    for item in res.agent.brief["findings"]:
        console.print(f"  [bold]{item['title']}[/bold]\n  {item['body']}")
    if res.agent.brief.get("narrative"):
        console.print(res.agent.brief["narrative"])


@app.command()
def simulate(
    out: Path = typer.Option(Path("data"), help="Folder for the simulated tables."),
    small: bool = typer.Option(False),
    seed: int | None = typer.Option(None),
) -> None:
    """Generate the synthetic Bangalore world and write it as Parquet tables."""
    from demand_agent.sim.world import generate_world

    world = generate_world(_settings(small, seed).sim)
    world.save(out)
    p = world.panel
    console.print(
        f"Wrote {p.n_days} days, {p.n_stores} stores and {p.n_skus} products to {out}/ "
        f"(as of {p.asof}). Simulator truth is in {out}/truth/."
    )


@app.command()
def trace(artifacts: Path = typer.Option(Path("artifacts"))) -> None:
    """Print the agent's last run step by step."""
    path = artifacts / "agent_trace.json"
    if not path.exists():
        raise typer.BadParameter(f"{path} not found; run `demand-agent run` first")
    agent = json.loads(path.read_text())
    for step in agent["steps"]:
        console.print(f"[bold]{step['n']:>2}. {step['tool']}[/bold] [dim]{json.dumps(step['args'])}[/dim]")
        console.print(f"    [italic]{step['thought']}[/italic]")
        console.print(f"    {step['observation']}")


@app.command()
def serve(
    artifacts: Path = typer.Option(Path("artifacts"), help="Folder holding dashboard.json."),
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(8000),
    live: bool = typer.Option(False, help="Run the pipeline at start-up and enable agent re-runs over the API."),
    small: bool = typer.Option(False, help="With --live, use the tiny world."),
) -> None:
    """Serve the dashboard and the JSON API."""
    import uvicorn

    from demand_agent.api.app import create_app

    result = None
    if live:
        from demand_agent.run import run_pipeline

        result = run_pipeline(_settings(small, None), progress=lambda m: console.print(f"[dim]{m}[/dim]"))
    uvicorn.run(create_app(artifacts, result), host=host, port=port)


if __name__ == "__main__":
    app()
