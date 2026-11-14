"""The agent loop: observe, diagnose, plan, verify, repair, commit, report.

Two planners drive the same tools under the same guardrails.

* ``run_rules`` is a deterministic controller. It needs no API key, behaves identically on every
  run, and is what the tests and the closed-loop evaluation rely on. It is not a fixed script:
  what it inspects and how often it re-plans depend on what the tools return.
* ``run_llm`` hands the tools to Claude. The model decides what to look at and writes the
  brief, while every number still comes from the tools and every plan still has to pass
  ``check_guardrails`` before ``commit_plan`` accepts it.

``ask`` answers a free-form question ("what is trending in outerwear?", "why is this hoodie
at risk?") with the same tools, without committing anything.

All three return a record whose trace holds each thought, tool call and observation.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from luma.agent.tools import Toolbox
from luma.agent.workspace import Workspace
from luma.money import money

DEFAULT_MODEL = "claude-sonnet-5-5"
MAX_REPLANS = 3


@dataclass
class Step:
    index: int
    thought: str
    tool: str
    args: dict
    observation: str
    result: dict
    seconds: float


@dataclass
class AgentRun:
    mode: str  # "rules" or "llm"
    model: str | None
    status: str  # "committed", "forecast_only" or "needs_human"
    steps: list[Step] = field(default_factory=list)
    brief: dict = field(default_factory=dict)
    seconds: float = 0.0


@dataclass
class Answer:
    question: str
    text: str
    model: str
    steps: list[Step] = field(default_factory=list)
    seconds: float = 0.0


def signed(value: float) -> str:
    """Whole signed percentage without a negative zero: +12%, -8%, 0%."""
    r = round(value)
    return "0%" if r == 0 else f"{r:+d}%"


def summarise(tool: str, r: dict, currency: str = "INR") -> str:
    """One line describing a tool result, for the trace."""

    def cash(value: float) -> str:
        return money(value, currency)

    if "error" in r:
        return str(r["error"])
    if tool == "network_overview":
        change = 100.0 * (r["forecast_next_28d"] / max(r["demand_last_28d"], 1) - 1.0)
        events = ", ".join(r["events_next_28d"][:3]) or "no calendar events"
        n = len(r["stores"])
        hidden = r["estimated_lost_to_stockouts_last_28d_pct"]
        return (
            f"{n} {'store' if n == 1 else 'stores'}. Demand was {r['demand_last_28d']:,} units over the last 28 "
            f"days; forecast is {r['forecast_next_28d']:,} for the next 28 ({signed(change)}). Ahead: {events}."
            + (f" Stockouts hid an estimated {hidden}% of recent demand." if hidden is not None else "")
        )
    if tool == "scan_anomalies":
        noun = "anomaly" if r["count"] == 1 else "anomalies"
        return f"{r['count']} {noun} in {r['window_days']} days, {r['unexplained']} without a known cause."
    if tool == "scan_trends":
        names = ", ".join(f"{t['value']} ({t['status'].lower()})" for t in r["trends"][:4]) or "none"
        noun = "attribute is" if r["count"] == 1 else "attributes are"
        return f"{r['count']} {noun} moving: {names}."
    if tool == "scan_stockout_risk":
        top = r["top"][0] if r["top"] else None
        lead = f" Largest exposure: {top['product']} in {top['store']}." if top else ""
        return (
            f"{r['cells_above_50pct_risk']} size-level cells are more likely than not to sell out within "
            f"{r['horizon_days']} days; {cash(r['expected_lost_revenue_total'])} of sales at risk.{lead}"
        )
    if tool == "explain_forecast":
        f14 = r["forecast"]["next_14d"]
        drivers = ", ".join(f"{d['driver'].lower()} {signed(d['effect_pct'])}" for d in r["drivers"][:3])
        out = [s["size"] for s in r["sizes"] if "on_hand" in s and s["on_hand"] + s["inbound"] == 0]
        gap = f" Sizes with no stock: {', '.join(out)}." if out else ""
        return (
            f"{r['product']} in {r['store']}: expects {f14['expected']} units in 14 days "
            f"(P10 {f14['p10']}, P90 {f14['p90']}). Drivers: {drivers or 'run-rate only'}.{gap}"
        )
    if tool == "find_overstock":
        return (
            f"{r['pairs']} product-store pairs hold more than 12 weeks of cover, worth {cash(r['stock_value_total'])}."
        )
    if tool == "draft_plan":
        t, o, i = r["transfers"], r["supplier_orders"], r["expected_impact"]
        cap = " Budget is binding." if o["budget_binding"] else ""
        return (
            f"Move {t['units']} units over {t['lanes']} lanes ({cash(t['cost'])}); order {o['units']:,} units on "
            f"{o['lines']} lines for {cash(o['cost'])}.{cap} Expected unmet demand over 14 days falls from "
            f"{i['lost_units_14d'][0]:,} to {i['lost_units_14d'][1]:,} units."
        )
    if tool == "check_guardrails":
        bad = [c for c in r["checks"] if c["status"] == "fail"]
        warn = [c for c in r["checks"] if c["status"] == "warn"]
        if bad:
            return "Failed: " + " ".join(f"{c['check']}: {c['detail']}" for c in bad)
        tail = f" {len(warn)} warning(s): " + "; ".join(c["check"] for c in warn) + "." if warn else ""
        return f"All {len(r['checks'])} checks passed.{tail}"
    if tool == "commit_plan":
        if not r.get("committed"):
            return str(r.get("error", "Not committed."))
        return (
            f"Committed {r['order_lines']} order lines ({r['auto_approved']} approved automatically, "
            f"{r['queued_for_review']} queued for review, {cash(r['review_value'])}) and {r['transfer_moves']} "
            f"transfer moves on {r['transfer_lanes']} lanes."
        )
    return json.dumps(r)[:200]


class _Trace:
    def __init__(self, toolbox: Toolbox) -> None:
        self.toolbox = toolbox
        self.currency = toolbox.ws.panel.currency
        self.steps: list[Step] = []

    def call(self, thought: str, tool: str, **args: Any) -> dict:
        started = time.perf_counter()
        try:
            result = self.toolbox.call(tool, args)
        except (KeyError, TypeError, ValueError) as exc:
            result = {"error": f"{type(exc).__name__}: {exc}"}
        elapsed = time.perf_counter() - started
        self.steps.append(
            Step(len(self.steps) + 1, thought, tool, args, summarise(tool, result, self.currency), result, elapsed)
        )
        return result


def _attribute_word(ws: Workspace) -> str:
    """How to refer to the trend attribute in a sentence: "styles", "collections", "attributes"."""
    name = ws.panel.trend_attribute
    return f"{name}s" if name else "attributes"


def run_rules(ws: Workspace) -> AgentRun:
    """The deterministic planner."""
    started = time.perf_counter()
    toolbox = Toolbox(ws)
    trace = _Trace(toolbox)
    policy = ws.settings.policy
    attr = ws.panel.trend_attribute

    overview = trace.call("Start with the state of the network and what the calendar holds.", "network_overview")
    anomalies = trace.call(
        "Before trusting recent sales, check whether any of them were abnormal. An unexplained spike would "
        "inflate a run-rate and anything planned from it.",
        "scan_anomalies",
        days=21,
    )
    trends = trace.call(
        f"Check which {_attribute_word(ws)} are gaining or losing share, since a trend changes what the same "
        "stock is worth four weeks from now.",
        "scan_trends",
    )
    rising = [t for t in trends.get("trends", []) if t["attribute"] == attr and t["status"] in ("Emerging", "Rising")]

    if not ws.panel.has_stock:
        # Without a stock position the agent can forecast and detect, but not plan orders.
        for row in toolbox.movers(limit=2):
            trace.call(
                f"{row['product']} has the largest forecast change in {row['store']}. Check what is driving it.",
                "explain_forecast",
                store=row["store"],
                sku_id=row["sku_id"],
            )
        run = AgentRun(mode="rules", model=None, status="forecast_only", steps=trace.steps)
        run.brief = compose_brief(ws, overview, anomalies, trends, {})
        run.seconds = time.perf_counter() - started
        return run

    risk = trace.call(
        "Find where a sale will be lost first: products likely to run out before a supplier order can land.",
        "scan_stockout_risk",
        horizon="week",
        limit=8,
    )
    for row in risk.get("top", [])[:2]:
        trace.call(
            f"Understand why {row['product']} in {row['store']} is exposed before ordering against it.",
            "explain_forecast",
            store=row["store"],
            sku_id=row["sku_id"],
        )
    if rising:
        value = rising[0]["value"]
        match = next(
            (r for r in toolbox.risk_table(horizon="cycle") if str(ws.panel.catalog[attr].iloc[r["_k"]]) == value),
            None,
        )
        if match:
            trace.call(
                f"{value.capitalize()} is {rising[0]['status'].lower()}. Check that the forecast for an exposed "
                f"{value} product reflects it.",
                "explain_forecast",
                store=match["store"],
                sku_id=match["sku_id"],
            )

    plan = trace.call(
        "Draft the plan that meets every service-level target, ignoring budget, to see what full protection costs.",
        "draft_plan",
        budget=None,
        use_transfers=True,
    )
    status = "needs_human"
    use_transfers, budget = True, None
    for _ in range(MAX_REPLANS + 1):
        check = trace.call(
            "Verify the draft against the operating guardrails before committing anything.", "check_guardrails"
        )
        if check["passed"]:
            status = "ready"
            break
        failed = {c["check"] for c in check["checks"] if c["status"] == "fail"}
        if "Supplier budget" in failed and budget is None:
            budget = policy.replenishment_budget
            thought = (
                f"The draft costs {ws.money(plan['supplier_orders']['cost'])}, above the {ws.money(budget)} "
                "cap. Redraft under the cap; units are then bought in order of expected margin per unit of spend, "
                "so the cut removes the units least likely to sell."
            )
        elif failed & {"Donor stores stay protected", "Trip capacity per lane"} and use_transfers:
            use_transfers = False
            thought = "A transfer check failed. Redraft with supplier orders only."
        else:
            break
        plan = trace.call(thought, "draft_plan", budget=budget, use_transfers=use_transfers)

    trace.call(
        "Look at the other side of the shelf: stock that demand will not clear, which ties up cash the plan needs.",
        "find_overstock",
        limit=8,
    )
    if status == "ready":
        done = trace.call(
            "The draft passes every hard check. Commit it and route exceptions to a person.", "commit_plan"
        )
        status = "committed" if done.get("committed") else "needs_human"

    run = AgentRun(mode="rules", model=None, status=status, steps=trace.steps)
    run.brief = compose_brief(ws, overview, anomalies, trends, risk)
    run.seconds = time.perf_counter() - started
    return run


def _concentration(ws: Workspace) -> str:
    """A caution when one product accounts for most of a large change in the forecast.

    A total that moves because of one young, fast-growing product is the forecast a reader most
    needs to question, so the brief names it.
    """
    past = ws.uncensored.by_sku[-28:].sum(axis=(0, 1))
    ahead = np.nan_to_num(ws.forecast.window(1, 28)[0], nan=0.0).sum(axis=0)
    delta = ahead - past
    total = float(delta.sum())
    if abs(total) < 0.2 * max(float(past.sum()), 1.0):
        return ""
    k = int(np.argmax(delta * np.sign(total)))
    if delta[k] * np.sign(total) < 0.5 * abs(total):
        return ""
    panel = ws.panel
    age = int(panel.n_days - max(int(panel.catalog["launch_day"].iloc[k]), 0))
    history = f"{age} days" if age < 84 else f"{age // 7} weeks"
    return (
        f"More than half of that change comes from one product, {panel.catalog['name'].iloc[k]}, "
        f"forecast at {ahead[k]:,.0f} units against {past[k]:,.0f} in the last 28 days on {history} of history. "
        "Check that forecast before acting on the total. "
    )


def compose_brief(ws: Workspace, overview: dict, anomalies: dict, trends: dict, risk: dict) -> dict:
    """The agent's written brief, built from tool results."""
    committed = ws.committed
    cash = ws.money
    attr = ws.panel.trend_attribute
    findings: list[dict] = []

    change = 100.0 * (overview["forecast_next_28d"] / max(overview["demand_last_28d"], 1) - 1.0)
    movers = sorted(overview["categories"], key=lambda c: -abs(c["change_pct"]))[:2]
    events = overview["events_next_28d"]
    concentration = _concentration(ws)
    findings.append(
        {
            "kind": "demand",
            "title": (
                f"Demand forecast: {overview['forecast_next_28d']:,} units over the next 28 days ({signed(change)})"
            ),
            "body": " ".join(
                part
                for part in (
                    f"The last 28 days saw {overview['demand_last_28d']:,} units of demand.",
                    "Largest category moves: "
                    + ", ".join(f"{m['category'].lower()} {signed(m['change_pct'])}" for m in movers)
                    + "."
                    if len(overview["categories"]) > 1
                    else "",
                    f"Calendar ahead: {', '.join(events[:3])}." if events else "",
                    concentration.strip(),
                )
                if part
            ),
        }
    )
    if risk.get("top"):
        top = risk["top"][0]
        n = risk["cells_above_50pct_risk"]
        findings.append(
            {
                "kind": "risk",
                "title": f"{n} {'size is' if n == 1 else 'sizes are'} likely to sell out this week",
                "body": (
                    f"{cash(risk['expected_lost_revenue_total'])} of sales is at risk before a supplier order can "
                    f"arrive. The largest single exposure is {top['product']} in {top['store']}: "
                    f"{top['on_hand']} units on hand against {top['expected_demand']} expected this week"
                    + (f", with size {', '.join(top['sizes_out'])} already out." if top["sizes_out"] else ".")
                ),
            }
        )
    moving = [t for t in trends.get("trends", []) if t["attribute"] == attr] or trends.get("trends", [])
    if moving:
        parts = []
        for t in moving[:3]:
            buzz = f", buzz {signed(t['buzz_momentum_pct'])}" if t["buzz_momentum_pct"] is not None else ""
            parts.append(
                f"{t['value'].capitalize()} is {t['status'].lower()} "
                f"(share {signed(t['share_change_4w_pct'])} in 4 weeks{buzz})."
            )
        findings.append({"kind": "trend", "title": "Trends in motion", "body": " ".join(parts)})
    unexplained = [a for a in anomalies.get("anomalies", []) if a["cause"] == "unexplained"]
    if unexplained:
        a = unexplained[0]
        findings.append(
            {
                "kind": "anomaly",
                "title": (
                    f"{len(unexplained)} unexplained {'anomaly' if len(unexplained) == 1 else 'anomalies'} "
                    f"in the last {anomalies['window_days']} days"
                ),
                "body": (
                    f"Most recent: {a['name']} on {a['date']}, a {a['direction']} of {a['actual_units']} units against "
                    f"{a['expected_units']} expected."
                    + (" Orders that depend on an unexplained spike are routed to review." if committed else "")
                ),
            }
        )

    stats: dict = {}
    if committed:
        s = committed["summary"]
        orders, transfers, impact = s["supplier_orders"], s["transfers"], s["expected_impact"]
        review = [r for r in committed["orders"] if r["approval"] == "review"]
        move = f" and move {transfers['units']:,} between stores" if transfers["units"] else ""
        headline = (
            f"Order {orders['units']:,} units{move} to protect "
            f"{cash(impact['revenue_protected'])} of sales over the next 14 days."
        )
        deferred = orders["cost_without_budget"] - orders["cost"]
        body = f"{orders['lines']} order lines; {len(review)} need a person's approval. "
        if orders["budget_binding"]:
            body += f"The budget is binding, so {cash(deferred)} of the lowest-value units is deferred. "
        if transfers["units"]:
            body += (
                f"Transfers cost {cash(transfers['cost'])} and are expected to recover "
                f"{cash(transfers['expected_margin_recovered'])} of margin this week. "
            )
        body += (
            f"Sizes above 50% stockout risk fall from {impact['cells_above_50pct_risk'][0]} "
            f"to {impact['cells_above_50pct_risk'][1]}."
        )
        title = f"Plan: {cash(orders['cost'])} of supplier orders"
        if transfers["lanes"]:
            title += f", {transfers['lanes']} transfer lanes"
        findings.append({"kind": "plan", "title": title, "body": body})
        stats = {
            "order_units": orders["units"],
            "order_cost": orders["cost"],
            "order_lines": orders["lines"],
            "review_lines": len(review),
            "transfer_units": transfers["units"],
            "transfer_lanes": transfers["lanes"],
            "revenue_protected": impact["revenue_protected"],
            "risk_cells_before": impact["cells_above_50pct_risk"][0],
            "risk_cells_after": impact["cells_above_50pct_risk"][1],
        }
    elif not ws.panel.has_stock:
        lead = moving[0] if moving else None
        headline = (
            f"Demand is forecast at {overview['forecast_next_28d']:,} units over the next 28 days "
            f"({signed(change)} on the last 28)"
            + (f", and {lead['value']} is {lead['status'].lower()}." if lead else ".")
        )
        findings.append(
            {
                "kind": "watch",
                "title": "Add a stock table to get order plans",
                "body": (
                    "These forecasts come from sales alone. With a stock table (sku_id and on_hand), the agent also "
                    "measures stockout risk by size and drafts supplier orders within a budget."
                ),
            }
        )
    else:
        headline = "No plan was committed. A person needs to review the failed checks."

    over = ws.notes.get("overstock", [])
    if over:
        value = sum(r["stock_value"] for r in over)
        findings.append(
            {
                "kind": "watch",
                "title": f"{cash(value)} of stock is not moving",
                "body": (
                    f"{len(over)} product-store pairs hold more than 12 weeks of cover. The largest is "
                    f"{over[0]['product']} in {over[0]['store']} ({over[0]['weeks_of_cover']:.0f} weeks). "
                    "No orders are placed for these; they are candidates for markdown."
                ),
            }
        )
    return {"headline": headline, "findings": findings, "stats": stats, "narrative": None}


SYSTEM_PROMPT = """You are Luma, the demand and inventory agent for a fashion retailer.
Once a week you decide what each store should hold for the coming two weeks.

Work in this order:
1. Read the network overview, then check anomalies, trends and stockout risk. Look into individual
   products when a number needs explaining.
2. Draft a plan with draft_plan, then run check_guardrails. If a check fails, redraft to fix it.
   A budget failure is fixed by passing the budget to draft_plan. A transfer failure is fixed by
   setting use_transfers to false.
3. When the checks pass, call commit_plan.
4. Finish with a brief for the operations lead: one headline sentence, then at most five short
   paragraphs covering demand, the main risks, trends, the plan and what needs a person's attention.

If a tool reports that no stock table was supplied, skip steps 2 and 3 and brief on demand and trends.

Rules: every number you state must come from a tool result. Do not invent figures. Do not commit
a plan that fails a guardrail. Write plain, formal sentences."""

ASK_PROMPT = """You are Luma, an analyst for a fashion retailer. Answer the user's question about
demand, trends, stock risk or the forecast, using the tools.

Rules: every number you state must come from a tool result. Do not invent figures. Use as few tool
calls as the question needs. You may draft a plan to answer a what-if question, but you cannot
commit one. Answer in plain sentences, lead with the answer, and keep it short."""


def _client(client: Any | None) -> Any:
    if client is not None:
        return client
    import anthropic  # imported lazily: the dependency is optional

    return anthropic.Anthropic()


def _converse(
    toolbox: Toolbox,
    client: Any,
    model: str,
    system: str,
    prompt: str,
    tools: list[dict],
    max_steps: int,
) -> tuple[str, list[Step]]:
    """Run the Messages API tool-use loop until the model stops calling tools."""
    trace = _Trace(toolbox)
    allowed = {t["name"] for t in tools}
    messages: list[dict] = [{"role": "user", "content": prompt}]
    for _ in range(max_steps):
        response = client.messages.create(model=model, max_tokens=2000, system=system, tools=tools, messages=messages)
        text = " ".join(b.text for b in response.content if b.type == "text").strip()
        if response.stop_reason != "tool_use":
            return text, trace.steps
        messages.append({"role": "assistant", "content": response.content})
        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            if block.name in allowed:
                result = trace.call(text or f"Call {block.name}.", block.name, **(block.input or {}))
            else:
                result = {"error": f"{block.name} is not available here"}
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result, default=str)[:12000],
                    **({"is_error": True} if "error" in result else {}),
                }
            )
            text = ""
        messages.append({"role": "user", "content": results})
    return "", trace.steps


def run_llm(
    ws: Workspace,
    client: Any | None = None,
    model: str | None = None,
    max_steps: int = 24,
) -> AgentRun:
    """Let Claude drive the tools. Requires the ``anthropic`` package and an API key."""
    started = time.perf_counter()
    model = model or os.environ.get("LUMA_MODEL", DEFAULT_MODEL)
    toolbox = Toolbox(ws)
    prompt = f"It is the evening of {ws.panel.asof.isoformat()}. Run this week's cycle."
    narrative, steps = _converse(toolbox, _client(client), model, SYSTEM_PROMPT, prompt, toolbox.specs(), max_steps)

    # The structured findings are always rebuilt from the tools, whatever the model looked at.
    risk = toolbox.call("scan_stockout_risk") if ws.panel.has_stock else {}
    brief = compose_brief(
        ws, toolbox.call("network_overview"), toolbox.call("scan_anomalies"), toolbox.call("scan_trends"), risk
    )
    brief["narrative"] = narrative or None
    if ws.committed:
        status = "committed"
    else:
        status = "needs_human" if ws.panel.has_stock else "forecast_only"
    return AgentRun(
        mode="llm", model=model, status=status, steps=steps, brief=brief, seconds=time.perf_counter() - started
    )


def ask(
    ws: Workspace,
    question: str,
    client: Any | None = None,
    model: str | None = None,
    max_steps: int = 12,
) -> Answer:
    """Answer a question about demand, trends or stock with the agent's tools. Nothing is committed."""
    started = time.perf_counter()
    model = model or os.environ.get("LUMA_MODEL", DEFAULT_MODEL)
    toolbox = Toolbox(ws)
    tools = [t for t in toolbox.specs() if t["name"] != "commit_plan"]
    draft, committed = ws.draft, ws.committed
    try:
        prompt = f"As of {ws.panel.asof.isoformat()}: {question}"
        text, steps = _converse(toolbox, _client(client), model, ASK_PROMPT, prompt, tools, max_steps)
    finally:
        ws.draft, ws.committed = draft, committed  # a what-if draft must not replace the real plan
    return Answer(question=question, text=text, model=model, steps=steps, seconds=time.perf_counter() - started)


def run_agent(ws: Workspace, mode: str = "auto") -> AgentRun:
    """Run the agent. ``mode`` is "rules", "llm" or "auto" (LLM when an API key is present)."""
    if mode == "auto":
        mode = "llm" if os.environ.get("ANTHROPIC_API_KEY") else "rules"
    if mode == "llm":
        return run_llm(ws)
    if mode != "rules":
        raise ValueError(f"unknown agent mode {mode!r}")
    return run_rules(ws)
