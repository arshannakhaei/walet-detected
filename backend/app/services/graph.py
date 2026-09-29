"""Multi-layer fund-flow graph: who funded a wallet (and who funded them), and where
the money went (and where it went next), expanded breadth-first."""

import asyncio
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.models import Chain, Direction, Transfer
from app.services.labels import TERMINAL_CATEGORIES, Label, LabelService
from app.services.wallet import TransferFilter, WalletService

MAX_TX_HASHES_PER_EDGE = 10


class GraphNode(BaseModel):
    address: str
    # 0 = root; negative = layers of senders (sources); positive = layers of receivers.
    depth: int
    label: Label | None = None
    is_hub: bool = False
    expanded: bool = False
    stop_reason: str | None = Field(
        None, description="Why the node was not expanded: hub, labeled, max_depth, max_nodes"
    )


class GraphEdge(BaseModel):
    from_address: str
    to_address: str
    token_symbol: str
    token_contract: str | None = None
    amount: Decimal
    count: int
    first_seen: datetime
    last_seen: datetime
    tx_hashes: list[str]


class Graph(BaseModel):
    chain: Chain
    root: str
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    truncated: bool = Field(description="True when max_nodes stopped the expansion early.")


@dataclass
class GraphParams:
    depth_in: int = 2
    depth_out: int = 2
    max_nodes: int = 150
    max_children: int = 20  # counterparties kept per node, largest first
    # Only follow money forward in time: an outflow is followed only if it
    # happened after the node first received money from its parent.
    follow_time: bool = True
    filter: TransferFilter | None = None


def _aggregate(transfers: list[Transfer], address: str, direction: Direction) -> list[GraphEdge]:
    edges: dict[tuple[str, str | None, str], GraphEdge] = {}
    for t in transfers:
        if t.direction_for(address) != direction:
            continue
        other = t.counterparty_for(address)
        key = (other, t.token_contract, t.token_symbol)
        edge = edges.get(key)
        if edge is None:
            edges[key] = GraphEdge(
                from_address=t.from_address,
                to_address=t.to_address,
                token_symbol=t.token_symbol,
                token_contract=t.token_contract,
                amount=t.amount,
                count=1,
                first_seen=t.timestamp,
                last_seen=t.timestamp,
                tx_hashes=[t.tx_hash],
            )
            continue
        edge.amount += t.amount
        edge.count += 1
        edge.first_seen = min(edge.first_seen, t.timestamp)
        edge.last_seen = max(edge.last_seen, t.timestamp)
        if len(edge.tx_hashes) < MAX_TX_HASHES_PER_EDGE:
            edge.tx_hashes.append(t.tx_hash)
    return sorted(edges.values(), key=lambda e: e.amount, reverse=True)


class GraphBuilder:
    def __init__(
        self,
        wallets: WalletService,
        labels: LabelService,
        hub_threshold: int,
        concurrency: int = 4,
    ):
        self._wallets = wallets
        self._labels = labels
        self._hub_threshold = hub_threshold
        self._concurrency = concurrency

    async def build(self, chain: Chain, root: str, params: GraphParams) -> Graph:
        flt = params.filter or TransferFilter()
        nodes: dict[str, GraphNode] = {root: GraphNode(address=root, depth=0, label=self._labels.get(chain, root))}
        edges: dict[tuple[str, str, str | None, str], GraphEdge] = {}
        truncated = False
        sem = asyncio.Semaphore(self._concurrency)

        async def load(address: str) -> tuple[list[Transfer], bool]:
            async with sem:
                limit = None if address == root else self._hub_threshold
                return await self._wallets.load_transfers(chain, address, limit=limit)

        for direction, max_depth, sign in (
            (Direction.OUT, params.depth_out, 1),
            (Direction.IN, params.depth_in, -1),
        ):
            # address -> time bound for following money through it (see follow_time)
            frontier: dict[str, datetime | None] = {root: None}
            for layer in range(1, max_depth + 1):
                expandable = {
                    a: bound for a, bound in frontier.items() if self._should_expand(chain, nodes[a], a == root)
                }
                results = await asyncio.gather(*(load(a) for a in expandable))
                next_frontier: dict[str, datetime | None] = {}
                for (address, bound), (transfers, is_truncated) in zip(expandable.items(), results):
                    node = nodes[address]
                    if is_truncated and address != root:
                        node.is_hub = True
                        node.stop_reason = "hub"
                        continue
                    node.expanded = True
                    relevant = [t for t in transfers if flt.matches(t, address)]
                    if params.follow_time and bound is not None:
                        if direction == Direction.OUT:
                            relevant = [t for t in relevant if t.timestamp >= bound]
                        else:
                            relevant = [t for t in relevant if t.timestamp <= bound]
                    for edge in _aggregate(relevant, address, direction)[: params.max_children]:
                        other = edge.to_address if direction == Direction.OUT else edge.from_address
                        if other not in nodes:
                            if len(nodes) >= params.max_nodes:
                                truncated = True
                                node.stop_reason = "max_nodes"
                                continue
                            nodes[other] = GraphNode(
                                address=other, depth=sign * layer, label=self._labels.get(chain, other)
                            )
                            if layer == max_depth:
                                nodes[other].stop_reason = "max_depth"
                            else:
                                next_frontier[other] = (
                                    edge.first_seen if direction == Direction.OUT else edge.last_seen
                                )
                        key = (edge.from_address, edge.to_address, edge.token_contract, edge.token_symbol)
                        edges.setdefault(key, edge)
                frontier = next_frontier
                if not frontier:
                    break

        return Graph(
            chain=chain,
            root=root,
            nodes=list(nodes.values()),
            edges=list(edges.values()),
            truncated=truncated,
        )

    def _should_expand(self, chain: Chain, node: GraphNode, is_root: bool) -> bool:
        if is_root:
            return True
        label = self._labels.get(chain, node.address)
        if label is not None and label.category in TERMINAL_CATEGORIES:
            node.stop_reason = "labeled"
            return False
        return not node.is_hub
