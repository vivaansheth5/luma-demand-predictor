import json

import pytest
from fastapi.testclient import TestClient

from demand_agent.api.app import create_app
from demand_agent.export import build_dashboard, write_outputs


@pytest.fixture(scope="module")
def artifacts(result, tmp_path_factory):
    folder = tmp_path_factory.mktemp("artifacts")
    write_outputs(result, folder)
    return folder


def test_dashboard_json_is_strict_and_complete(result, artifacts):
    text = (artifacts / "dashboard.json").read_text()
    payload = json.loads(text, parse_constant=lambda c: pytest.fail(f"non-finite constant {c} in JSON"))
    for key in ("meta", "network", "stores", "skus", "pairs", "plan", "trends", "anomalies", "evaluation", "agent"):
        assert key in payload
    assert len(payload["pairs"]) == int(result.rolling.live.forecast.active.sum())
    assert len(payload["network"]["weeks"]) == len(payload["network"]["demand"]) + 4
    pair = payload["pairs"][0]
    assert len(pair["mean"]) == 4 and len(pair["stock"]) == 4
    assert all(lo <= hi for lo, hi in zip(pair["p10"], pair["p90"], strict=True))
    assert (artifacts / "orders.csv").read_text().startswith("store,")


def test_static_api(artifacts):
    client = TestClient(create_app(artifacts))
    assert client.get("/api/health").json() == {"status": "ok", "live": False}
    overview = client.get("/api/overview").json()
    store, sku = overview["stores"][0]["name"], client.get("/api/dashboard").json()["skus"]
    pairs = client.get("/api/dashboard").json()["pairs"]
    first = next(p for p in pairs if p["s"] == 0)
    detail = client.get(f"/api/forecast/{store}/{sku[first['k']]['id']}")
    assert detail.status_code == 200 and len(detail.json()["sizes"]) == 4
    assert client.get("/api/forecast/Atlantis/TEE-001").status_code == 404
    assert client.post("/api/agent/run", json={}).status_code == 409
    assert client.get("/api/plan").json()["orders"]
    assert client.get("/api/agent").json()["steps"]


def test_live_api_reruns_the_agent_with_a_new_budget(result):
    client = TestClient(create_app(result=result))
    assert client.get("/api/health").json()["live"] is True
    before = build_dashboard(result)["plan"]["summary"]["supplier_orders"]["cost"]
    out = client.post("/api/agent/run", json={"budget": before / 2}).json()
    after = out["plan"]["summary"]["supplier_orders"]
    assert after["cost"] <= before / 2 + 1 and after["budget_binding"]
    assert client.post("/api/agent/run", json={"budget": -5}).status_code == 422
