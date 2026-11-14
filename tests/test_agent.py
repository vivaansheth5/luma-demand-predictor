from dataclasses import replace
from types import SimpleNamespace

import pytest

from demand_agent.agent.loop import run_llm, run_rules
from demand_agent.agent.tools import Toolbox
from demand_agent.agent.workspace import build_workspace


@pytest.fixture()
def ws(result):
    live = result.rolling.live
    return build_workspace(result.panel, result.settings, live.uncensored, result.rolling.model, live.forecast)


def test_rules_agent_commits_a_plan_that_passes_guardrails(ws):
    run = run_rules(ws)
    assert run.status == "committed" and ws.committed is not None
    tools = [s.tool for s in run.steps]
    assert tools[0] == "network_overview" and tools[-1] == "commit_plan"
    assert tools.index("draft_plan") < tools.index("check_guardrails")
    assert all(c["status"] != "fail" for c in ws.committed["guardrails"])
    assert run.brief["headline"] and run.brief["findings"]
    spend = sum(line["cost"] for line in ws.committed["orders"])
    assert spend <= ws.settings.policy.replenishment_budget * 1.001


def test_agent_replans_when_the_budget_fails(ws):
    ws.settings = replace(ws.settings, policy=replace(ws.settings.policy, replenishment_budget=30_000.0))
    run = run_rules(ws)
    drafts = [s for s in run.steps if s.tool == "draft_plan"]
    assert len(drafts) == 2 and drafts[0].args["budget"] is None and drafts[1].args["budget"] == 30_000.0
    assert run.status == "committed"
    assert ws.draft.replenishment.budget_binding


def test_commit_refuses_a_plan_over_budget(ws):
    ws.settings = replace(ws.settings, policy=replace(ws.settings.policy, replenishment_budget=1_000.0))
    box = Toolbox(ws)
    box.call("draft_plan", {"budget": None})
    assert not box.call("check_guardrails")["passed"]
    out = box.call("commit_plan")
    assert out["committed"] is False and ws.committed is None


def test_tools_expose_valid_schemas_and_handle_bad_input(ws):
    box = Toolbox(ws)
    for spec in box.specs():
        assert spec["input_schema"]["type"] == "object" and spec["description"]
    with pytest.raises(KeyError):
        box.call("explain_forecast", {"store": "Atlantis", "sku_id": "TEE-001"})
    top = box.call("scan_stockout_risk", {"limit": 3})["top"]
    assert len(top) <= 3
    if top:
        detail = box.call("explain_forecast", {"store": top[0]["store"], "sku_id": top[0]["sku_id"]})
        assert (
            len(detail["sizes"]) == 4 and detail["forecast"]["next_14d"]["p10"] <= detail["forecast"]["next_14d"]["p90"]
        )


class FakeClaude:
    """Replays a scripted tool-use conversation in the shape of the Messages API."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        step = self.script.pop(0)
        if isinstance(step, str):
            return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=step)])
        name, args = step
        return SimpleNamespace(
            stop_reason="tool_use",
            content=[
                SimpleNamespace(type="text", text=f"I will call {name}."),
                SimpleNamespace(type="tool_use", id=f"toolu_{len(self.calls)}", name=name, input=args),
            ],
        )


def test_llm_loop_drives_tools_and_returns_tool_results(ws):
    budget = ws.settings.policy.replenishment_budget
    client = FakeClaude(
        [
            ("network_overview", {}),
            ("explain_forecast", {"store": "Atlantis", "sku_id": "TEE-001"}),  # a bad call must not crash the loop
            ("draft_plan", {"budget": budget}),
            ("check_guardrails", {}),
            ("commit_plan", {}),
            "Plan committed. Review the queued lines.",
        ]
    )
    run = run_llm(ws, client=client, model="test-model")
    assert run.mode == "llm" and run.status == "committed"
    assert [s.tool for s in run.steps] == [
        "network_overview",
        "explain_forecast",
        "draft_plan",
        "check_guardrails",
        "commit_plan",
    ]
    assert run.brief["narrative"].startswith("Plan committed")
    assert {t["name"] for t in client.calls[0]["tools"]} >= {"draft_plan", "commit_plan"}
    # Each tool result goes back as a user turn, keyed to the tool call, with errors flagged.
    reply = client.calls[2]["messages"][-1]
    assert reply["role"] == "user" and reply["content"][0]["type"] == "tool_result"
    assert reply["content"][0]["tool_use_id"] == "toolu_2" and reply["content"][0]["is_error"] is True
