"""HTTP API and dashboard host.

The API serves the latest pipeline output. In live mode (a pipeline result held in memory) it
can also re-run the agent with a different budget or service level without retraining.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from luma.data.panel import clean_json

WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"


class AgentRequest(BaseModel):
    budget: float | None = Field(None, gt=0, description="Supplier budget for this plan, in the data's currency.")
    mode: str = Field("rules", pattern="^(rules|llm|auto)$")


def create_app(artifacts: Path = Path("artifacts"), result=None, web_dist: Path | None = None) -> FastAPI:
    """Build the app. ``result`` is an optional in-memory ``PipelineResult`` for live mode."""
    app = FastAPI(title="Luma", version="0.1.0")
    state: dict = {"payload": None, "result": result}

    def payload() -> dict:
        if state["payload"] is None:
            if state["result"] is not None:
                from luma.export import build_dashboard

                state["payload"] = clean_json(build_dashboard(state["result"]))
            else:
                path = artifacts / "dashboard.json"
                if not path.exists():
                    raise HTTPException(503, f"{path} not found. Run `luma run` first.")
                state["payload"] = json.loads(path.read_text())
        return state["payload"]

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "live": state["result"] is not None}

    @app.get("/api/dashboard")
    @app.get("/data/dashboard.json")
    def dashboard() -> JSONResponse:
        return JSONResponse(payload())

    @app.get("/api/overview")
    def overview() -> dict:
        p = payload()
        return {k: p[k] for k in ("meta", "network", "categories", "stores")}

    @app.get("/api/forecast/{store}/{sku_id}")
    def forecast(store: str, sku_id: str) -> dict:
        p = payload()
        s = next(
            (i for i, row in enumerate(p["stores"]) if store.lower() in (row["id"].lower(), row["name"].lower())), None
        )
        k = next((i for i, row in enumerate(p["skus"]) if row["id"].lower() == sku_id.lower()), None)
        if s is None or k is None:
            raise HTTPException(404, "unknown store or product")
        pair = next((x for x in p["pairs"] if x["s"] == s and x["k"] == k), None)
        if pair is None:
            raise HTTPException(404, "product is not live in that store")
        names = p["meta"]["model"]["driverNames"]
        return {
            "store": p["stores"][s]["name"],
            "product": p["skus"][k],
            "weeks": p["network"]["weeks"],
            "forecast": {q: pair[q] for q in ("mean", "p10", "p25", "p50", "p75", "p90")},
            "history": {"sales": pair["sales"], "demand": pair["demand"]},
            "drivers": dict(zip(names, pair["drivers"], strict=True)),
            "sizes": [
                {"size": z, "share": pair["share"][i], "expected_14d": pair["exp14"][i]}
                | (
                    {
                        "on_hand": pair["stock"][i],
                        "inbound": pair["inbound"][i],
                        "stockout_risk_14d": pair["risk14"][i],
                        "order": pair["order"][i],
                    }
                    if "stock" in pair
                    else {}
                )
                for i, z in enumerate(p["meta"]["sizes"])
            ],
        }

    @app.get("/api/risks")
    def risks() -> list:
        return payload()["risks"]

    @app.get("/api/plan")
    def plan() -> dict:
        return payload()["plan"]

    @app.get("/api/trends")
    def trends() -> list:
        return payload()["trends"]

    @app.get("/api/anomalies")
    def anomalies() -> list:
        return payload()["anomalies"]

    @app.get("/api/evaluation")
    def evaluation() -> dict:
        return payload()["evaluation"]

    @app.get("/api/agent")
    def agent() -> dict:
        return payload()["agent"]

    @app.post("/api/agent/run")
    def run_agent_again(req: AgentRequest) -> dict:
        res = state["result"]
        if res is None:
            raise HTTPException(409, "Agent re-runs need live mode: start with `luma serve --live`.")
        from luma.agent.loop import run_agent

        ws = res.workspace
        if not ws.panel.has_stock:
            raise HTTPException(409, "This data has no stock table, so there is no plan to re-run.")
        if req.budget is not None:
            ws.settings = replace(ws.settings, policy=replace(ws.settings.policy, replenishment_budget=req.budget))
        ws.draft, ws.committed = None, None
        res.agent = run_agent(ws, mode=req.mode)
        state["payload"] = None
        p = payload()
        return {"agent": p["agent"], "plan": p["plan"]}

    dist = web_dist or WEB_DIST
    if dist.exists():

        @app.get("/")
        def index() -> FileResponse:
            return FileResponse(dist / "index.html")

        app.mount("/", StaticFiles(directory=dist), name="web")
    return app
