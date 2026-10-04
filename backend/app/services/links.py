"""Links between a set of wallets: did money move from one to another, directly or
through intermediaries, and how much?

Typical question: "here are 16 USDT wallets - which of them are connected, and
did e.g. 3,999 USDT go from wallet X to wallet Z?"

What is found, from the members' own histories (no extra downloads):
  - direct transfers between members (X -> Z);
  - pass-through paths X -> C -> Z through one outside wallet C, with the
    individual transfers paired up when an amount leaves C soon after a
    similar amount arrived (the same money, most likely);
  - shared counterparties: outside wallets that funded, or received money
    from, several members.
With `deep`, the histories of the main intermediaries are downloaded as well,
which finds three-hop paths X -> C -> D -> Z.
"""

import asyncio
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field

from app.models import Chain, Transfer
from app.providers import ProviderError
from app.services.addresses import AddressError, resolve_address
from app.services.labels import TERMINAL_CATEGORIES, Label, LabelCategory, LabelService
from app.services.wallet import TransferFilter, UnsupportedChainError, WalletService

log = logging.getLogger(__name__)

MAX_TRANSFERS_PER_LINK = 50
MAX_MATCHES_PER_PATH = 20
MAX_SHARED = 50
MAX_PATHS = 200
MAX_MEMBERS = 50


def split_addresses(value: list[str] | str) -> list[str]:
    items = value if isinstance(value, list) else re.split(r"[\s,;]+", value)
    return [a.strip() for a in items if a.strip()]


def resolve_members(raw: list[str] | str, chain: Chain | None, supported: list[Chain]) -> tuple[Chain, list[str]]:
    """Validate a list of addresses that must all belong to one chain (detected from the first)."""
    addresses = split_addresses(raw)
    if len(addresses) < 2:
        raise ValueError("give at least two addresses")
    if len(addresses) > MAX_MEMBERS:
        raise ValueError(f"at most {MAX_MEMBERS} addresses at once")
    resolved, bad = [], []
    for a in addresses:
        try:
            c, normalized = resolve_address(a, chain, supported)
        except AddressError:
            bad.append(a)
            continue
        chain = chain or c
        resolved.append(normalized)
    if bad:
        where = f" for {chain.value}" if chain else ""
        raise AddressError(f"invalid address(es){where}: {', '.join(bad)}")
    if chain not in supported:
        raise UnsupportedChainError(f"chain '{chain.value}' is not supported yet")
    return chain, list(dict.fromkeys(resolved))


class LinkTransfer(BaseModel):
    tx_hash: str
    timestamp: datetime
    from_address: str
    to_address: str
    amount: Decimal
    token_symbol: str


def _lt(t: Transfer) -> LinkTransfer:
    return LinkTransfer(
        tx_hash=t.tx_hash,
        timestamp=t.timestamp,
        from_address=t.from_address,
        to_address=t.to_address,
        amount=t.amount,
        token_symbol=t.token_symbol,
    )


class DirectLink(BaseModel):
    from_address: str
    to_address: str
    token_symbol: str
    token_contract: str | None = None
    total: Decimal
    count: int
    first_seen: datetime
    last_seen: datetime
    transfers: list[LinkTransfer] = Field(description="Largest first, at most 50.")


class MatchedHop(BaseModel):
    """One amount that went in to the intermediary and (most likely) straight out again."""

    incoming: LinkTransfer
    outgoing: LinkTransfer
    delay_minutes: float


class PathLink(BaseModel):
    """Money from one member to another through outside wallets (`via`, in order)."""

    from_address: str
    to_address: str
    via: list[str]
    via_labels: list[Label | None]
    token_symbol: str
    token_contract: str | None = None
    amount_in: Decimal = Field(description="Total the first member sent into the path.")
    amount_out: Decimal = Field(description="Total the last intermediary sent to the second member.")
    matched: list[MatchedHop] = Field(
        default_factory=list, description="Transfer pairs whose amount and timing match (two-hop paths)."
    )
    matched_amount: Decimal = Decimal(0)
    # Paths through exchanges and other services are weak evidence: those
    # wallets pool many customers' money.
    through_service: bool = False


class SharedRole(str, Enum):
    COMMON_SOURCE = "common_source"  # sent money to several members
    COMMON_DESTINATION = "common_destination"  # received money from several members


class MemberShare(BaseModel):
    address: str
    amount: Decimal
    count: int


class SharedCounterparty(BaseModel):
    address: str
    label: Label | None = None
    role: SharedRole
    token_symbol: str
    token_contract: str | None = None
    members: list[MemberShare]
    total: Decimal


class MemberSummary(BaseModel):
    address: str
    index: int = Field(description="Position in the input list, from 1.")
    label: Label | None = None
    transfer_count: int = 0
    truncated: bool = False
    error: str | None = None
    sent_to_members: Decimal = Decimal(0)
    received_from_members: Decimal = Decimal(0)
    linked_members: int = 0
    group: int | None = Field(None, description="Members in the same group are connected by money flows.")
    usdt_frozen: bool | None = Field(None, description="USDT frozen by Tether (None = unknown).")
    sanctioned: bool | None = Field(None, description="On a sanctions list (Chainalysis oracle; None = unknown).")


class LinkReport(BaseModel):
    chain: Chain
    token: str | None
    members: list[MemberSummary]
    direct: list[DirectLink]
    paths: list[PathLink]
    shared: list[SharedCounterparty]
    groups: list[list[str]] = Field(description="Connected members (2 or more), largest group first.")
    intermediaries_checked: int = 0
    complete: bool = Field(description="False when a history was cut short or could not be loaded.")


@dataclass
class LinkParams:
    token: str | None = "USDT"  # symbol or contract; None = every token
    min_amount: Decimal = Decimal("1")
    start: datetime | None = None
    end: datetime | None = None
    # Pairing an incoming and outgoing transfer of an intermediary
    match_window_hours: int = 72
    tolerance: Decimal = Decimal("0.03")  # outgoing may be up to 3% smaller (fees) or larger
    deep: bool = False
    max_intermediaries: int = 25


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, a: str) -> str:
        self.parent.setdefault(a, a)
        while self.parent[a] != a:
            self.parent[a] = self.parent[self.parent[a]]
            a = self.parent[a]
        return a

    def union(self, a: str, b: str) -> None:
        self.parent[self.find(a)] = self.find(b)


def _token_key(t: Transfer) -> tuple[str, str | None]:
    return (t.token_symbol, t.token_contract)


def match_hops(
    incoming: list[Transfer], outgoing: list[Transfer], window: timedelta, tolerance: Decimal
) -> list[MatchedHop]:
    """Pair each incoming transfer with the first unused outgoing one of a similar
    amount that left within `window` after it."""
    outgoing = sorted(outgoing, key=lambda t: t.timestamp)
    used: set[str] = set()
    pairs: list[MatchedHop] = []
    for t_in in sorted(incoming, key=lambda t: t.timestamp):
        low, high = t_in.amount * (1 - tolerance), t_in.amount * (1 + tolerance)
        for t_out in outgoing:
            if t_out.transfer_id in used or t_out.timestamp < t_in.timestamp:
                continue
            if t_out.timestamp - t_in.timestamp > window:
                break
            if low <= t_out.amount <= high:
                used.add(t_out.transfer_id)
                pairs.append(
                    MatchedHop(
                        incoming=_lt(t_in),
                        outgoing=_lt(t_out),
                        delay_minutes=round((t_out.timestamp - t_in.timestamp).total_seconds() / 60, 1),
                    )
                )
                break
    return pairs


class LinkAnalyzer:
    def __init__(
        self, wallets: WalletService, labels: LabelService, hub_threshold: int, concurrency: int = 4, sanctions=None
    ):
        self._sanctions = sanctions  # SanctionsChecker, optional
        self._wallets = wallets
        self._labels = labels
        self._hub_threshold = hub_threshold
        self._concurrency = concurrency

    def _is_service(self, chain: Chain, address: str) -> bool:
        label = self._labels.get(chain, address)
        return label is not None and label.category in TERMINAL_CATEGORIES

    async def analyze(
        self,
        chain: Chain,
        addresses: list[str],
        params: LinkParams,
        progress=None,  # callable(done, total, stage)
    ) -> LinkReport:
        members = list(dict.fromkeys(addresses))
        member_set = set(members)
        flt = TransferFilter(token=params.token, min_amount=params.min_amount, start=params.start, end=params.end)
        sem = asyncio.Semaphore(self._concurrency)
        summaries = {
            a: MemberSummary(address=a, index=i + 1, label=self._labels.get(chain, a)) for i, a in enumerate(members)
        }
        done = 0

        async def load_member(address: str) -> list[Transfer]:
            nonlocal done
            summary = summaries[address]
            try:
                async with sem:
                    transfers, truncated = await self._wallets.load_transfers(chain, address)
                summary.transfer_count = len(transfers)
                summary.truncated = truncated
                return [t for t in transfers if flt.matches(t, address)]
            except ProviderError as exc:
                summary.error = str(exc)
                return []
            finally:
                done += 1
                if progress:
                    progress(done, len(members), "members")

        histories = dict(zip(members, await asyncio.gather(*(load_member(a) for a in members))))
        if self._sanctions is not None and self._sanctions.supports(chain):
            for status in await self._sanctions.check_many(chain, members):
                summaries[status.address].usdt_frozen = status.usdt_frozen
                summaries[status.address].sanctioned = status.sanctioned

        # Every relevant transfer once (a transfer between two members shows up in both histories).
        seen: set[str] = set()
        transfers: list[Transfer] = []
        for history in histories.values():
            for t in history:
                if t.transfer_id not in seen and t.from_address != t.to_address:
                    seen.add(t.transfer_id)
                    transfers.append(t)

        direct = self._direct_links(transfers, member_set)
        # outside wallet -> token -> transfers from members to it / from it to members
        into: dict[str, dict[tuple, list[Transfer]]] = {}
        out_of: dict[str, dict[tuple, list[Transfer]]] = {}
        for t in transfers:
            if t.from_address in member_set and t.to_address not in member_set:
                into.setdefault(t.to_address, {}).setdefault(_token_key(t), []).append(t)
            elif t.to_address in member_set and t.from_address not in member_set:
                out_of.setdefault(t.from_address, {}).setdefault(_token_key(t), []).append(t)

        window = timedelta(hours=params.match_window_hours)
        paths = self._two_hop_paths(chain, into, out_of, window, params.tolerance)
        shared = self._shared(chain, into, out_of)

        checked = 0
        if params.deep:
            paths_3, checked = await self._three_hop_paths(chain, into, out_of, member_set, flt, params, progress)
            paths.extend(paths_3)

        paths.sort(key=lambda p: (p.through_service, -len(p.matched), -p.matched_amount, -p.amount_out))
        paths = paths[:MAX_PATHS]

        uf = _UnionFind()
        for link in direct:
            uf.union(link.from_address, link.to_address)
            summaries[link.from_address].sent_to_members += link.total
            summaries[link.to_address].received_from_members += link.total
        for p in paths:
            if not p.through_service:
                uf.union(p.from_address, p.to_address)
        linked: dict[str, set[str]] = {a: set() for a in members}
        for link in direct:
            linked[link.from_address].add(link.to_address)
            linked[link.to_address].add(link.from_address)
        for p in paths:
            if not p.through_service:
                linked[p.from_address].add(p.to_address)
                linked[p.to_address].add(p.from_address)

        groups_by_root: dict[str, list[str]] = {}
        for a in members:
            if a in uf.parent:
                groups_by_root.setdefault(uf.find(a), []).append(a)
        groups = sorted((g for g in groups_by_root.values() if len(g) > 1), key=len, reverse=True)
        for i, group in enumerate(groups, start=1):
            for a in group:
                summaries[a].group = i
        for a in members:
            linked[a].discard(a)
            summaries[a].linked_members = len(linked[a])

        return LinkReport(
            chain=chain,
            token=params.token,
            members=list(summaries.values()),
            direct=direct,
            paths=paths,
            shared=shared,
            groups=groups,
            intermediaries_checked=checked,
            complete=all(not s.truncated and s.error is None for s in summaries.values()),
        )

    @staticmethod
    def _direct_links(transfers: list[Transfer], members: set[str]) -> list[DirectLink]:
        links: dict[tuple, list[Transfer]] = {}
        for t in transfers:
            if t.from_address in members and t.to_address in members:
                links.setdefault((t.from_address, t.to_address, *_token_key(t)), []).append(t)
        out = []
        for (frm, to, symbol, contract), ts in links.items():
            ts.sort(key=lambda t: t.amount, reverse=True)
            out.append(
                DirectLink(
                    from_address=frm,
                    to_address=to,
                    token_symbol=symbol,
                    token_contract=contract,
                    total=sum((t.amount for t in ts), Decimal(0)),
                    count=len(ts),
                    first_seen=min(t.timestamp for t in ts),
                    last_seen=max(t.timestamp for t in ts),
                    transfers=[_lt(t) for t in ts[:MAX_TRANSFERS_PER_LINK]],
                )
            )
        return sorted(out, key=lambda link: link.total, reverse=True)

    def _two_hop_paths(self, chain, into, out_of, window, tolerance) -> list[PathLink]:
        paths = []
        for via in into.keys() & out_of.keys():
            label = self._labels.get(chain, via)
            if label is not None and label.category == LabelCategory.TOKEN_CONTRACT:
                continue
            service = self._is_service(chain, via)
            for token in into[via].keys() & out_of[via].keys():
                ins, outs = into[via][token], out_of[via][token]
                senders = {t.from_address for t in ins}
                receivers = {t.to_address for t in outs}
                for a in senders:
                    for b in receivers:
                        if a == b:
                            continue
                        a_in = [t for t in ins if t.from_address == a]
                        b_out = [t for t in outs if t.to_address == b]
                        # Money must leave the intermediary after it arrived.
                        if max(t.timestamp for t in b_out) < min(t.timestamp for t in a_in):
                            continue
                        matched = [] if service else match_hops(a_in, b_out, window, tolerance)
                        paths.append(
                            PathLink(
                                from_address=a,
                                to_address=b,
                                via=[via],
                                via_labels=[label],
                                token_symbol=token[0],
                                token_contract=token[1],
                                amount_in=sum((t.amount for t in a_in), Decimal(0)),
                                amount_out=sum((t.amount for t in b_out), Decimal(0)),
                                matched=matched[:MAX_MATCHES_PER_PATH],
                                matched_amount=sum((m.outgoing.amount for m in matched), Decimal(0)),
                                through_service=service,
                            )
                        )
        return paths

    def _shared(self, chain, into, out_of) -> list[SharedCounterparty]:
        shared = []
        for role, table, member_of in (
            (SharedRole.COMMON_SOURCE, out_of, lambda t: t.to_address),
            (SharedRole.COMMON_DESTINATION, into, lambda t: t.from_address),
        ):
            for address, by_token in table.items():
                label = self._labels.get(chain, address)
                if label is not None and label.category == LabelCategory.TOKEN_CONTRACT:
                    continue
                for (symbol, contract), ts in by_token.items():
                    per_member: dict[str, MemberShare] = {}
                    for t in ts:
                        m = member_of(t)
                        share = per_member.setdefault(m, MemberShare(address=m, amount=Decimal(0), count=0))
                        share.amount += t.amount
                        share.count += 1
                    if len(per_member) < 2:
                        continue
                    shares = sorted(per_member.values(), key=lambda s: s.amount, reverse=True)
                    shared.append(
                        SharedCounterparty(
                            address=address,
                            label=label,
                            role=role,
                            token_symbol=symbol,
                            token_contract=contract,
                            members=shares,
                            total=sum((s.amount for s in shares), Decimal(0)),
                        )
                    )
        shared.sort(key=lambda s: (len(s.members), s.total), reverse=True)
        return shared[:MAX_SHARED]

    async def _three_hop_paths(self, chain, into, out_of, members, flt, params, progress):
        """X -> C -> D -> Z: download the histories of the wallets members sent most to."""
        candidates = sorted(
            (
                (sum(t.amount for ts in by_token.values() for t in ts), c)
                for c, by_token in into.items()
                if not self._is_service(chain, c)
            ),
            reverse=True,
        )[: params.max_intermediaries]
        sem = asyncio.Semaphore(self._concurrency)
        done = 0

        async def load(c: str) -> list[Transfer]:
            nonlocal done
            try:
                async with sem:
                    ts, truncated = await self._wallets.load_transfers(chain, c, limit=self._hub_threshold)
                return [] if truncated else [t for t in ts if flt.matches(t, c)]  # a hub: too busy to follow
            except ProviderError as exc:
                log.info("links: could not load intermediary %s: %s", c, exc)
                return []
            finally:
                done += 1
                if progress:
                    progress(done, len(candidates), "intermediaries")

        histories = await asyncio.gather(*(load(c) for _, c in candidates))
        paths: list[PathLink] = []
        for (_, c), history in zip(candidates, histories):
            hops: dict[tuple, dict[str, list[Transfer]]] = {}
            for t in history:
                d = t.to_address
                if t.from_address == c and d not in members and d != c and d in out_of:
                    hops.setdefault(_token_key(t), {}).setdefault(d, []).append(t)
            for token, by_d in hops.items():
                ins = into[c].get(token, [])
                for d, c_to_d in by_d.items():
                    if self._is_service(chain, d):
                        continue
                    outs = out_of[d].get(token, [])
                    for a in {t.from_address for t in ins}:
                        for b in {t.to_address for t in outs}:
                            if a == b:
                                continue
                            a_in = [t for t in ins if t.from_address == a]
                            b_out = [t for t in outs if t.to_address == b]
                            if max(t.timestamp for t in b_out) < min(t.timestamp for t in a_in):
                                continue
                            paths.append(
                                PathLink(
                                    from_address=a,
                                    to_address=b,
                                    via=[c, d],
                                    via_labels=[self._labels.get(chain, c), self._labels.get(chain, d)],
                                    token_symbol=token[0],
                                    token_contract=token[1],
                                    amount_in=sum((t.amount for t in a_in), Decimal(0)),
                                    amount_out=sum((t.amount for t in b_out), Decimal(0)),
                                )
                            )
        return paths, len(candidates)


class JobState(str, Enum):
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class LinkJob(BaseModel):
    id: str
    state: JobState
    stage: str = "members"
    done: int = 0
    total: int = 0
    error: str | None = None
    result: LinkReport | None = None


@dataclass
class LinkJobs:
    """Runs analyses in the background so the dashboard can show progress."""

    analyzer: LinkAnalyzer
    keep: int = 20
    _jobs: dict[str, LinkJob] = field(default_factory=dict)
    _tasks: dict[str, asyncio.Task] = field(default_factory=dict)

    def start(self, chain: Chain, addresses: list[str], params: LinkParams) -> LinkJob:
        job = LinkJob(id=uuid.uuid4().hex[:12], state=JobState.RUNNING, total=len(set(addresses)))
        self._jobs[job.id] = job

        def progress(done: int, total: int, stage: str) -> None:
            job.done, job.total, job.stage = done, total, stage

        async def run() -> None:
            try:
                job.result = await self.analyzer.analyze(chain, addresses, params, progress)
                job.state = JobState.DONE
            except Exception as exc:  # reported to the dashboard
                log.exception("link analysis failed")
                job.error = str(exc)
                job.state = JobState.FAILED

        self._tasks[job.id] = asyncio.create_task(run())
        while len(self._jobs) > self.keep:
            old = next(iter(self._jobs))
            self._jobs.pop(old)
            task = self._tasks.pop(old, None)
            if task is not None and not task.done():
                task.cancel()
        return job

    def get(self, job_id: str) -> LinkJob | None:
        return self._jobs.get(job_id)

    async def close(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
