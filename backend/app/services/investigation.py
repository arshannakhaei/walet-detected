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
from app.services.labels import TERMINAL_CATEGORIES, LabelService
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
MAX_TAG_LOOKUPS = 120  # outside addresses whose TronScan name tag is looked up


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

        # --- link analyses --------------------------------------------------------
        snapshot = _Snapshot(self._wallets, histories)
        analyzer = LinkAnalyzer(snapshot, self._labels, self._hub_threshold, sanctions=self._sanctions)
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
            label = self._labels.get(chain, a)
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
                await self._profile(chain, a, index, histories[a], clean[a], token, contract, by_address[a].usdt_frozen)
            )
            step("profiles", n, len(focus))

        # --- most connected other members ----------------------------------------------
        others = [r for r in ranking if not r.is_focus and r.total_with_members > 0][:MOST_CONNECTED]
        most_connected = [
            await self._member_profile(chain, r, members, matrix, counts, inside, histories[r.address], contract, token)
            for r in others
        ]

        # --- verification against TronScan ----------------------------------------------
        if self._tronscan is not None and chain == Chain.TRON and contract is not None:
            step("verification", 0, len(focus))
            for n, p in enumerate(profiles, start=1):
                ours = [t for t in clean[p.address] if t.token_contract == contract]
                p.verification = await self._tronscan.verify(p.address, token, contract, ours)
                step("verification", n, len(focus))
        else:
            for p in profiles:
                ours = [t for t in clean[p.address] if _is_token(t, contract, token)]
                p.verification = compare(p.address, token, ours, None)
                p.verification.note = "TronScan verification is available for Tron tokens only"

        # --- values and labels ------------------------------------------------------------
        focus_history = {a: await self._records(chain, sorted(clean[a], key=lambda t: t.timestamp), index) for a in focus}
        mentioned = set(members)
        ordered: list[str] = []  # outside addresses, most important first
        for p in profiles:
            ordered.extend(s.address for s in p.top_senders + p.top_receivers)
        for path in links_focus.paths + links_all.paths:
            ordered.extend(path.via)
        ordered.extend(s.address for s in links_focus.shared + links_all.shared)
        for mp in most_connected:
            ordered.extend(pf.address for pf in mp.partners)
        mentioned.update(ordered)
        labels, categories, sources = {}, {}, {}
        for a in mentioned:
            label = self._labels.get(chain, a)
            if label is not None:
                labels[a], categories[a], sources[a] = label.name, label.category.value, label.source
        if self._tronscan is not None and chain == Chain.TRON:
            lookups = [a for a in dict.fromkeys(focus + ordered) if a not in labels][: MAX_TAG_LOOKUPS + len(focus)]
            step("labels", 0, len(lookups))
            infos: dict[str, AccountInfo] = {}
            for n, a in enumerate(lookups, start=1):
                info = await self._tronscan.account(a)
                if info is not None:
                    infos[a] = info
                    if info.tag:
                        labels[a], categories[a], sources[a] = info.tag, tag_category(info.tag), "tronscan"
                step("labels", n, len(lookups))
            for p in profiles:
                if p.address in infos:
                    p.is_contract = infos[p.address].is_contract
        for p in profiles:
            for party in p.top_senders + p.top_receivers:
                if party.label is None and party.address in labels:
                    party.label, party.category = labels[party.address], categories[party.address]

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
            labels=labels,
            categories=categories,
            label_sources=sources,
            toman=toman,
            warnings=warnings,
        )

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

    def _parties(
        self, chain: Chain, address: str, transfers: list[Transfer], index: dict[str, int], incoming: bool
    ) -> list[PartyStat]:
        grouped: dict[str, list[Transfer]] = defaultdict(list)
        for t in transfers:
            if incoming and t.to_address == address:
                grouped[t.from_address].append(t)
            elif not incoming and t.from_address == address:
                grouped[t.to_address].append(t)
        out = []
        for other, ts in grouped.items():
            label = self._labels.get(chain, other)
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
    ) -> FocusProfile:
        transfers, truncated = history
        of_token = [t for t in clean if _is_token(t, contract, token)]
        findings = analyze_transfers(chain, address, transfers, self._labels, truncated)
        findings.sort(key=lambda f: f.points, reverse=True)
        score = min(100, sum(f.points for f in findings))
        times = [t.timestamp for t in clean]
        label = self._labels.get(chain, address)
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
            top_senders=self._parties(chain, address, of_token, index, incoming=True),
            top_receivers=self._parties(chain, address, of_token, index, incoming=False),
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
        findings = analyze_transfers(chain, a, transfers, self._labels, truncated)
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
