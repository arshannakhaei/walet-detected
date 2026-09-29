"""Follow a specific amount of money hop by hop, forward (where did it go?) or
backward (where did it come from?).

Inside one wallet, money is fungible, so which outgoing transfer "carries" a
given incoming transfer is a modelling choice. Two rules are applied, in order:

1. Exact match: an outgoing transfer of (almost) the same amount shortly after
   an incoming one is assumed to forward it. This catches the common
   receive-and-forward pattern and gets high confidence.
2. Lot accounting over everything else: each incoming transfer is a lot, each
   outgoing transfer spends lots either oldest-first (FIFO) or newest-first
   (LIFO). Lower confidence, since the wallet's funds are mixed.

Confidence multiplies along the path, so long paths through mixed wallets
show up as uncertain.
"""

from collections import deque
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field

from app.models import Chain, Transfer
from app.services.labels import TERMINAL_CATEGORIES, Label, LabelService
from app.services.wallet import WalletService

EXACT_CONFIDENCE = Decimal("0.95")
LOT_CONFIDENCE = Decimal("0.7")


class TraceDirection(str, Enum):
    FORWARD = "forward"
    BACKWARD = "backward"


class LotMethod(str, Enum):
    FIFO = "fifo"
    LIFO = "lifo"


class EndReason(str, Enum):
    UNSPENT = "unspent"  # (forward) still sitting in the wallet
    UNKNOWN_SOURCE = "unknown_source"  # (backward) funded before the known history
    LABELED = "labeled"  # reached an exchange / bridge / mixer / known service
    HUB = "hub"  # very busy address, funds are pooled
    MAX_HOPS = "max_hops"
    BELOW_MIN = "below_min"  # split into pieces smaller than min_amount
    PRUNED = "pruned"  # dropped by max_branches
    STEP_LIMIT = "step_limit"
    LOOP = "loop"  # came back to a transfer already traced


@dataclass
class Allocation:
    inflow_id: str | None  # None = funds from before the known history
    outflow_id: str
    amount: Decimal
    exact: bool


def allocate(
    transfers: list[Transfer],
    address: str,
    method: LotMethod = LotMethod.FIFO,
    tolerance: Decimal = Decimal("0.01"),
    exact_window: timedelta = timedelta(days=3),
) -> list[Allocation]:
    """Decide which incoming transfers paid for each outgoing transfer of one token."""
    ordered = sorted(
        (
            t
            for t in transfers
            if t.success and t.from_address != t.to_address and address in (t.from_address, t.to_address)
        ),
        key=lambda t: (t.timestamp, t.to_address != address),  # inflows first on ties
    )
    inflows = [t for t in ordered if t.to_address == address]
    outflows = [t for t in ordered if t.from_address == address]

    allocations: list[Allocation] = []
    in_left = {t.transfer_id: t.amount for t in inflows}
    out_left = {t.transfer_id: t.amount for t in outflows}

    # 1. Exact receive-and-forward matches.
    matched_out: set[str] = set()
    for inflow in inflows:
        for outflow in outflows:
            if outflow.transfer_id in matched_out or outflow.timestamp < inflow.timestamp:
                continue
            if outflow.timestamp - inflow.timestamp > exact_window:
                break
            if abs(outflow.amount - inflow.amount) <= inflow.amount * tolerance:
                amount = min(inflow.amount, outflow.amount)
                allocations.append(Allocation(inflow.transfer_id, outflow.transfer_id, amount, exact=True))
                in_left[inflow.transfer_id] -= amount
                out_left[outflow.transfer_id] -= amount
                matched_out.add(outflow.transfer_id)
                break

    # 2. Lot accounting for whatever is left.
    pool: deque[list] = deque()  # [inflow_id, remaining]
    for t in ordered:
        if t.to_address == address:
            if in_left[t.transfer_id] > 0:
                pool.append([t.transfer_id, in_left[t.transfer_id]])
            continue
        need = out_left[t.transfer_id]
        while need > 0 and pool:
            lot = pool[0] if method == LotMethod.FIFO else pool[-1]
            take = min(need, lot[1])
            allocations.append(Allocation(lot[0], t.transfer_id, take, exact=False))
            lot[1] -= take
            need -= take
            if lot[1] <= 0:
                pool.popleft() if method == LotMethod.FIFO else pool.pop()
        if need > 0:
            allocations.append(Allocation(None, t.transfer_id, need, exact=False))
    return allocations


class TraceFlow(BaseModel):
    hop: int
    transfer_id: str
    tx_hash: str
    from_address: str
    to_address: str
    timestamp: str
    transfer_amount: Decimal
    traced_amount: Decimal = Field(description="Part of this transfer attributed to the traced funds.")
    confidence: Decimal
    match: str  # start, exact, fifo, lifo


class TraceEndpoint(BaseModel):
    address: str
    amount: Decimal
    reason: EndReason
    confidence: Decimal
    label: Label | None = None


class TraceNode(BaseModel):
    address: str
    label: Label | None = None
    is_hub: bool = False


class TraceResult(BaseModel):
    chain: Chain
    direction: TraceDirection
    method: LotMethod
    token_symbol: str
    token_contract: str | None
    start: Transfer
    traced_amount: Decimal
    nodes: list[TraceNode]
    flows: list[TraceFlow]
    endpoints: list[TraceEndpoint]
    summary: dict[EndReason, Decimal]


class TraceStartNotFound(Exception):
    pass


@dataclass
class TraceParams:
    direction: TraceDirection = TraceDirection.FORWARD
    method: LotMethod = LotMethod.FIFO
    max_hops: int = 6
    min_amount: Decimal = Decimal("1")
    max_branches: int = 10
    tolerance: Decimal = Decimal("0.01")
    exact_window_hours: int = 72
    max_steps: int = 200  # address histories loaded, to bound API usage


@dataclass
class _Step:
    address: str  # wallet being examined
    transfer: Transfer  # traced transfer entering (forward) or leaving (backward) it
    amount: Decimal  # traced part of that transfer
    confidence: Decimal
    hop: int


def _same_token(t: Transfer, ref: Transfer) -> bool:
    return t.token_contract == ref.token_contract and t.token_symbol == ref.token_symbol


class Tracer:
    def __init__(self, wallets: WalletService, labels: LabelService, hub_threshold: int):
        self._wallets = wallets
        self._labels = labels
        self._hub_threshold = hub_threshold

    async def find_start(
        self, chain: Chain, address: str, tx_hash: str, token: str | None = None
    ) -> Transfer:
        transfers, _ = await self._wallets.load_transfers(chain, address)
        candidates = [
            t
            for t in transfers
            if t.tx_hash == tx_hash
            and (token is None or token.upper() == t.token_symbol.upper() or token == t.token_contract)
        ]
        if not candidates:
            raise TraceStartNotFound(f"no transfer of {address} found in transaction {tx_hash}")
        return max(candidates, key=lambda t: t.amount)

    async def trace(
        self, chain: Chain, start: Transfer, params: TraceParams, amount: Decimal | None = None
    ) -> TraceResult:
        forward = params.direction == TraceDirection.FORWARD
        traced = min(amount, start.amount) if amount else start.amount
        match_name = params.method.value

        nodes: dict[str, TraceNode] = {}
        flows: dict[str, TraceFlow] = {}
        endpoints: dict[tuple[str, EndReason], TraceEndpoint] = {}

        def node(address: str, is_hub: bool = False) -> TraceNode:
            n = nodes.get(address)
            if n is None:
                n = nodes[address] = TraceNode(address=address, label=self._labels.get(chain, address))
            n.is_hub = n.is_hub or is_hub
            return n

        def end(address: str, amt: Decimal, reason: EndReason, confidence: Decimal) -> None:
            if amt <= 0:
                return
            key = (address, reason)
            ep = endpoints.get(key)
            if ep is None:
                endpoints[key] = TraceEndpoint(
                    address=address,
                    amount=amt,
                    reason=reason,
                    confidence=confidence,
                    label=self._labels.get(chain, address),
                )
            else:
                # Weighted average keeps the confidence meaningful after merging.
                total = ep.amount + amt
                ep.confidence = (ep.confidence * ep.amount + confidence * amt) / total
                ep.amount = total

        def add_flow(t: Transfer, hop: int, amt: Decimal, confidence: Decimal, match: str) -> None:
            node(t.from_address)
            node(t.to_address)
            flow = flows.get(t.transfer_id)
            if flow is not None:  # several traced pieces merged into one transfer
                total = flow.traced_amount + amt
                flow.confidence = (flow.confidence * flow.traced_amount + confidence * amt) / total
                flow.traced_amount = total
                return
            flows[t.transfer_id] = TraceFlow(
                hop=hop,
                transfer_id=t.transfer_id,
                tx_hash=t.tx_hash,
                from_address=t.from_address,
                to_address=t.to_address,
                timestamp=t.timestamp.isoformat(),
                transfer_amount=t.amount,
                traced_amount=amt,
                confidence=confidence,
                match=match,
            )

        add_flow(start, 0, traced, Decimal(1), "start")
        queue = deque([_Step(start.to_address if forward else start.from_address, start, traced, Decimal(1), 0)])
        pending: dict[str, _Step] = {}  # queued, not yet examined, by transfer id
        done: set[str] = {start.transfer_id}
        steps = 0

        while queue:
            step = queue.popleft()
            pending.pop(step.transfer.transfer_id, None)
            done.add(step.transfer.transfer_id)
            here = step.address
            label = self._labels.get(chain, here)
            if label is not None and label.category in TERMINAL_CATEGORIES:
                end(here, step.amount, EndReason.LABELED, step.confidence)
                continue
            if step.hop >= params.max_hops:
                end(here, step.amount, EndReason.MAX_HOPS, step.confidence)
                continue
            if steps >= params.max_steps:
                end(here, step.amount, EndReason.STEP_LIMIT, step.confidence)
                continue
            steps += 1

            history, truncated = await self._wallets.load_transfers(chain, here, limit=self._hub_threshold)
            if truncated:
                node(here, is_hub=True)
                end(here, step.amount, EndReason.HUB, step.confidence)
                continue

            same_token = [t for t in history if _same_token(t, step.transfer)]
            by_id = {t.transfer_id: t for t in same_token}
            allocations = allocate(
                same_token,
                here,
                params.method,
                params.tolerance,
                timedelta(hours=params.exact_window_hours),
            )
            ratio = step.amount / step.transfer.amount if step.transfer.amount else Decimal(0)

            # Pieces of the traced amount that continue to the next wallet.
            pieces: dict[str | None, tuple[Decimal, bool]] = {}
            for a in allocations:
                mine = a.inflow_id if forward else a.outflow_id
                other = a.outflow_id if forward else a.inflow_id
                if mine != step.transfer.transfer_id:
                    continue
                prev_amt, prev_exact = pieces.get(other, (Decimal(0), True))
                pieces[other] = (prev_amt + a.amount * ratio, prev_exact and a.exact)

            if not forward and None in pieces:
                end(here, pieces.pop(None)[0], EndReason.UNKNOWN_SOURCE, step.confidence)
            accounted = sum((amt for amt, _ in pieces.values()), Decimal(0))
            if forward:
                end(here, step.amount - accounted, EndReason.UNSPENT, step.confidence)

            ranked = sorted(pieces.items(), key=lambda kv: kv[1][0], reverse=True)
            for i, (transfer_id, (amt, exact)) in enumerate(ranked):
                nxt = by_id[transfer_id]
                confidence = step.confidence * (EXACT_CONFIDENCE if exact else LOT_CONFIDENCE)
                if amt < params.min_amount:
                    end(here, amt, EndReason.BELOW_MIN, confidence)
                    continue
                if i >= params.max_branches:
                    end(here, amt, EndReason.PRUNED, confidence)
                    continue
                if transfer_id in done:
                    end(here, amt, EndReason.LOOP, confidence)
                    continue
                add_flow(nxt, step.hop + 1, amt, confidence, "exact" if exact else match_name)
                queued = pending.get(transfer_id)
                if queued is not None:  # paths merge: carry both pieces forward together
                    total = queued.amount + amt
                    queued.confidence = (queued.confidence * queued.amount + confidence * amt) / total
                    queued.amount = total
                    continue
                next_address = nxt.to_address if forward else nxt.from_address
                pending[transfer_id] = _Step(next_address, nxt, amt, confidence, step.hop + 1)
                queue.append(pending[transfer_id])

        summary: dict[EndReason, Decimal] = {}
        for ep in endpoints.values():
            summary[ep.reason] = summary.get(ep.reason, Decimal(0)) + ep.amount

        return TraceResult(
            chain=chain,
            direction=params.direction,
            method=params.method,
            token_symbol=start.token_symbol,
            token_contract=start.token_contract,
            start=start,
            traced_amount=traced,
            nodes=list(nodes.values()),
            flows=sorted(flows.values(), key=lambda f: (f.hop, f.timestamp)),
            endpoints=sorted(endpoints.values(), key=lambda e: e.amount, reverse=True),
            summary=summary,
        )
