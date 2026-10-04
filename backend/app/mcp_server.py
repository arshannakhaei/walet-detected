"""MCP server exposing ChainTrace analysis as tools for Claude and other MCP clients.

Runs over stdio and builds its own services (same database and settings as
the web app), so the web server does not need to be running.

    python mcp_server.py            (from the project root)

Outputs are trimmed to what an assistant needs; use the dashboard for full data.
"""

import functools
import json
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from app.config import get_settings
from app.container import Services, create_services
from app.models import Chain, Direction
from app.providers import ProviderError
from app.services.addresses import detect_chains, resolve_address
from app.services.graph import GraphParams
from app.services.labels import LabelCategory
from app.services.links import LinkParams, resolve_members
from app.services.tracer import LotMethod, TraceDirection, TraceParams, TraceStartNotFound
from app.services.wallet import TransferFilter, UnsupportedChainError

_services: Services | None = None


@asynccontextmanager
async def lifespan(_server: MCPServer):
    global _services
    _services = await create_services(get_settings())
    try:
        yield {}
    finally:
        await _services.close()
        _services = None


mcp = MCPServer(
    name="chaintrace",
    instructions=(
        "Blockchain wallet investigation tools. Supported chains: tron, ethereum, bsc, polygon, "
        "arbitrum, optimism, base, avalanche, bitcoin, solana (the chain is detected from the "
        "address; pass `chain` for EVM addresses on chains other than Ethereum). Start with "
        "wallet_overview, then counterparties / fund_flow_graph to see who sent and received "
        "money, trace_funds to follow a specific transaction hop by hop, risk_report for "
        "suspicious patterns, and wallet_links to find how a list of wallets are connected. Amount tracing is heuristic: report confidence values."
    ),
    lifespan=lifespan,
)


def tool(fn):
    """Register an MCP tool whose expected failures reach the model as readable errors."""

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except (ProviderError, UnsupportedChainError, TraceStartNotFound, ValueError) as exc:
            raise ToolError(str(exc)) from exc

    return mcp.tool()(wrapper)


def _svc() -> Services:
    if _services is None:
        raise RuntimeError("services not initialised")
    return _services


def _target(address: str, chain: str | None) -> tuple[Chain, str]:
    return resolve_address(address, Chain(chain) if chain else None, _svc().providers.supported_chains)


def _dump(value: Any) -> str:
    """JSON text with Decimals as strings, as MCP text content."""

    def default(o):
        if isinstance(o, Decimal):
            return format(o.normalize(), "f")
        if hasattr(o, "isoformat"):
            return o.isoformat()
        if hasattr(o, "value"):
            return o.value
        raise TypeError(type(o))

    return json.dumps(value, default=default, ensure_ascii=False, indent=1)


async def _toman_rate() -> Decimal | None:
    values = getattr(_svc(), "values", None)
    return (await values.toman_now()).rate if values is not None else None


def _label(chain: Chain, address: str) -> str | None:
    label = _svc().labels.get(chain, address)
    return f"{label.name} [{label.category.value}]" if label else None


@tool
async def detect_address(address: str) -> str:
    """Which blockchain(s) an address belongs to, and which are enabled in this installation."""
    supported = _svc().providers.supported_chains
    return _dump([{"chain": c, "enabled": c in supported} for c in detect_chains(address.strip())])


@tool
async def wallet_overview(address: str, chain: str | None = None) -> str:
    """Balances (with USD and toman), first/last activity, number of transfers and counterparties,
    and total in/out per token for a wallet."""
    c, a = _target(address, chain)
    o = await _svc().wallets.overview(c, a)
    data = o.model_dump()
    data["label"] = _label(c, a)
    rate = await _toman_rate()
    data["toman_per_usd_today"] = rate
    if rate:
        data["total_toman"] = o.total_usd * rate if o.total_usd is not None else None
    data["flows"] = data["flows"][:15]
    return _dump(data)


@tool
async def counterparties(
    address: str,
    chain: str | None = None,
    direction: str | None = None,
    token: str | None = None,
    min_amount: float | None = None,
    limit: int = 20,
) -> str:
    """Who sent money to this wallet and who received money from it, per token, largest first.
    direction: 'in' (senders only) or 'out' (receivers only)."""
    c, a = _target(address, chain)
    flt = TransferFilter(
        token=token,
        direction=Direction(direction) if direction else None,
        min_amount=Decimal(str(min_amount)) if min_amount is not None else None,
    )
    rows = await _svc().wallets.counterparties(c, a, flt)
    out = []
    for cp in rows[: max(1, min(limit, 200))]:
        item = cp.model_dump()
        item["label"] = _label(c, cp.address)
        out.append(item)
    return _dump({"total_counterparties": len(rows), "items": out})


@tool
async def transfers(
    address: str,
    chain: str | None = None,
    token: str | None = None,
    direction: str | None = None,
    min_amount: float | None = None,
    counterparty: str | None = None,
    limit: int = 50,
) -> str:
    """Individual transfers of a wallet, newest first, with filters."""
    c, a = _target(address, chain)
    flt = TransferFilter(
        token=token,
        direction=Direction(direction) if direction else None,
        min_amount=Decimal(str(min_amount)) if min_amount is not None else None,
        counterparty=_target(counterparty, c.value)[1] if counterparty else None,
    )
    views, total = await _svc().wallets.transfers(c, a, flt, max(1, min(limit, 500)), 0)
    items = [
        {
            "time": v.transfer.timestamp,
            "tx": v.transfer.tx_hash,
            "direction": v.direction,
            "counterparty": v.counterparty,
            "counterparty_label": _label(c, v.counterparty),
            "amount": v.transfer.amount,
            "token": v.transfer.token_symbol,
        }
        for v in views
    ]
    return _dump({"total": total, "items": items})


@tool
async def fund_flow_graph(
    address: str,
    chain: str | None = None,
    depth_in: int = 1,
    depth_out: int = 2,
    max_nodes: int = 60,
    token: str | None = None,
    min_amount: float | None = None,
) -> str:
    """Multi-layer money flow around a wallet: senders (and their senders) up to depth_in,
    receivers (and their receivers) up to depth_out. Exchanges and very busy wallets are
    not expanded. Returns nodes with depth (negative = sources) and aggregated edges."""
    c, a = _target(address, chain)
    params = GraphParams(
        depth_in=max(0, min(depth_in, 4)),
        depth_out=max(0, min(depth_out, 4)),
        max_nodes=max(2, min(max_nodes, 300)),
        max_children=10,
        filter=TransferFilter(
            token=token, min_amount=Decimal(str(min_amount)) if min_amount is not None else None
        ),
    )
    g = await _svc().graphs.build(c, a, params)
    nodes = [
        {
            "address": n.address,
            "depth": n.depth,
            "label": _label(c, n.address),
            "hub": n.is_hub,
            "not_expanded_because": n.stop_reason,
        }
        for n in g.nodes
    ]
    edges = [
        {
            "from": e.from_address,
            "to": e.to_address,
            "amount": e.amount,
            "token": e.token_symbol,
            "count": e.count,
            "first": e.first_seen,
            "last": e.last_seen,
        }
        for e in sorted(g.edges, key=lambda e: e.amount, reverse=True)
    ]
    return _dump({"root": g.root, "truncated": g.truncated, "nodes": nodes, "edges": edges})


@tool
async def trace_funds(
    address: str,
    tx_hash: str,
    direction: str = "forward",
    method: str = "fifo",
    amount: float | None = None,
    token: str | None = None,
    max_hops: int = 6,
    min_amount: float = 1,
    chain: str | None = None,
) -> str:
    """Follow the money of one transaction hop by hop.
    address: a wallet that sent or received the transaction. direction: 'forward' (where did it
    go) or 'backward' (where did it come from). method: 'fifo' or 'lifo' lot accounting after
    exact-amount matches. Returns where the money ended (exchange, still in wallet, hub, ...)
    with confidence, and the flows of the path."""
    c, a = _target(address, chain)
    s = _svc()
    start = await s.tracer.find_start(c, a, tx_hash.strip(), token)
    params = TraceParams(
        direction=TraceDirection(direction),
        method=LotMethod(method),
        max_hops=max(1, min(max_hops, 15)),
        min_amount=Decimal(str(min_amount)),
    )
    r = await s.tracer.trace(c, start, params, Decimal(str(amount)) if amount else None)
    value = None
    if getattr(s, "values", None) is not None:
        [value] = await s.values.values([(c, r.token_symbol, r.token_contract, r.traced_amount, r.start.timestamp)])
    return _dump(
        {
            "traced_value": value.model_dump() if value else None,
            "start": {
                "tx": r.start.tx_hash,
                "from": r.start.from_address,
                "to": r.start.to_address,
                "amount": r.start.amount,
                "token": r.token_symbol,
            },
            "traced_amount": r.traced_amount,
            "summary_by_outcome": r.summary,
            "endpoints": [
                {
                    "address": e.address,
                    "label": _label(c, e.address),
                    "outcome": e.reason,
                    "amount": e.amount,
                    "confidence": round(float(e.confidence), 3),
                }
                for e in r.endpoints[:30]
            ],
            "flows": [
                {
                    "hop": f.hop,
                    "from": f.from_address,
                    "to": f.to_address,
                    "traced": f.traced_amount,
                    "of_transfer": f.transfer_amount,
                    "confidence": round(float(f.confidence), 3),
                    "match": f.match,
                    "tx": f.tx_hash,
                    "time": f.timestamp,
                }
                for f in r.flows[:80]
            ],
        }
    )


@tool
async def risk_report(address: str, chain: str | None = None, deep: bool = False) -> str:
    """0-100 risk score with findings and evidence: exposure to sanctioned/mixer/scam addresses,
    pass-through behaviour, rapid movement, fan-in/fan-out, structuring, address poisoning, etc.
    deep=True also checks addresses two hops away (slower)."""
    c, a = _target(address, chain)
    return _dump((await _svc().risk.analyze(c, a, deep)).model_dump())


@tool
async def wallet_links(
    addresses: list[str],
    chain: str | None = None,
    token: str | None = "USDT",
    min_amount: str = "1",
    deep: bool = False,
) -> str:
    """Links between several wallets (2-50, one chain): direct transfers between them
    (e.g. "3,999 USDT went from X to Z", with tx hashes), money that passed through an
    outside intermediary wallet from one to another (with matching in/out amounts),
    outside wallets that funded or received from several of them, and groups of connected
    wallets. token=None checks every token. deep=True also downloads the main intermediaries
    to find three-hop paths (slower). Members are numbered #1.. in input order."""
    c, members = resolve_members(addresses, Chain(chain) if chain else None, _svc().providers.supported_chains)
    params = LinkParams(token=token or None, min_amount=Decimal(min_amount), deep=deep)
    report = await _svc().links.analyzer.analyze(c, members, params)
    data = report.model_dump()
    for d in data["direct"]:
        d["transfers"] = d["transfers"][:10]
    data["paths"] = [p for p in data["paths"] if not p["through_service"]][:40] + [
        {k: p[k] for k in ("from_address", "to_address", "via", "amount_out", "through_service")}
        for p in data["paths"]
        if p["through_service"]
    ][:10]
    for p in data["paths"]:
        if "matched" in p:
            p["matched"] = p["matched"][:5]
    return _dump(data)


@tool
async def money_value(amount: str, token: str, chain: str = "tron", contract: str | None = None, time: str | None = None) -> str:
    """Dollar and toman value of a token amount, at a given time (ISO date/time of the transfer)
    and today. Toman uses the USDT/IRT market rate (Nobitex/Wallex) or the rate set by hand.
    For tokens other than the native coin, pass the contract (fake look-alike tokens have no value)."""
    when = datetime.fromisoformat(time.replace("Z", "+00:00")) if time else None
    if when is not None and when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    s = _svc()
    [v] = await s.values.values([(Chain(chain), token, contract, Decimal(amount), when)])
    rate = await s.values.toman_now()
    return _dump({**v.model_dump(), "toman_per_usd_today": rate.rate, "rate_source": rate.source})


@tool
async def label_address(address: str, name: str, category: str, chain: str | None = None, note: str = "") -> str:
    """Save a label for an address (categories: exchange, bridge, mixer, defi, token_contract,
    sanctioned, scam, service, personal, other). Exchanges/bridges/mixers stop traces."""
    c, a = _target(address, chain)
    label = await _svc().labels.set(c, a, name, LabelCategory(category), note or None)
    return _dump(label.model_dump())


@tool
async def get_label(address: str, chain: str | None = None) -> str:
    """Known label of an address (built-in lists, OFAC sanctions, or user labels), if any."""
    c, a = _target(address, chain)
    label = _svc().labels.get(c, a)
    return _dump(label.model_dump() if label else None)


def main() -> None:
    mcp.run("stdio")


if __name__ == "__main__":
    main()
