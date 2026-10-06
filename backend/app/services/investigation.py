"""Investigation report data: a few key ("focus") wallets inside a longer list.

Typical case: the owners of three wallets confessed; the investigator has a
list of sixteen wallets and wants to see how the three are tied to each other
and to the rest, when they were active, and which other wallets stand out.

Collected here, from the chain (nothing is estimated):
  - the full history of each focus wallet, with totals, counterparties, largest
    transfers, monthly series, risk findings and Tether's freeze status;
  - every transfer between the focus wallets and between any two list members;
  - paths through intermediaries and shared counterparties (LinkAnalyzer);
  - a member-to-member matrix and a ranking by money moved inside the list;
  - a comparison of the focus wallets' numbers with TronScan (verification).

Rendering (graphs, HTML, CSV) lives in investigation_graphs / investigation_report.
"""

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal

from pydantic import BaseModel

from app.models import Chain, Transfer
from app.providers import ProviderError, ProviderRegistry
from app.services.fx import TomanRate, ValueService
from app.services.labels import TERMINAL_CATEGORIES, Label, LabelCategory, LabelService
from app.services.links import LinkAnalyzer, LinkParams, LinkReport
from app.services.risk import Finding, WalletStats, _level, analyze_transfers, timeline, wallet_stats
from app.services.tokens import is_spam, known_symbol
from app.services.tronscan import AccountInfo, TronScanClient, Verification, compare, tag_category
from app.services.wallet import WalletService

log = logging.getLogger(__name__)

FOCUS_LIMIT = 20_000  # transfers downloaded per focus wallet (the full history, normally)
MEMBER_LIMIT = 5_000  # per other list member
TOP_PARTIES = 15
LARGEST = 20
MOST_CONNECTED = 3
EXIT_SHARE = Decimal("0.95")  # counterparties covering this share of a focus wallet's flow are its entry/exit points
MAX_EXITS_PER_WALLET = 8
SAMPLE_TRANSFERS = 200  # outgoing transfers of an exit wallet looked at to see where the money goes next
MAX_TAG_LOOKUPS = 150  # addresses whose TronScan name tag is looked up


class TransferRec(BaseModel):
    """One transfer, with the list numbers of its two sides and its value when it happened."""

    tx_hash: str
    timestamp: datetime
    from_address: str
    to_address: str
    from_index: int | None = None  # position in the list (from 1), None for outside wallets
    to_index: int | None = None
    amount: Decimal
    token_symbol: str
    token_contract: str | None = None
    usd_then: Decimal | None = None
    toman_then: Decimal | None = None


class PartyStat(BaseModel):
    """A counterparty of one wallet, for one token and direction."""

    address: str
    index: int | None = None
    label: str | None = None
    category: str | None = None
    amount: Decimal
    count: int
    first_seen: datetime
    last_seen: datetime


class TokenTotals(BaseModel):
    token_symbol: str
    token_contract: str | None = None
    total_in: Decimal = Decimal(0)
    total_out: Decimal = Decimal(0)
    count_in: int = 0
    count_out: int = 0
    usd_in_then: Decimal | None = None  # valued on the day of each transfer
    usd_out_then: Decimal | None = None
    toman_in_then: Decimal | None = None
    toman_out_then: Decimal | None = None
    usd_in_now: Decimal | None = None  # at today's price
    usd_out_now: Decimal | None = None


class BalanceRec(BaseModel):
    token_symbol: str
    token_contract: str | None = None
    amount: Decimal
    usd: Decimal | None = None
    toman: Decimal | None = None


class MonthRow(BaseModel):
    period: str  # YYYY-MM
    amount_in: Decimal
    amount_out: Decimal
    count_in: int
    count_out: int
    balance: Decimal  # running in - out at the end of the month


class FocusProfile(BaseModel):
    index: int
    address: str
    label: str | None = None
    first_seen: datetime | None
    last_seen: datetime | None
    transfer_count: int  # successful, non-spam transfers
    spam_count: int
    truncated: bool
    counterparty_count: int
    balances: list[BalanceRec]
    totals: list[TokenTotals]
    risk_score: int
    risk_level: str
    findings: list[Finding]
    stats: WalletStats
    usdt_frozen: bool | None
    top_senders: list[PartyStat]
    top_receivers: list[PartyStat]
    largest: list[TransferRec]
    monthly: dict[str, list[MonthRow]]  # token symbol -> months, oldest first
    verification: Verification | None = None
    # A smart-contract account (e.g. a deposit address a service created for a customer).
    is_contract: bool | None = None


class MemberRow(BaseModel):
    index: int
    address: str
    label: str | None = None
    is_focus: bool = False
    likely_service: bool = False
    transfer_count: int = 0
    truncated: bool = False
    error: str | None = None
    counterparty_count: int = 0
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    usdt_frozen: bool | None = None
    group: int | None = None
    sent_to_members: Decimal = Decimal(0)
    received_from_members: Decimal = Decimal(0)
    transfers_with_members: int = 0
    partners: int = 0  # other members it exchanged the token with directly

    @property
    def total_with_members(self) -> Decimal:
        return self.sent_to_members + self.received_from_members


class PartnerFlow(BaseModel):
    index: int
    address: str
    sent: Decimal  # this member -> partner
    received: Decimal
    count: int
    first_seen: datetime
    last_seen: datetime


class MemberProfile(BaseModel):
    """A non-focus member that moved notably much money inside the list."""

    index: int
    address: str
    likely_service: bool
    counterparty_count: int
    first_seen: datetime | None
    last_seen: datetime | None
    truncated: bool
    sent_to_members: Decimal
    received_from_members: Decimal
    token_in: Decimal  # all of the token it ever received (within the downloaded history)
    token_out: Decimal
    balance: Decimal | None
    risk_score: int
    risk_level: str
    findings: list[Finding]
    partners: list[PartnerFlow]


class ExitFlow(BaseModel):
    """What one focus wallet moved to (or received from) an entry/exit wallet."""

    index: int  # the focus wallet's list number
    total: Decimal
    count: int
    first_seen: datetime
    last_seen: datetime
    share: Decimal  # of the focus wallet's total outflow (or inflow), 0-100
    largest: list[TransferRec]  # at most 3


class Downstream(BaseModel):
    """Where an exit wallet sent money next (from a sample of its newest outgoing transfers)."""

    address: str
    label: str | None = None
    amount: Decimal
    count: int
    last_seen: datetime
    tx_hash: str  # of the largest transfer


class Inference(BaseModel):
    owner: str  # e.g. "Nobitex"
    basis: str  # short English explanation of how the owner was inferred
    evidence_tx: str | None = None  # a transaction that shows it
    evidence_date: datetime | None = None


class ExitPoint(BaseModel):
    """A wallet the focus wallets' money went to (role "exit") or came from (role "entry")."""

    role: str
    address: str
    index: int | None = None  # list number when it is a member
    label: str | None = None  # tag or label name
    category: str | None = None
    label_source: str | None = None  # builtin / user / tronscan
    inference: Inference | None = None  # owner worked out from on-chain evidence when there is no tag
    is_contract: bool | None = None
    transactions: int | None = None  # TronScan's total transaction count (size of the wallet)
    flows: list[ExitFlow]
    total: Decimal
    downstream: list[Downstream] = []
    downstream_sampled: int = 0  # outgoing transfers looked at
    first_seen: datetime | None = None
    last_seen: datetime | None = None


class Investigation(BaseModel):
    generated_at: datetime
    chain: Chain
    token: str
    token_contract: str | None
    focus: list[FocusProfile]
    members: list[MemberRow]
    focus_transfers: list[TransferRec]  # between two focus wallets, oldest first, every token
    member_transfers: list[TransferRec]  # between any two list members, oldest first
    focus_history: dict[str, list[TransferRec]]  # address -> its full clean history, oldest first
    matrix: list[list[Decimal]]  # [sender][receiver] of the token, in list order
    matrix_counts: list[list[int]]
    ranking: list[MemberRow]  # by total moved with other members, largest first
    most_connected: list[MemberProfile]
    links_focus: LinkReport  # the focus wallets alone, with three-hop paths
    links_all: LinkReport
    exits: list[ExitPoint] = []  # where the focus wallets' money went, largest first
    entries: list[ExitPoint] = []  # where it came from
    contract_creators: dict[str, str] = {}  # focus wallet -> address that deployed it (contract accounts)
    inferred: dict[str, Inference] = {}  # address -> owner worked out from evidence
    labels: dict[str, str]  # address -> name, for every labelled address mentioned
    categories: dict[str, str]
    label_sources: dict[str, str] = {}  # "builtin", "user" or "tronscan"
    toman: TomanRate
    warnings: list[str] = []


def short(address: str, index: int | None = None) -> str:
    """"#4 TCrnJ...FLDi" - plain ASCII, safe inside images."""
    text = f"{address[:5]}...{address[-4:]}" if len(address) > 12 else address
    return f"#{index} {text}" if index else text


class _Snapshot:
    """The members' histories as downloaded once, so every analysis sees the same data.

    Stands in for WalletService inside LinkAnalyzer; other addresses
    (intermediaries) are loaded from the real service.
    """

    def __init__(self, wallets: WalletService, histories: dict[str, tuple[list[Transfer], bool]]):
        self._wallets = wallets
        self._histories = histories

    async def load_transfers(self, chain: Chain, address: str, **kwargs) -> tuple[list[Transfer], bool]:
        if address in self._histories:
            return self._histories[address]
        return await self._wallets.load_transfers(chain, address, **kwargs)


class _TaggedLabels:
    """The label service plus name tags looked up on TronScan for this report."""

    def __init__(self, base: LabelService, chain: Chain, tags: dict[str, str]):
        self._base = base
        self._tags = {
            a: Label(chain=chain, address=a, name=tag, category=LabelCategory(tag_category(tag)), source="tronscan")
            for a, tag in tags.items()
        }

    def get(self, chain: Chain, address: str) -> Label | None:
        return self._base.get(chain, address) or self._tags.get(address)


def _clean(transfers: list[Transfer]) -> list[Transfer]:
    return [t for t in transfers if t.success and not is_spam(t) and t.from_address != t.to_address]


def monthly_series(address: str, transfers: list[Transfer], token: str) -> list[MonthRow]:
    """In/out per calendar month, every month from the first to the last, with a running balance."""
    rows = {r["period"]: r for r in timeline(address, transfers, "month", token)}
    if not rows:
        return []
    first, last = min(rows), max(rows)
    year, month = int(first[:4]), int(first[5:7])
    out: list[MonthRow] = []
    balance = Decimal(0)
    while f"{year:04d}-{month:02d}" <= last:
        period = f"{year:04d}-{month:02d}"
        r = rows.get(period)
        amount_in = r["amount_in"] if r else Decimal(0)
        amount_out = r["amount_out"] if r else Decimal(0)
        balance += amount_in - amount_out
        out.append(
            MonthRow(
                period=period,
                amount_in=amount_in,
                amount_out=amount_out,
                count_in=r["count_in"] if r else 0,
                count_out=r["count_out"] if r else 0,
                balance=balance,
            )
        )
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return out


def member_matrix(
    members: list[str], transfers: list[Transfer], contract: str | None, symbol: str
) -> tuple[list[list[Decimal]], list[list[int]]]:
    """Amount and number of transfers of one token from each member (row) to each member (column)."""
    pos = {a: i for i, a in enumerate(members)}
    n = len(members)
    amounts = [[Decimal(0)] * n for _ in range(n)]
    counts = [[0] * n for _ in range(n)]
    for t in transfers:
        if t.from_address in pos and t.to_address in pos and _is_token(t, contract, symbol):
            amounts[pos[t.from_address]][pos[t.to_address]] += t.amount
            counts[pos[t.from_address]][pos[t.to_address]] += 1
    return amounts, counts


def _is_token(t: Transfer, contract: str | None, symbol: str) -> bool:
    if contract is not None:
        return t.token_contract == contract
    return t.token_contract is None and t.token_symbol.upper() == symbol.upper()


class Investigator:
    def __init__(
        self,
        wallets: WalletService,
        providers: ProviderRegistry,
        labels: LabelService,
        values: ValueService,
        sanctions=None,  # SanctionsChecker
        tronscan: TronScanClient | None = None,
        hub_threshold: int = 500,
    ):
        self._wallets = wallets
        self._providers = providers
        self._labels = labels
        self._values = values
        self._sanctions = sanctions
        self._tronscan = tronscan
        self._hub_threshold = hub_threshold

    async def run(
        self,
        chain: Chain,
        focus: list[str],
        members: list[str],
        token: str = "USDT",
        focus_limit: int = FOCUS_LIMIT,
        member_limit: int = MEMBER_LIMIT,
        progress=None,  # callable(stage, done, total)
    ) -> Investigation:
        members = list(dict.fromkeys(members))
        for a in focus:
            if a not in members:
                members.append(a)
        focus = [a for a in members if a in set(focus)]  # list order
        index = {a: i + 1 for i, a in enumerate(members)}
        warnings: list[str] = []

        def step(stage: str, done: int, total: int) -> None:
            if progress:
                progress(stage, done, total)

        # --- histories (once; everything below works on this snapshot) ----------
        histories: dict[str, tuple[list[Transfer], bool]] = {}
        errors: dict[str, str] = {}
        done = 0
        sem = asyncio.Semaphore(3)

        async def load(address: str) -> None:
            nonlocal done
            limit = focus_limit if address in focus else member_limit
            try:
                async with sem:
                    histories[address] = await self._wallets.load_transfers(chain, address, limit=limit)
            except ProviderError as exc:
                histories[address] = ([], True)
                errors[address] = str(exc)
            done += 1
            step("histories", done, len(members))

        step("histories", 0, len(members))
        await asyncio.gather(*(load(a) for a in members))
        for a in focus:
            if a in errors:
                raise ProviderError(f"could not load focus wallet {a}: {errors[a]}")
            if histories[a][1]:
                warnings.append(f"focus wallet #{index[a]} has more than {focus_limit} transfers; its history is cut short")

        contract = self._token_contract(chain, token, [t for ts, _ in histories.values() for t in ts])
        clean = {a: _clean(ts) for a, (ts, _) in histories.items()}

        # --- name tags (before the link analysis, so exchanges are treated as such) -------
        infos: dict[str, AccountInfo] = {}
        if self._tronscan is not None and chain == Chain.TRON:
            lookups = self._tag_candidates(chain, focus, members, clean, contract, token)
            step("labels", 0, len(lookups))
            for n, a in enumerate(lookups, start=1):
                info = await self._tronscan.account(a)
                if info is not None:
                    infos[a] = info
                step("labels", n, len(lookups))
        labels = _TaggedLabels(self._labels, chain, {a: i.tag for a, i in infos.items() if i.tag})

        # --- link analyses --------------------------------------------------------
        snapshot = _Snapshot(self._wallets, histories)
        analyzer = LinkAnalyzer(snapshot, labels, self._hub_threshold, sanctions=self._sanctions)
        step("links", 0, 2)
        links_all = await analyzer.analyze(chain, members, LinkParams(token=token))
        step("links", 1, 2)
        if len(focus) >= 2:
            links_focus = await analyzer.analyze(chain, focus, LinkParams(token=token, deep=True))
        else:
            links_focus = LinkReport(chain=chain, token=token, members=[], direct=[], paths=[], shared=[], groups=[], complete=True)
        step("links", 2, 2)

        # --- transfers inside the list ---------------------------------------------
        member_set = set(members)
        seen: set[str] = set()
        inside: list[Transfer] = []
        for a in members:
            for t in clean[a]:
                if t.from_address in member_set and t.to_address in member_set and t.transfer_id not in seen:
                    seen.add(t.transfer_id)
                    inside.append(t)
        inside.sort(key=lambda t: t.timestamp)
        matrix, counts = member_matrix(members, inside, contract, token)

        focus_set = set(focus)
        between_focus = [t for t in inside if t.from_address in focus_set and t.to_address in focus_set]

        # --- members table and ranking ------------------------------------------------
        by_address = {m.address: m for m in links_all.members}
        rows: list[MemberRow] = []
        for a in members:
            i = index[a] - 1
            s = by_address[a]
            label = labels.get(chain, a)
            times = [t.timestamp for t in clean[a]]
            partners = {j for j in range(len(members)) if j != i and (matrix[i][j] or matrix[j][i])}
            rows.append(
                MemberRow(
                    index=index[a],
                    address=a,
                    label=label.name if label else None,
                    is_focus=a in focus_set,
                    likely_service=s.likely_service,
                    transfer_count=len(clean[a]),
                    truncated=histories[a][1],
                    error=errors.get(a),
                    counterparty_count=s.counterparty_count,
                    first_seen=min(times, default=None),
                    last_seen=max(times, default=None),
                    usdt_frozen=s.usdt_frozen,
                    group=s.group,
                    sent_to_members=sum(matrix[i], Decimal(0)),
                    received_from_members=sum((matrix[j][i] for j in range(len(members))), Decimal(0)),
                    transfers_with_members=sum(counts[i]) + sum(counts[j][i] for j in range(len(members))),
                    partners=len(partners),
                )
            )
            if errors.get(a):
                warnings.append(f"member #{index[a]} could not be loaded: {errors[a]}")
            elif histories[a][1]:
                warnings.append(
                    f"member #{index[a]} has more than {member_limit} transfers; only the newest are included"
                )
        ranking = sorted(rows, key=lambda r: (r.total_with_members, r.transfers_with_members), reverse=True)

        # --- focus profiles -----------------------------------------------------------
        step("profiles", 0, len(focus))
        profiles = []
        for n, a in enumerate(focus, start=1):
            profiles.append(
                await self._profile(
                    chain, a, index, histories[a], clean[a], token, contract, by_address[a].usdt_frozen, labels
                )
            )
            if a in infos:
                profiles[-1].is_contract = infos[a].is_contract
            step("profiles", n, len(focus))

        # --- most connected other members ----------------------------------------------
        others = [r for r in ranking if not r.is_focus and r.total_with_members > 0][:MOST_CONNECTED]
        most_connected = [
            await self._member_profile(
                chain, r, members, matrix, counts, inside, histories[r.address], contract, token, labels
            )
            for r in others
        ]

        mentioned = set(members)
        for p in profiles:
            mentioned.update(s.address for s in p.top_senders + p.top_receivers)
        for path in links_focus.paths + links_all.paths:
            mentioned.update(path.via)
        mentioned.update(s.address for s in links_focus.shared + links_all.shared)
        names, categories, sources = {}, {}, {}
        for a in mentioned:
            label = labels.get(chain, a)
            if label is not None:
                names[a], categories[a], sources[a] = label.name, label.category.value, label.source

        # --- entry and exit points of the focus wallets -----------------------------------
        step("exits", 0, 1)
        exits, entries, creators, inferred = await self._exit_points(
            chain, focus, index, clean, contract, token, labels, infos
        )
        step("exits", 1, 1)

        # --- verification against TronScan ----------------------------------------------
        if self._tronscan is not None and chain == Chain.TRON and contract is not None:
            step("verification", 0, len(focus))
            for n, p in enumerate(profiles, start=1):
                ours = [t for t in clean[p.address] if t.token_contract == contract]
                all_ours = [t for t in histories[p.address][0] if t.token_contract is not None]
                p.verification = await self._tronscan.verify(p.address, token, contract, ours, all_ours)
                step("verification", n, len(focus))
        else:
            for p in profiles:
                ours = [t for t in clean[p.address] if _is_token(t, contract, token)]
                p.verification = compare(p.address, token, ours, None)
                p.verification.note = "TronScan verification is available for Tron tokens only"

        # --- values and labels ------------------------------------------------------------
        focus_history = {a: await self._records(chain, sorted(clean[a], key=lambda t: t.timestamp), index) for a in focus}
        toman = await self._values.toman_now()
        if toman.rate is None:
            warnings.append("toman rate unavailable (Nobitex and Wallex unreachable, no USD_TOMAN_RATE set)")

        return Investigation(
            generated_at=datetime.now(timezone.utc),
            chain=chain,
            token=token,
            token_contract=contract,
            focus=profiles,
            members=rows,
            focus_transfers=await self._records(chain, between_focus, index),
            member_transfers=await self._records(chain, inside, index),
            focus_history=focus_history,
            matrix=matrix,
            matrix_counts=counts,
            ranking=ranking,
            most_connected=most_connected,
            links_focus=links_focus,
            links_all=links_all,
            exits=exits,
            entries=entries,
            contract_creators=creators,
            inferred=inferred,
            labels=names,
            categories=categories,
            label_sources=sources,
            toman=toman,
            warnings=warnings,
        )

    async def _exit_points(
        self, chain, focus, index, clean, contract, token, labels, infos
    ) -> tuple[list[ExitPoint], list[ExitPoint], dict[str, str], dict[str, Inference]]:
        """Who the focus wallets' money went to and came from, with the owner worked out
        where the chain gives evidence (name tags, a known sweep/burn target, the contract's creator)."""
        scan = self._tronscan if chain == Chain.TRON else None
        inferred: dict[str, Inference] = {}
        creators: dict[str, str] = {}

        async def sample_out(address: str) -> list:
            if scan is None or not contract:
                return []
            try:
                return await scan.recent_transfers(address, contract, outgoing=True, limit=SAMPLE_TRANSFERS)
            except Exception as exc:  # a TronScan hiccup costs an inference, not the report
                log.info("outgoing sample of %s skipped: %s", address, exc)
                return []

        async def owner_of(address: str, depth: int = 0) -> Inference | None:
            """Where the wallet sent money to a target that implies an owner (a hack's burn
            address, a known sweep wallet); for a contract account, who deployed it."""
            if address in inferred:
                return inferred[address]
            label = labels.get(chain, address)
            if label is not None and label.category.value in ("exchange", "service"):
                return None  # a name is better than an inference
            found: Inference | None = None
            for t in sorted(await sample_out(address), key=lambda t: t.timestamp, reverse=True):
                target = labels.get(chain, t.to_address)
                if target is not None and target.implies:
                    found = Inference(
                        owner=target.implies,
                        basis=f"sent {t.amount} {token} to {target.name} on {t.timestamp:%Y-%m-%d}",
                        evidence_tx=t.tx_hash,
                        evidence_date=t.timestamp,
                    )
                    break
            if scan is not None and address not in infos:
                info = await scan.account(address)
                if info is not None:
                    infos[address] = info
            info = infos.get(address)
            if found is None and scan is not None and info is not None and info.is_contract and depth < 1:
                creator = await scan.contract_creator(address)
                if creator:
                    creators[address] = creator
                    upstream = await owner_of(creator, depth + 1)
                    creator_label = labels.get(chain, creator)
                    owner = upstream.owner if upstream else (creator_label.name if creator_label else None)
                    if owner:
                        found = Inference(
                            owner=owner,
                            basis=f"deposit contract created by {creator}, a wallet of {owner}",
                            evidence_tx=upstream.evidence_tx if upstream else None,
                            evidence_date=upstream.evidence_date if upstream else None,
                        )
            if found is not None:
                inferred[address] = found
            return found

        # Which counterparties matter: the ones that carry most of each focus wallet's flow.
        parties: dict[tuple[str, str], dict[str, list[Transfer]]] = defaultdict(lambda: defaultdict(list))
        totals: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
        for a in focus:
            for t in clean[a]:
                if not _is_token(t, contract, token):
                    continue
                if t.from_address == a:
                    parties[("exit", t.to_address)][a].append(t)
                    totals[("exit", a)] += t.amount
                elif t.to_address == a:
                    parties[("entry", t.from_address)][a].append(t)
                    totals[("entry", a)] += t.amount
        chosen: dict[str, set[str]] = {"exit": set(), "entry": set()}
        for role in ("exit", "entry"):
            for a in focus:
                ranked = sorted(
                    (
                        (other, sum((t.amount for t in by_focus[a]), Decimal(0)))
                        for (r, other), by_focus in parties.items()
                        if r == role and a in by_focus
                    ),
                    key=lambda kv: kv[1],
                    reverse=True,
                )
                running = Decimal(0)
                for n, (other, amount) in enumerate(ranked):
                    if n >= MAX_EXITS_PER_WALLET or (n > 0 and running >= totals[(role, a)] * EXIT_SHARE):
                        break
                    chosen[role].add(other)
                    running += amount

        for a in focus:  # a focus wallet that is a contract account: find its owner first
            if infos.get(a) and infos[a].is_contract:
                await owner_of(a)

        out: dict[str, list[ExitPoint]] = {"exit": [], "entry": []}
        for role in ("exit", "entry"):
            for other in chosen[role]:
                by_focus = parties[(role, other)]
                flows = []
                for a in focus:
                    ts = by_focus.get(a)
                    if not ts:
                        continue
                    total = sum((t.amount for t in ts), Decimal(0))
                    largest = sorted(ts, key=lambda t: t.amount, reverse=True)[:3]
                    flows.append(
                        ExitFlow(
                            index=index[a],
                            total=total,
                            count=len(ts),
                            first_seen=min(t.timestamp for t in ts),
                            last_seen=max(t.timestamp for t in ts),
                            share=(total / totals[(role, a)] * 100) if totals[(role, a)] else Decimal(0),
                            largest=await self._records(chain, largest, index),
                        )
                    )
                label = labels.get(chain, other)
                inference = None if other in focus else await owner_of(other)
                if inference is None and role == "exit":
                    # The sweep target of a deposit contract belongs to the contract's owner.
                    for a in focus:
                        f = next((x for x in flows if x.index == index[a]), None)
                        if f and f.share >= 90 and a in inferred and infos.get(a) and infos[a].is_contract:
                            inference = Inference(
                                owner=inferred[a].owner,
                                basis=f"receives {f.share:.0f}% of what leaves deposit contract #{index[a]} ({inferred[a].owner})",
                                evidence_tx=inferred[a].evidence_tx,
                                evidence_date=inferred[a].evidence_date,
                            )
                            inferred[other] = inference
                            break
                downstream: list[Downstream] = []
                sampled = 0
                if role == "exit" and other not in focus:
                    sample = await sample_out(other)
                    sampled = len(sample)
                    grouped: dict[str, list] = defaultdict(list)
                    for t in sample:
                        grouped[t.to_address].append(t)
                    for to, ts in sorted(grouped.items(), key=lambda kv: -sum(t.amount for t in kv[1]))[:5]:
                        to_label = labels.get(chain, to)
                        biggest = max(ts, key=lambda t: t.amount)
                        downstream.append(
                            Downstream(
                                address=to,
                                label=to_label.name if to_label else (f"{inferred[to].owner} (inferred)" if to in inferred else None),
                                amount=sum((t.amount for t in ts), Decimal(0)),
                                count=len(ts),
                                last_seen=max(t.timestamp for t in ts),
                                tx_hash=biggest.tx_hash,
                            )
                        )
                info = infos.get(other)
                all_ts = [t for ts in by_focus.values() for t in ts]
                out[role].append(
                    ExitPoint(
                        role=role,
                        address=other,
                        index=index.get(other),
                        label=label.name if label else None,
                        category=label.category.value if label else None,
                        label_source=label.source if label else None,
                        inference=inference,
                        is_contract=info.is_contract if info else None,
                        transactions=info.transactions if info else None,
                        flows=sorted(flows, key=lambda f: f.total, reverse=True),
                        total=sum((f.total for f in flows), Decimal(0)),
                        downstream=downstream,
                        downstream_sampled=sampled,
                        first_seen=min(t.timestamp for t in all_ts),
                        last_seen=max(t.timestamp for t in all_ts),
                    )
                )
            out[role].sort(key=lambda e: e.total, reverse=True)
        # Owners worked out late in the loop apply to earlier rows too.
        for e in out["exit"] + out["entry"]:
            for d in e.downstream:
                if d.label is None and d.address in inferred:
                    d.label = f"{inferred[d.address].owner} (inferred)"
            if e.inference is None and e.address in inferred:
                e.inference = inferred[e.address]
        return out["exit"], out["entry"], creators, inferred

    @staticmethod
    def _token_contract(chain: Chain, token: str, transfers: list[Transfer]) -> str | None:
        """The real contract behind a symbol like "USDT" (None for the native coin)."""
        if not token.isalnum() or len(token) > 12:
            return token  # already a contract address
        for t in transfers:
            if t.token_contract and known_symbol(chain, t.token_contract) == token.upper():
                return t.token_contract
        for t in transfers:
            if t.token_symbol.upper() == token.upper() and not is_spam(t):
                return t.token_contract
        return None

    async def _records(self, chain: Chain, transfers: list[Transfer], index: dict[str, int]) -> list[TransferRec]:
        values = await self._values.values(
            [(chain, t.token_symbol, t.token_contract, t.amount, t.timestamp) for t in transfers]
        )
        return [
            TransferRec(
                tx_hash=t.tx_hash,
                timestamp=t.timestamp,
                from_address=t.from_address,
                to_address=t.to_address,
                from_index=index.get(t.from_address),
                to_index=index.get(t.to_address),
                amount=t.amount,
                token_symbol=t.token_symbol,
                token_contract=t.token_contract,
                usd_then=v.usd_then,
                toman_then=v.toman_then,
            )
            for t, v in zip(transfers, values)
        ]

    def _tag_candidates(
        self,
        chain: Chain,
        focus: list[str],
        members: list[str],
        clean: dict[str, list[Transfer]],
        contract: str | None,
        token: str,
    ) -> list[str]:
        """Addresses worth a name-tag lookup: the members, the focus wallets' main
        counterparties, and outside wallets that dealt with several members."""
        member_set = set(members)
        touched: dict[str, set[str]] = defaultdict(set)
        volume: dict[str, Decimal] = defaultdict(Decimal)
        per_focus: dict[str, dict[str, Decimal]] = {a: defaultdict(Decimal) for a in focus}
        for a in members:
            for t in clean[a]:
                if not _is_token(t, contract, token):
                    continue
                other = t.counterparty_for(a)
                if other in member_set:
                    continue
                touched[other].add(a)
                volume[other] += t.amount
                if a in per_focus:
                    per_focus[a][other] += t.amount
        order = list(members)
        for a in focus:
            order.extend(sorted(per_focus[a], key=per_focus[a].get, reverse=True)[: 2 * TOP_PARTIES])
        shared = [o for o, ms in touched.items() if len(ms) >= 2]
        order.extend(sorted(shared, key=volume.get, reverse=True))
        order = [a for a in dict.fromkeys(order) if self._labels.get(chain, a) is None]
        return order[:MAX_TAG_LOOKUPS]

    @staticmethod
    def _parties(
        labels, chain: Chain, address: str, transfers: list[Transfer], index: dict[str, int], incoming: bool
    ) -> list[PartyStat]:
        grouped: dict[str, list[Transfer]] = defaultdict(list)
        for t in transfers:
            if incoming and t.to_address == address:
                grouped[t.from_address].append(t)
            elif not incoming and t.from_address == address:
                grouped[t.to_address].append(t)
        out = []
        for other, ts in grouped.items():
            label = labels.get(chain, other)
            out.append(
                PartyStat(
                    address=other,
                    index=index.get(other),
                    label=label.name if label else None,
                    category=label.category.value if label else None,
                    amount=sum((t.amount for t in ts), Decimal(0)),
                    count=len(ts),
                    first_seen=min(t.timestamp for t in ts),
                    last_seen=max(t.timestamp for t in ts),
                )
            )
        return sorted(out, key=lambda p: p.amount, reverse=True)[:TOP_PARTIES]

    async def _totals(self, chain: Chain, address: str, transfers: list[Transfer]) -> list[TokenTotals]:
        values = await self._values.values(
            [(chain, t.token_symbol, t.token_contract, t.amount, t.timestamp) for t in transfers]
        )
        totals: dict[tuple[str, str | None], TokenTotals] = {}

        def add(current: Decimal | None, value: Decimal | None) -> Decimal | None:
            return current if value is None else (current or Decimal(0)) + value

        for t, v in zip(transfers, values):
            row = totals.setdefault(
                (t.token_symbol, t.token_contract),
                TokenTotals(token_symbol=t.token_symbol, token_contract=t.token_contract),
            )
            if t.to_address == address:
                row.total_in += t.amount
                row.count_in += 1
                row.usd_in_then = add(row.usd_in_then, v.usd_then)
                row.toman_in_then = add(row.toman_in_then, v.toman_then)
                row.usd_in_now = add(row.usd_in_now, v.usd_now)
            else:
                row.total_out += t.amount
                row.count_out += 1
                row.usd_out_then = add(row.usd_out_then, v.usd_then)
                row.toman_out_then = add(row.toman_out_then, v.toman_then)
                row.usd_out_now = add(row.usd_out_now, v.usd_now)
        return sorted(totals.values(), key=lambda r: r.count_in + r.count_out, reverse=True)

    async def _balances(self, chain: Chain, address: str) -> list[BalanceRec]:
        provider = self._providers.get(chain)
        try:
            raw = [b for b in await provider.get_balances(address)]
        except ProviderError as exc:
            log.warning("balances of %s unavailable: %s", address, exc)
            return []
        raw = [b for b in raw if b.token_contract is None or known_symbol(chain, b.token_contract)]
        values = await self._values.values([(chain, b.token_symbol, b.token_contract, b.amount, None) for b in raw])
        return [
            BalanceRec(
                token_symbol=b.token_symbol, token_contract=b.token_contract, amount=b.amount, usd=v.usd_now, toman=v.toman_now
            )
            for b, v in zip(raw, values)
        ]

    async def _profile(
        self,
        chain: Chain,
        address: str,
        index: dict[str, int],
        history: tuple[list[Transfer], bool],
        clean: list[Transfer],
        token: str,
        contract: str | None,
        usdt_frozen: bool | None,
        labels,
    ) -> FocusProfile:
        transfers, truncated = history
        of_token = [t for t in clean if _is_token(t, contract, token)]
        findings = analyze_transfers(chain, address, transfers, labels, truncated)
        findings.sort(key=lambda f: f.points, reverse=True)
        score = min(100, sum(f.points for f in findings))
        times = [t.timestamp for t in clean]
        label = labels.get(chain, address)
        largest = sorted(of_token, key=lambda t: t.amount, reverse=True)[:LARGEST]
        return FocusProfile(
            index=index[address],
            address=address,
            label=label.name if label else None,
            first_seen=min(times, default=None),
            last_seen=max(times, default=None),
            transfer_count=len(clean),
            spam_count=sum(1 for t in transfers if is_spam(t)),
            truncated=truncated,
            counterparty_count=len({t.counterparty_for(address) for t in clean}),
            balances=await self._balances(chain, address),
            totals=await self._totals(chain, address, clean),
            risk_score=score,
            risk_level=_level(score),
            findings=findings,
            stats=wallet_stats(address, transfers),
            usdt_frozen=usdt_frozen,
            top_senders=self._parties(labels, chain, address, of_token, index, incoming=True),
            top_receivers=self._parties(labels, chain, address, of_token, index, incoming=False),
            largest=await self._records(chain, largest, index),
            monthly={
                token: monthly_series(address, clean, contract or token),
                **({"TRX": monthly_series(address, [t for t in clean if t.token_contract is None], "TRX")} if chain == Chain.TRON and token != "TRX" else {}),
            },
        )

    async def _member_profile(
        self,
        chain: Chain,
        row: MemberRow,
        members: list[str],
        matrix: list[list[Decimal]],
        counts: list[list[int]],
        inside: list[Transfer],
        history: tuple[list[Transfer], bool],
        contract: str | None,
        token: str,
        labels,
    ) -> MemberProfile:
        transfers, truncated = history
        a, i = row.address, row.index - 1
        clean = _clean(transfers)
        of_token = [t for t in clean if _is_token(t, contract, token)]
        partners = []
        for j, other in enumerate(members):
            if j == i or not (matrix[i][j] or matrix[j][i]):
                continue
            ts = [
                t
                for t in inside
                if _is_token(t, contract, token) and {t.from_address, t.to_address} == {a, other}
            ]
            partners.append(
                PartnerFlow(
                    index=j + 1,
                    address=other,
                    sent=matrix[i][j],
                    received=matrix[j][i],
                    count=counts[i][j] + counts[j][i],
                    first_seen=min(t.timestamp for t in ts),
                    last_seen=max(t.timestamp for t in ts),
                )
            )
        partners.sort(key=lambda p: p.sent + p.received, reverse=True)
        findings = analyze_transfers(chain, a, transfers, labels, truncated)
        findings.sort(key=lambda f: f.points, reverse=True)
        score = min(100, sum(f.points for f in findings))
        balances = await self._balances(chain, a)
        # The balance call lists non-zero tokens only: no entry means nothing is held.
        balance = next((b.amount for b in balances if _is_token_balance(b, contract, token)), Decimal(0) if balances else None)
        return MemberProfile(
            index=row.index,
            address=a,
            likely_service=row.likely_service,
            counterparty_count=row.counterparty_count,
            first_seen=row.first_seen,
            last_seen=row.last_seen,
            truncated=truncated,
            sent_to_members=row.sent_to_members,
            received_from_members=row.received_from_members,
            token_in=sum((t.amount for t in of_token if t.to_address == a), Decimal(0)),
            token_out=sum((t.amount for t in of_token if t.from_address == a), Decimal(0)),
            balance=balance,
            risk_score=score,
            risk_level=_level(score),
            findings=findings,
            partners=partners,
        )


def _is_token_balance(b: BalanceRec, contract: str | None, symbol: str) -> bool:
    if contract is not None:
        return b.token_contract == contract
    return b.token_contract is None and b.token_symbol.upper() == symbol.upper()


def is_service(inv: Investigation, address: str) -> bool:
    """Labelled exchange/service, or a list member with hundreds of counterparties."""
    category = inv.categories.get(address)
    if category is not None and category in {c.value for c in TERMINAL_CATEGORIES}:
        return True
    return any(m.address == address and m.likely_service for m in inv.members)
