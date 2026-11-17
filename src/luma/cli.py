"""Command line interface."""

from __future__ import annotations

import json
import os
import time
from dataclasses import replace
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from luma.config import Settings, small_settings
from luma.money import money

app = typer.Typer(
    add_completion=False,
    help="Luma: demand forecasts, trend detection and inventory decisions for fashion brands.",
)
console = Console()


def _settings(small: bool, seed: int | None, budget: float | None = None) -> Settings:
    settings = small_settings() if small else Settings()
    if seed is not None:
        settings = replace(settings, sim=replace(settings.sim, seed=seed))
    if budget is not None:
        settings = replace(settings, policy=replace(settings.policy, replenishment_budget=budget))
    return settings


@app.command()
def init(
    folder: Path = typer.Argument(Path("my-brand"), help="Folder to create the starter tables in."),
    overwrite: bool = typer.Option(False, help="Replace files that already exist."),
) -> None:
    """Write starter tables and a column guide for your own data."""
    from luma.data.templates import write_templates

    written = write_templates(folder, overwrite)
    if not written:
        console.print(f"Nothing written: {folder}/ already holds every file. Pass --overwrite to replace them.")
        return
    console.print(
        f"Wrote sales.csv, catalog.csv and a column guide to {folder}/, and optional tables to {folder}/optional/."
    )
    console.print(f"Replace the example rows with your own, then run: [bold]luma run --data {folder}[/bold]")


@app.command()
def run(
    data: Path | None = typer.Option(None, help="Folder with your tables. Leave out to run the simulated demo."),
    out: Path = typer.Option(Path("artifacts"), help="Folder for the dashboard JSON and action tables."),
    mode: str = typer.Option("rules", help="Agent planner: rules, llm, or auto (llm when ANTHROPIC_API_KEY is set)."),
    brand: str | None = typer.Option(None, help="Brand name shown on the dashboard."),
    currency: str | None = typer.Option(None, help="Currency code for your data, such as USD, EUR, GBP or INR."),
    lead_time: int | None = typer.Option(None, help="Supplier lead time in days for your data (default 7)."),
    budget: float | None = typer.Option(
        None, help="Supplier budget for this plan, in your currency. Default: the cost of the last 14 days of sales."
    ),
    approval_threshold: float | None = typer.Option(
        None, help="Order lines that cost more than this wait for a person. Default: 1% of the budget."
    ),
    trend_attribute: str | None = typer.Option(None, help="Catalog column that the buzz table is keyed by."),
    small: bool = typer.Option(False, help="Demo only: use a tiny world that runs in seconds."),
    seed: int | None = typer.Option(None, help="Demo only: random seed of the simulated world."),
    skip_policy: bool = typer.Option(False, help="Demo only: skip the closed-loop policy evaluation."),
    save_data: Path | None = typer.Option(None, help="Demo only: also write the simulated tables to this folder."),
) -> None:
    """Forecast, detect trends, run the agent and export everything."""
    from luma.data.loader import DataError, load_panel
    from luma.export import write_outputs
    from luma.run import policy_for, run_pipeline

    settings = _settings(small, seed, budget)
    started = time.perf_counter()

    def progress(msg: str) -> None:
        console.print(f"[dim]{time.perf_counter() - started:6.1f}s[/dim]  {msg}")

    if data is not None:
        try:
            panel = load_panel(data, currency=currency, lead_time=lead_time, trend_attribute=trend_attribute)
        except (DataError, FileNotFoundError) as err:
            console.print(f"[red]Could not read {data}/:[/red] {err}")
            console.print("Run [bold]luma init[/bold] for starter tables and a column guide.")
            raise typer.Exit(1) from err
        level = (
            "sales, catalog and stock"
            if panel.has_stock
            else "sales and catalog only, so forecasts and trends without stock decisions"
        )
        progress(
            f"Read {panel.n_days} days, {panel.n_stores} store(s), {panel.n_skus} products and "
            f"{panel.n_sizes} size(s) as of {panel.asof} ({level})"
        )
        settings = replace(settings, policy=policy_for(panel, settings.policy, budget, approval_threshold))
        res = run_pipeline(settings, mode=mode, panel=panel, progress=progress)
    else:
        from luma.sim.world import generate_world

        world = generate_world(settings.sim)
        if save_data is not None:
            world.save(save_data)
            progress(f"Wrote simulated tables to {save_data}/")
        res = run_pipeline(settings, mode=mode, world=world, with_policy=not skip_policy, progress=progress)
    paths = write_outputs(res, out, brand)
    progress(f"Wrote {', '.join(p.name for p in paths.values())} to {out}/")
    _print_summary(res)


def _print_summary(res) -> None:
    ev = res.evaluation
    currency = res.panel.currency
    if ev:
        table = Table(title=f"Forecast error against {ev['against']} (WAPE, lower is better)", title_justify="left")
        table.add_column("Method")
        table.add_column("All windows", justify="right")
        table.add_column("Bias", justify="right")
        for name, row in ev["overall"].items():
            table.add_row(name, f"{row['wape']:.1%}", f"{row['bias']:+.1%}")
        console.print(table)
        i90 = ev["coverage"]["nominal"].index(0.9)
        skill = ev.get("skill_vs_trailing")
        lead = (
            f"Luma removes [bold]{skill:.0%}[/bold] of the avoidable error of a trailing 28-day average. "
            if skill is not None
            else ""
        )
        console.print(
            f"{lead}A stated P90 holds {ev['coverage']['calibrated'][i90]:.1%} of outcomes below it; "
            f"the P10-P90 interval covers {ev['interval80']['calibrated']:.0%} (target 80%)."
        )
    else:
        console.print("The history is too short to replay earlier forecasts, so no accuracy table is shown.")
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
                money(r["lost_revenue"], currency),
                money(r["avg_stock_value"], currency),
                money(r["net_contribution"], currency),
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
def ask(
    question: str = typer.Argument(..., help='A question, such as "which styles are rising and are we stocked?"'),
    artifacts: Path = typer.Option(Path("artifacts"), help="Folder written by `luma run`."),
    show_steps: bool = typer.Option(False, help="Print each tool call the agent made."),
) -> None:
    """Ask the agent a question about demand, trends or stock (needs ANTHROPIC_API_KEY)."""
    from luma.agent.loop import ask as ask_agent
    from luma.agent.workspace import Workspace

    path = artifacts / "workspace.pkl"
    if not path.exists():
        raise typer.BadParameter(f"{path} not found; run `luma run` first")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        console.print("Set ANTHROPIC_API_KEY to ask questions. `luma run` and `luma trends` work without it.")
        raise typer.Exit(1)
    answer = ask_agent(Workspace.load(path), question)
    if show_steps:
        for step in answer.steps:
            console.print(f"[dim]{step.index:>2}. {step.tool} {json.dumps(step.args)}[/dim]")
            console.print(f"[dim]    {step.observation}[/dim]")
    console.print(answer.text)
    console.print(f"[dim]{len(answer.steps)} tool calls, {answer.seconds:.1f}s, {answer.model}[/dim]")


@app.command()
def trends(
    artifacts: Path = typer.Option(Path("artifacts"), help="Folder written by `luma run`."),
    all_values: bool = typer.Option(False, "--all", help="Include values with no significant movement."),
) -> None:
    """Print the trends found in the last run."""
    path = artifacts / "dashboard.json"
    if not path.exists():
        raise typer.BadParameter(f"{path} not found; run `luma run` first")
    rows = json.loads(path.read_text())["trends"]
    if not all_values:
        rows = [r for r in rows if r["status"] != "Stable"]
    if not rows:
        console.print("No attribute value is gaining or losing share of demand at the 10% false discovery rate.")
        return
    table = Table(title="Share of demand, adjusted for mix and stockouts", title_justify="left")
    for col, justify in (
        ("Attribute", "left"),
        ("Value", "left"),
        ("Status", "left"),
        ("Slope per week", "right"),
        ("Last 4 weeks", "right"),
        ("Share now", "right"),
        ("q-value", "right"),
    ):
        table.add_column(col, justify=justify)
    for r in rows:
        table.add_row(
            r["attribute"],
            r["value"],
            r["status"],
            f"{r['slope_pct_per_week']:+.1f}%",
            f"{r['change_4w']:+.0%}",
            f"{r['share']:.1%}",
            f"{r['q_value']:.3f}",
        )
    console.print(table)


@app.command()
def simulate(
    out: Path = typer.Option(Path("data"), help="Folder for the simulated tables."),
    csv: bool = typer.Option(False, help="Write CSV files, not Parquet (CSV is the default without pyarrow)."),
    small: bool = typer.Option(False),
    seed: int | None = typer.Option(None),
) -> None:
    """Generate the demo retailer and write its tables, as an example of the input format."""
    from luma.sim.world import generate_world

    world = generate_world(_settings(small, seed).sim)
    world.save(out, "csv" if csv else None)
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
        raise typer.BadParameter(f"{path} not found; run `luma run` first")
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
    live: bool = typer.Option(False, help="Run the demo pipeline at start-up and enable agent re-runs over the API."),
    small: bool = typer.Option(False, help="With --live, use the tiny world."),
) -> None:
    """Serve the dashboard and the JSON API."""
    try:
        import uvicorn

        from luma.api.app import create_app
    except ImportError as err:
        console.print('The server needs the api extra: [bold]pip install -e ".[api]"[/bold]')
        raise typer.Exit(1) from err

    result = None
    if live:
        from luma.run import run_pipeline

        result = run_pipeline(_settings(small, None), progress=lambda m: console.print(f"[dim]{m}[/dim]"))
    uvicorn.run(create_app(artifacts, result), host=host, port=port)


if __name__ == "__main__":
    app()
