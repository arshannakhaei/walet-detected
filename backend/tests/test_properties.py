"""Invariant tests on randomly generated transaction networks.

Whatever the network looks like:
* allocate() never assigns more than an inflow brought in, and every outflow
  is fully paid for (by inflows or by "unknown source");
* a trace accounts for every unit it follows: the amounts at its endpoints
  add up to the traced amount (forward and backward);
* traced amounts never exceed the transfers that carry them, and
  confidence stays in (0, 1].
"""

import random
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.db import Database
from app.models import Chain, Transfer
from app.services.labels import Label, LabelCategory, LabelService
from app.services.tracer import LotMethod, TraceDirection, Tracer, TraceParams, allocate
from app.services.wallet import WalletService
from tests.conftest import USDT, make_address
from tests.test_tracing import FakeProvider, FakeRegistry

T0 = datetime(2024, 6, 1, tzinfo=timezone.utc)
EPS = Decimal("0.000001")


def random_network(seed: int, wallets: int = 12, transfers: int = 60) -> tuple[list[Transfer], list[str]]:
    rng = random.Random(seed)
    addrs = [make_address(5000 + seed * 100 + i) for i in range(wallets)]
    balance = defaultdict(Decimal)
    out: list[Transfer] = []
    # Seed money from outside, then random spending of what each wallet holds.
    for i, a in enumerate(addrs[:4]):
        amt = Decimal(rng.randint(500, 5000))
        out.append(_t(f"s{seed}-{i}", make_address(9000 + i), a, amt, i))
        balance[a] += amt
    for n in range(transfers):
        rich = [a for a in addrs if balance[a] > 1]
        if not rich:
            break
        frm = rng.choice(rich)
        to = rng.choice([a for a in addrs if a != frm])
        amt = (balance[frm] * Decimal(rng.choice([0.1, 0.3, 0.5, 0.9, 1]))).quantize(Decimal("0.01"))
        if amt <= 0:
            continue
        balance[frm] -= amt
        balance[to] += amt
        out.append(_t(f"r{seed}-{n}", frm, to, amt, 10 + n * 5))
    return out, addrs


def _t(tx, frm, to, amount, minutes) -> Transfer:
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


@pytest.mark.parametrize("seed", range(25))
@pytest.mark.parametrize("method", [LotMethod.FIFO, LotMethod.LIFO])
def test_allocate_conserves_value(seed, method):
    transfers, addrs = random_network(seed)
    for a in addrs:
        mine = [t for t in transfers if a in (t.from_address, t.to_address)]
        allocs = allocate(mine, a, method)
        by_out = defaultdict(Decimal)
        by_in = defaultdict(Decimal)
        for x in allocs:
            assert x.amount > 0
            by_out[x.outflow_id] += x.amount
            if x.inflow_id:
                by_in[x.inflow_id] += x.amount
        for t in mine:
            if t.from_address == a:
                assert abs(by_out[t.transfer_id] - t.amount) < EPS, "outflow not fully paid for"
            if t.to_address == a:
                assert by_in[t.transfer_id] <= t.amount + EPS, "inflow spent more than once"
        # Money cannot be spent before it arrives.
        when = {t.transfer_id: t.timestamp for t in mine}
        for x in allocs:
            if x.inflow_id:
                assert when[x.inflow_id] <= when[x.outflow_id]


class _NoDb:
    async def list_labels(self):
        return []


@pytest.fixture
async def make_tracer(tmp_path):
    dbs = []

    async def make(transfers, hubs=(), exchange=None):
        db = Database(f"sqlite+aiosqlite:///{tmp_path / f'p{len(dbs)}.db'}")
        await db.init()
        dbs.append(db)
        wallets = WalletService(db, FakeRegistry(FakeProvider(transfers, set(hubs))), 2000, 3600)
        builtin = {}
        if exchange:
            builtin[(Chain.TRON, exchange)] = Label(
                chain=Chain.TRON, address=exchange, name="X", category=LabelCategory.EXCHANGE, source="builtin"
            )
        return Tracer(wallets, LabelService(_NoDb(), builtin=builtin), hub_threshold=1000)

    yield make
    for db in dbs:
        await db.close()


def _check(result):
    total = sum((e.amount for e in result.endpoints), Decimal(0))
    assert abs(total - result.traced_amount) < Decimal("0.0001"), (
        f"endpoints sum {total} != traced {result.traced_amount}: {result.summary}"
    )
    for f in result.flows:
        assert f.traced_amount <= f.transfer_amount + EPS
        assert Decimal(0) < f.confidence <= 1
    for e in result.endpoints:
        assert e.amount > 0 and Decimal(0) < e.confidence <= 1


@pytest.mark.parametrize("seed", range(20))
@pytest.mark.parametrize("method", [LotMethod.FIFO, LotMethod.LIFO])
async def test_forward_trace_accounts_for_everything(make_tracer, seed, method):
    transfers, addrs = random_network(seed)
    rng = random.Random(seed)
    tracer = await make_tracer(transfers, hubs=[addrs[-1]] if seed % 3 == 0 else (), exchange=addrs[5] if seed % 2 else None)
    start = rng.choice([t for t in transfers if t.tx_hash.startswith("s")])
    params = TraceParams(method=method, max_hops=rng.choice([2, 4, 8]), min_amount=Decimal(rng.choice([0, 1, 50])))
    _check(await tracer.trace(Chain.TRON, start, params))


@pytest.mark.parametrize("seed", range(20))
async def test_backward_trace_accounts_for_everything(make_tracer, seed):
    transfers, _ = random_network(seed)
    rng = random.Random(seed + 1)
    tracer = await make_tracer(transfers)
    start = rng.choice(transfers[-10:])
    params = TraceParams(direction=TraceDirection.BACKWARD, max_hops=6, min_amount=Decimal(0))
    result = await tracer.trace(Chain.TRON, start, params)
    _check(result)


@pytest.mark.parametrize("seed", range(10))
async def test_partial_amount_trace(make_tracer, seed):
    transfers, _ = random_network(seed)
    tracer = await make_tracer(transfers)
    start = transfers[0]
    part = (start.amount / 3).quantize(Decimal("0.01"))
    result = await tracer.trace(Chain.TRON, start, TraceParams(min_amount=Decimal(0)), amount=part)
    assert result.traced_amount == part
    _check(result)
