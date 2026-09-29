"""Graph and tracer tests on a small in-memory chain.

Scenario (USDT):
    OTHER  --200-->  SCAMMER            (t-60, older funds already in the wallet)
    VICTIM --1000--> SCAMMER            (t0,   the transfer we trace)
    SCAMMER --600--> M1                 (t+10)
    SCAMMER --400--> M2                 (t+20)
    M1 --600--> EXCHANGE                (t+30)
    M2 --400--> M3                      (t+40)
    M3 --400--> EXCHANGE                (t+50)
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.db import Database
from app.models import Chain, Transfer
from app.providers import TransferPage
from app.services.graph import GraphBuilder, GraphParams
from app.services.labels import Label, LabelCategory, LabelService
from app.services.tracer import (
    EndReason,
    LotMethod,
    TraceDirection,
    Tracer,
    TraceParams,
    allocate,
)
from app.services.wallet import WalletService
from tests.conftest import USDT, make_address

VICTIM, SCAMMER, M1, M2, M3, EXCHANGE, OTHER, HUB = (make_address(n) for n in range(10, 18))
T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def usdt(tx: str, frm: str, to: str, amount: int, minutes: int) -> Transfer:
    return Transfer(
        chain=Chain.TRON,
        transfer_id=f"{tx}:id",
        tx_hash=tx,
        timestamp=T0 + timedelta(minutes=minutes),
        from_address=frm,
        to_address=to,
        amount=Decimal(amount),
        token_symbol="USDT",
        token_contract=USDT,
        token_decimals=6,
    )


SCENARIO = [
    usdt("old", OTHER, SCAMMER, 200, -60),
    usdt("s1", VICTIM, SCAMMER, 1000, 0),
    usdt("a", SCAMMER, M1, 600, 10),
    usdt("b", SCAMMER, M2, 400, 20),
    usdt("c", M1, EXCHANGE, 600, 30),
    usdt("d", M2, M3, 400, 40),
    usdt("e", M3, EXCHANGE, 400, 50),
]


class FakeProvider:
    history_cap = None
    reports_token_balances = True

    def __init__(self, transfers: list[Transfer], hubs: set[str] = frozenset()):
        self.transfers = transfers
        self.hubs = hubs
        self.calls: list[str] = []

    async def get_transfers(self, address: str, max_items: int) -> TransferPage:
        self.calls.append(address)
        mine = [t for t in self.transfers if address in (t.from_address, t.to_address)]
        mine.sort(key=lambda t: t.timestamp, reverse=True)
        return TransferPage(transfers=mine, truncated=address in self.hubs)

    async def get_balances(self, address: str):
        return []


class FakeRegistry:
    def __init__(self, provider: FakeProvider):
        self.provider = provider

    def get(self, chain: Chain):
        return self.provider if chain == Chain.TRON else None

    @property
    def supported_chains(self):
        return [Chain.TRON]


@pytest.fixture
async def env(tmp_path):
    async def make(transfers=SCENARIO, hubs=frozenset()):
        db = Database(f"sqlite+aiosqlite:///{tmp_path / 'trace.db'}")
        await db.init()
        provider = FakeProvider(transfers, set(hubs))
        wallets = WalletService(db, FakeRegistry(provider), max_transfers=2000, cache_ttl=3600)
        builtin = {
            (Chain.TRON, EXCHANGE): Label(
                chain=Chain.TRON, address=EXCHANGE, name="Test Exchange",
                category=LabelCategory.EXCHANGE, source="builtin",
            )
        }
        labels = LabelService(db, builtin=builtin)
        await labels.load()
        made.append(db)
        return wallets, labels, provider

    made: list[Database] = []
    yield make
    for db in made:
        await db.close()


# --- allocate ---------------------------------------------------------------


def _by_pair(allocs):
    return {(a.inflow_id, a.outflow_id): (a.amount, a.exact) for a in allocs}


def test_allocate_fifo_spends_oldest_first():
    pairs = _by_pair(allocate(SCENARIO, SCAMMER, LotMethod.FIFO))
    assert pairs[("old:id", "a:id")] == (200, False)
    assert pairs[("s1:id", "a:id")] == (400, False)
    assert pairs[("s1:id", "b:id")] == (400, False)


def test_allocate_lifo_spends_newest_first():
    pairs = _by_pair(allocate(SCENARIO, SCAMMER, LotMethod.LIFO))
    assert pairs[("s1:id", "a:id")] == (600, False)
    assert pairs[("s1:id", "b:id")] == (400, False)
    assert ("old:id", "a:id") not in pairs


def test_allocate_exact_match_beats_lot_order():
    transfers = [
        usdt("x0", OTHER, M1, 500, 0),
        usdt("x1", VICTIM, M1, 250, 5),
        usdt("x2", M1, M2, 249, 6),  # within 1% of the 250 just received
    ]
    pairs = _by_pair(allocate(transfers, M1, LotMethod.FIFO))
    assert pairs == {("x1:id", "x2:id"): (249, True)}


def test_allocate_unknown_source_when_history_is_short():
    pairs = _by_pair(allocate([usdt("y", M1, M2, 50, 0)], M1))
    assert pairs == {(None, "y:id"): (50, False)}


# --- tracer -----------------------------------------------------------------


async def _trace(env, method, direction=TraceDirection.FORWARD, address=VICTIM, tx="s1", **kw):
    wallets, labels, provider = await env(**kw)
    tracer = Tracer(wallets, labels, hub_threshold=1000)
    start = await tracer.find_start(Chain.TRON, address, tx)
    return await tracer.trace(Chain.TRON, start, TraceParams(direction=direction, method=method)), provider


async def test_trace_forward_fifo(env):
    result, _ = await _trace(env, LotMethod.FIFO)
    assert result.traced_amount == 1000
    assert result.summary[EndReason.LABELED] == 800
    assert result.summary[EndReason.UNSPENT] == 200
    exchange = next(e for e in result.endpoints if e.reason == EndReason.LABELED)
    assert exchange.address == EXCHANGE and exchange.label.name == "Test Exchange"
    # M1 -> EXCHANGE carries 400 of the traced funds out of a 600 transfer.
    flow = next(f for f in result.flows if f.tx_hash == "c")
    assert (flow.traced_amount, flow.transfer_amount, flow.hop) == (400, 600, 2)
    assert flow.match == "exact"
    # Lot step (0.7) then exact forward (0.95).
    assert flow.confidence == Decimal("0.7") * Decimal("0.95")


async def test_trace_forward_lifo(env):
    result, _ = await _trace(env, LotMethod.LIFO)
    assert result.summary == {EndReason.LABELED: Decimal(1000)}


async def test_trace_backward(env):
    result, _ = await _trace(env, LotMethod.FIFO, TraceDirection.BACKWARD, address=M3, tx="e")
    assert {f.tx_hash for f in result.flows} == {"e", "d", "b", "s1"}
    # The victim has no earlier history, so the source is unknown beyond them.
    assert result.summary == {EndReason.UNKNOWN_SOURCE: Decimal(400)}
    assert result.endpoints[0].address == VICTIM


async def test_trace_stops_at_hub(env):
    result, provider = await _trace(env, LotMethod.LIFO, hubs={M2})
    assert result.summary[EndReason.HUB] == 400
    assert M3 not in provider.calls
    assert next(n for n in result.nodes if n.address == M2).is_hub


async def test_trace_partial_amount_and_merge(env):
    # Money splits at SCAMMER and both halves merge again at M3.
    transfers = [
        usdt("s1", VICTIM, SCAMMER, 1000, 0),
        usdt("a", SCAMMER, M1, 500, 10),
        usdt("b", SCAMMER, M2, 500, 11),
        usdt("c", M1, M3, 500, 20),
        usdt("d", M2, M3, 500, 21),
        usdt("e", M3, EXCHANGE, 1000, 30),
    ]
    wallets, labels, _ = await env(transfers=transfers)
    tracer = Tracer(wallets, labels, hub_threshold=1000)
    start = await tracer.find_start(Chain.TRON, VICTIM, "s1")
    result = await tracer.trace(Chain.TRON, start, TraceParams(), amount=Decimal(100))
    assert result.traced_amount == 100
    assert result.summary == {EndReason.LABELED: Decimal(100)}
    merged = next(f for f in result.flows if f.tx_hash == "e")
    assert merged.traced_amount == 100


# --- graph ------------------------------------------------------------------


async def test_graph_layers(env):
    wallets, labels, provider = await env()
    graph = await GraphBuilder(wallets, labels, hub_threshold=1000).build(
        Chain.TRON, SCAMMER, GraphParams(depth_in=1, depth_out=2)
    )
    depth = {n.address: n.depth for n in graph.nodes}
    assert depth == {SCAMMER: 0, VICTIM: -1, OTHER: -1, M1: 1, M2: 1, EXCHANGE: 2, M3: 2}
    nodes = {n.address: n for n in graph.nodes}
    assert nodes[EXCHANGE].label.category == LabelCategory.EXCHANGE
    assert nodes[M3].stop_reason == "max_depth"
    edges = {(e.from_address, e.to_address): e.amount for e in graph.edges}
    assert edges[(SCAMMER, M1)] == 600 and edges[(M1, EXCHANGE)] == 600
    assert (M3, EXCHANGE) not in edges  # beyond depth 2
    assert EXCHANGE not in provider.calls  # depth limit reached, never loaded


async def test_graph_does_not_expand_labeled_exchange(env):
    wallets, labels, provider = await env()
    graph = await GraphBuilder(wallets, labels, hub_threshold=1000).build(
        Chain.TRON, M1, GraphParams(depth_in=0, depth_out=3)
    )
    nodes = {n.address: n for n in graph.nodes}
    assert nodes[EXCHANGE].stop_reason == "labeled"
    assert EXCHANGE not in provider.calls


async def test_graph_follow_time_ignores_earlier_outflows(env):
    transfers = SCENARIO + [usdt("early", M1, OTHER, 5, -30)]  # M1 paid OTHER before being funded
    wallets, labels, _ = await env(transfers=transfers)
    builder = GraphBuilder(wallets, labels, hub_threshold=1000)
    graph = await builder.build(Chain.TRON, SCAMMER, GraphParams(depth_in=0, depth_out=2))
    assert (M1, OTHER) not in {(e.from_address, e.to_address) for e in graph.edges}
    graph = await builder.build(Chain.TRON, SCAMMER, GraphParams(depth_in=0, depth_out=2, follow_time=False))
    assert (M1, OTHER) in {(e.from_address, e.to_address) for e in graph.edges}


async def test_graph_max_nodes(env):
    wallets, labels, _ = await env()
    graph = await GraphBuilder(wallets, labels, hub_threshold=1000).build(
        Chain.TRON, SCAMMER, GraphParams(depth_in=1, depth_out=2, max_nodes=3)
    )
    assert len(graph.nodes) == 3 and graph.truncated


async def test_graph_marks_hubs(env):
    wallets, labels, _ = await env(hubs={M2})
    graph = await GraphBuilder(wallets, labels, hub_threshold=1000).build(
        Chain.TRON, SCAMMER, GraphParams(depth_in=0, depth_out=3)
    )
    nodes = {n.address: n for n in graph.nodes}
    assert nodes[M2].is_hub and nodes[M2].stop_reason == "hub"
    assert M3 not in nodes
