"""MCP tools against the in-memory test chain."""

import json
from types import SimpleNamespace

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from app import mcp_server
from app.services.graph import GraphBuilder
from app.services.links import LinkAnalyzer
from app.services.risk import RiskAnalyzer
from app.services.tracer import Tracer
from tests.test_tracing import EXCHANGE, M1, M2, SCAMMER, VICTIM, env  # noqa: F401


@pytest.fixture
async def services(env, monkeypatch):
    wallets, labels, _ = await env()
    graphs = GraphBuilder(wallets, labels, 1000)
    svc = SimpleNamespace(
        providers=wallets._providers,
        wallets=wallets,
        labels=labels,
        graphs=graphs,
        tracer=Tracer(wallets, labels, 1000),
        risk=RiskAnalyzer(wallets, labels, graphs),
        links=SimpleNamespace(analyzer=LinkAnalyzer(wallets, labels, 1000)),
    )
    monkeypatch.setattr(mcp_server, "_services", svc)
    return svc


async def test_tools_are_registered():
    names = {t.name for t in await mcp_server.mcp.list_tools()}
    assert {"wallet_overview", "counterparties", "fund_flow_graph", "trace_funds", "risk_report"} <= names


async def test_counterparties_and_graph(services):
    data = json.loads(await mcp_server.counterparties(SCAMMER, direction="out"))
    assert {i["address"] for i in data["items"]} == {M1, M2} and data["total_counterparties"] == 2
    graph = json.loads(await mcp_server.fund_flow_graph(SCAMMER, depth_in=1, depth_out=2))
    exchange = next(n for n in graph["nodes"] if n["address"] == EXCHANGE)
    assert exchange["label"] == "Test Exchange [exchange]"


async def test_trace_funds(services):
    result = json.loads(await mcp_server.trace_funds(VICTIM, "s1", method="lifo"))
    assert result["traced_amount"] == "1000"
    assert result["endpoints"][0]["label"] == "Test Exchange [exchange]"


async def test_errors_are_tool_errors(services):
    with pytest.raises(ToolError, match="unrecognized address"):
        await mcp_server.wallet_overview("nonsense")
    with pytest.raises(ToolError, match="no transfer"):
        await mcp_server.trace_funds(VICTIM, "missing")


async def test_wallet_links(services):
    data = json.loads(await mcp_server.wallet_links([VICTIM, SCAMMER, M1, M2]))
    pairs = {(d["from_address"], d["to_address"]) for d in data["direct"]}
    assert (VICTIM, SCAMMER) in pairs and (SCAMMER, M1) in pairs
    assert len(data["groups"]) == 1
    with pytest.raises(ToolError, match="at least two"):
        await mcp_server.wallet_links([VICTIM])
