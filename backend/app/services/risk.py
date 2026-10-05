"""Behavioural pattern detection and a 0-100 risk score for one wallet.

Every finding carries the evidence (addresses, transactions) that triggered it,
so an investigator can check it rather than trust a number. The score is a
capped sum of finding points; it ranks wallets for attention and is not a
verdict.
"""

import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel

from app.models import Chain, Direction, Transfer
from app.services.graph import GraphBuilder, GraphParams
from app.services.labels import RISKY_CATEGORIES, LabelCategory, LabelService
from app.services.tokens import STABLECOINS, is_spam, known_symbol
from app.services.tracer import LotMethod, allocate
from app.services.wallet import WalletService

MAX_EVIDENCE = 20


class Severity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Finding(BaseModel):
    code: str
    severity: Severity
    points: int
    title: str
    detail: str
    evidence: list[str] = []


class WalletStats(BaseModel):
    main_token: str | None
    lifetime_days: float | None
    active_days: int
    median_holding_hours: float | None
    pass_through_ratio: float | None  # out / in of the main token
    distinct_senders: int
    distinct_receivers: int


class RiskReport(BaseModel):
    chain: Chain
    address: str
    score: int
    level: str
    findings: list[Finding]
    stats: WalletStats
    truncated: bool


def _level(score: int) -> str:
    if score >= 80:
        return "critical"
    if score >= 50:
        return "high"
    if score >= 20:
        return "medium"
    return "low"


def _is_round(amount: Decimal) -> bool:
    return amount >= 100 and amount % 100 == 0


def _max_distinct_in_window(events: list[tuple[datetime, str]], window: timedelta) -> tuple[int, list[str]]:
    """Largest number of distinct counterparties seen within any `window`."""
    events = sorted(events)
    best: tuple[int, list[str]] = (0, [])
    start = 0
    for end in range(len(events)):
        while events[end][0] - events[start][0] > window:
            start += 1
        distinct = {a for _, a in events[start : end + 1]}
        if len(distinct) > best[0]:
            best = (len(distinct), sorted(distinct))
    return best


def _looks_alike(a: str, b: str) -> bool:
    """Address poisoning copies the first and last characters of a real counterparty."""
    if a == b or len(a) != len(b) or len(a) < 12:
        return False
    head = 4 if not a.startswith("0x") else 6
    return a[:head].lower() == b[:head].lower() and a[-4:].lower() == b[-4:].lower()


# Counterparties checked against the live sanctions/freeze lists in a deep analysis.
SANCTION_COUNTERPARTIES = 20


class RiskAnalyzer:
    def __init__(self, wallets: WalletService, labels: LabelService, graphs: GraphBuilder, sanctions=None):
        self._wallets = wallets
        self._labels = labels
        self._graphs = graphs
        self._sanctions = sanctions  # SanctionsChecker, optional

    async def analyze(self, chain: Chain, address: str, deep: bool = False) -> RiskReport:
        transfers, truncated = await self._wallets.load_transfers(chain, address, quick=True)
        findings = analyze_transfers(chain, address, transfers, self._labels, truncated)
        if self._sanctions is not None and self._sanctions.supports(chain):
            findings.extend(await self._sanction_findings(chain, address, transfers, deep))
        if deep:
            findings.extend(await self._indirect_exposure(chain, address))
        findings.sort(key=lambda f: f.points, reverse=True)
        score = min(100, sum(f.points for f in findings))
        return RiskReport(
            chain=chain,
            address=address,
            score=score,
            level=_level(score),
            findings=findings,
            stats=wallet_stats(address, transfers),
            truncated=truncated,
        )

    async def _sanction_findings(
        self, chain: Chain, address: str, transfers: list[Transfer], deep: bool
    ) -> list[Finding]:
        findings: list[Finding] = []
        own = await self._sanctions.check(chain, address)
        if own.usdt_frozen:
            findings.append(
                Finding(
                    code="usdt_frozen",
                    severity=Severity.CRITICAL,
                    points=100,
                    title="USDT frozen by Tether",
                    detail="Tether has blacklisted this address: its USDT cannot move. Tether freezes wallets "
                    "on request of law enforcement, typically for scams, hacks and sanctions.",
                    evidence=[address],
                )
            )
        if own.sanctioned:
            findings.append(
                Finding(
                    code="sanctioned_oracle",
                    severity=Severity.CRITICAL,
                    points=100,
                    title="Sanctioned (Chainalysis sanctions oracle)",
                    detail="The address is on a US, EU or UN sanctions list according to the Chainalysis oracle.",
                    evidence=[address],
                )
            )
        if not deep:
            return findings

        volume: dict[str, Decimal] = {}
        for t in transfers:
            if t.success and not is_spam(t) and t.from_address != t.to_address:
                other = t.counterparty_for(address)
                volume[other] = volume.get(other, Decimal(0)) + t.amount
        top = sorted(volume, key=volume.get, reverse=True)[:SANCTION_COUNTERPARTIES]
        statuses = await self._sanctions.check_many(chain, top)
        frozen = [s.address for s in statuses if s.usdt_frozen]
        sanctioned = [s.address for s in statuses if s.sanctioned]
        if frozen:
            findings.append(
                Finding(
                    code="frozen_counterparty",
                    severity=Severity.HIGH,
                    points=30,
                    title="Transacted with wallets frozen by Tether",
                    detail=f"{len(frozen)} of the {len(top)} main counterparties have had their USDT frozen by Tether.",
                    evidence=frozen[:MAX_EVIDENCE],
                )
            )
        if sanctioned:
            findings.append(
                Finding(
                    code="sanctioned_counterparty",
                    severity=Severity.HIGH,
                    points=40,
                    title="Transacted with sanctioned wallets",
                    detail=f"{len(sanctioned)} of the {len(top)} main counterparties are on a sanctions list "
                    "(Chainalysis oracle).",
                    evidence=sanctioned[:MAX_EVIDENCE],
                )
            )
        return findings

    async def _indirect_exposure(self, chain: Chain, address: str) -> list[Finding]:
        graph = await self._graphs.build(
            chain, address, GraphParams(depth_in=2, depth_out=2, max_nodes=80, max_children=10)
        )
        hits = [
            n
            for n in graph.nodes
            if abs(n.depth) == 2 and n.label is not None and n.label.category in RISKY_CATEGORIES
        ]
        if not hits:
            return []
        return [
            Finding(
                code="indirect_exposure",
                severity=Severity.MEDIUM,
                points=15,
                title="Indirect exposure to high-risk addresses",
                detail=f"{len(hits)} high-risk address(es) two hops away "
                f"({', '.join(sorted({n.label.category.value for n in hits}))}).",
                evidence=[n.address for n in hits][:MAX_EVIDENCE],
            )
        ]


def _main_token(clean: list[Transfer]) -> tuple[str | None, str | None]:
    counts: dict[tuple[str, str | None], int] = defaultdict(int)
    for t in clean:
        counts[(t.token_symbol, t.token_contract)] += 1
    if not counts:
        return None, None
    return max(counts, key=counts.get)


def wallet_stats(address: str, transfers: list[Transfer]) -> WalletStats:
    clean = [t for t in transfers if t.success and not is_spam(t)]
    symbol, contract = _main_token(clean)
    main = [t for t in clean if (t.token_symbol, t.token_contract) == (symbol, contract)]
    total_in = sum((t.amount for t in main if t.to_address == address), Decimal(0))
    total_out = sum((t.amount for t in main if t.from_address == address), Decimal(0))

    by_id = {t.transfer_id: t for t in main}
    holding: list[float] = []
    for a in allocate(main, address, LotMethod.FIFO):
        if a.inflow_id is None:
            continue
        hours = (by_id[a.outflow_id].timestamp - by_id[a.inflow_id].timestamp).total_seconds() / 3600
        holding.append(hours)

    times = [t.timestamp for t in clean]
    return WalletStats(
        main_token=symbol,
        lifetime_days=(max(times) - min(times)).total_seconds() / 86400 if times else None,
        active_days=len({t.date() for t in times}),
        median_holding_hours=statistics.median(holding) if holding else None,
        pass_through_ratio=float(total_out / total_in) if total_in else None,
        distinct_senders=len({t.from_address for t in clean if t.to_address == address}),
        distinct_receivers=len({t.to_address for t in clean if t.from_address == address}),
    )


# A wallet dealing with this many different addresses is a service (exchange
# hot wallet, payment processor), whose high activity is normal.
SERVICE_COUNTERPARTIES = 500
# Patterns that are suspicious for a person's wallet but routine for a service.
ROUTINE_FOR_SERVICES = {"fan_in", "fan_out", "structuring", "round_amounts", "pass_through", "rapid_movement"}


def analyze_transfers(
    chain: Chain, address: str, transfers: list[Transfer], labels: LabelService, truncated: bool = False
) -> list[Finding]:
    """`truncated`: only the newest part of the history is known, so the wallet's age is not."""
    findings: list[Finding] = []
    ok = [t for t in transfers if t.success]
    clean = [t for t in ok if not is_spam(t)]
    stats = wallet_stats(address, transfers)

    # --- labels -------------------------------------------------------------
    own = labels.get(chain, address)
    if own is not None and own.category in RISKY_CATEGORIES:
        findings.append(
            Finding(
                code="flagged_address",
                severity=Severity.CRITICAL,
                points=100,
                title=f"Address itself is labeled {own.category.value}",
                detail=own.name,
                evidence=[address],
            )
        )

    risky: dict[str, list[Transfer]] = defaultdict(list)
    exchanges: dict[str, list[Transfer]] = defaultdict(list)
    for t in clean:
        other = t.counterparty_for(address)
        label = labels.get(chain, other)
        if label is None:
            continue
        if label.category in RISKY_CATEGORIES:
            risky[other].append(t)
        elif label.category == LabelCategory.EXCHANGE:
            exchanges[other].append(t)
    if risky:
        sent = any(t.from_address == address for ts in risky.values() for t in ts)
        received = any(t.to_address == address for ts in risky.values() for t in ts)
        cats = sorted({labels.get(chain, a).category.value for a in risky})
        findings.append(
            Finding(
                code="direct_exposure",
                severity=Severity.HIGH,
                points=40 if received else 30,
                title="Direct transactions with high-risk addresses",
                detail=f"{sum(len(v) for v in risky.values())} transfer(s) with {len(risky)} address(es) "
                f"labeled {', '.join(cats)}"
                + (" — received funds from them" if received else "")
                + (" — sent funds to them" if sent else ""),
                evidence=[f"{a} ({labels.get(chain, a).name})" for a in risky][:MAX_EVIDENCE],
            )
        )
    if exchanges:
        findings.append(
            Finding(
                code="exchange_exposure",
                severity=Severity.INFO,
                points=0,
                title="Transacts with exchanges",
                detail="Exchanges can identify the owner of deposit accounts (KYC). "
                + ", ".join(sorted({labels.get(chain, a).name for a in exchanges})),
                evidence=list(exchanges)[:MAX_EVIDENCE],
            )
        )

    # --- flow behaviour -----------------------------------------------------
    ratio = stats.pass_through_ratio
    if ratio is not None and 0.9 <= ratio <= 1.1 and stats.median_holding_hours is not None:
        if stats.median_holding_hours < 24:
            findings.append(
                Finding(
                    code="pass_through",
                    severity=Severity.MEDIUM,
                    points=20,
                    title="Pass-through wallet",
                    detail=f"Sends on {ratio:.0%} of what it receives in {stats.main_token}, "
                    f"typically within {stats.median_holding_hours:.1f} h. Common in layering.",
                )
            )
    if stats.median_holding_hours is not None and stats.median_holding_hours < 1:
        findings.append(
            Finding(
                code="rapid_movement",
                severity=Severity.MEDIUM,
                points=15,
                title="Funds move on very quickly",
                detail=f"Median time between receiving and sending: {stats.median_holding_hours * 60:.0f} min.",
            )
        )
    if (
        stats.lifetime_days is not None
        and stats.lifetime_days < 7
        and len(clean) <= 12
        and ratio is not None
        and ratio >= 0.9
    ):
        findings.append(
            Finding(
                code="short_lived_intermediary",
                severity=Severity.MEDIUM,
                points=15,
                title="Short-lived intermediary",
                detail=f"Used for {stats.lifetime_days:.1f} days, {len(clean)} transfers, then emptied. "
                "Typical of peel chains and throwaway hops.",
            )
        )

    window = timedelta(hours=24)
    fan_in, senders = _max_distinct_in_window(
        [(t.timestamp, t.from_address) for t in clean if t.to_address == address], window
    )
    if fan_in >= 10:
        findings.append(
            Finding(
                code="fan_in",
                severity=Severity.MEDIUM,
                points=10,
                title="Many senders in a short time",
                detail=f"{fan_in} different addresses sent funds within 24 h (collection / aggregation).",
                evidence=senders[:MAX_EVIDENCE],
            )
        )
    fan_out, receivers = _max_distinct_in_window(
        [(t.timestamp, t.to_address) for t in clean if t.from_address == address], window
    )
    if fan_out >= 10:
        findings.append(
            Finding(
                code="fan_out",
                severity=Severity.MEDIUM,
                points=10,
                title="Funds spread to many wallets quickly",
                detail=f"{fan_out} different addresses received funds within 24 h (distribution / smurfing).",
                evidence=receivers[:MAX_EVIDENCE],
            )
        )

    # --- amounts ------------------------------------------------------------
    outgoing = [t for t in clean if t.from_address == address]
    if len(outgoing) >= 5:
        rounds = [t for t in outgoing if _is_round(t.amount)]
        if len(rounds) / len(outgoing) >= 0.5:
            findings.append(
                Finding(
                    code="round_amounts",
                    severity=Severity.LOW,
                    points=5,
                    title="Mostly round amounts",
                    detail=f"{len(rounds)} of {len(outgoing)} outgoing transfers are round numbers.",
                    evidence=[t.tx_hash for t in rounds][:MAX_EVIDENCE],
                )
            )
    stable = [
        t
        for t in clean
        if (known_symbol(chain, t.token_contract) or "") in STABLECOINS
    ]
    below = [t for t in stable if Decimal(9000) <= t.amount < Decimal(10000)]
    if len(below) >= 3:
        findings.append(
            Finding(
                code="structuring",
                severity=Severity.MEDIUM,
                points=10,
                title="Amounts just below 10,000",
                detail=f"{len(below)} stablecoin transfers between 9,000 and 9,999 — possible structuring "
                "to stay under reporting thresholds.",
                evidence=[t.tx_hash for t in below][:MAX_EVIDENCE],
            )
        )
    if not truncated and stats.lifetime_days is not None and stats.lifetime_days < 30:
        volume = sum((t.amount for t in stable), Decimal(0))
        if volume >= 100_000:
            findings.append(
                Finding(
                    code="new_high_volume",
                    severity=Severity.MEDIUM,
                    points=10,
                    title="New wallet with high volume",
                    detail=f"Moved {volume:,.0f} in stablecoins within {stats.lifetime_days:.0f} days of first use.",
                )
            )

    times = sorted(t.timestamp for t in clean)
    gaps = [(b - a, b) for a, b in zip(times, times[1:])]
    if gaps:
        gap, resumed = max(gaps)
        if gap >= timedelta(days=180):
            findings.append(
                Finding(
                    code="dormant_reactivated",
                    severity=Severity.LOW,
                    points=5,
                    title="Dormant wallet reactivated",
                    detail=f"No activity for {gap.days} days, then active again on {resumed.date()}.",
                )
            )

    # --- address poisoning --------------------------------------------------
    real_partners = {t.counterparty_for(address) for t in clean if t.amount > 0}
    lookalikes: dict[str, str] = {}  # impostor -> genuine partner
    for t in ok:
        if t.to_address != address:
            continue
        sender = t.from_address
        if sender in real_partners and not (is_spam(t) or t.amount < 1):
            continue
        for partner in real_partners:
            if _looks_alike(sender, partner):
                lookalikes[sender] = partner
    if lookalikes:
        paid = [
            t
            for t in outgoing
            if t.to_address in lookalikes and t.amount >= 1
        ]
        if paid:
            findings.append(
                Finding(
                    code="poisoning_victim",
                    severity=Severity.HIGH,
                    points=0,  # victim, not suspect
                    title="Possibly lost funds to address poisoning",
                    detail=f"Sent {len(paid)} transfer(s) to look-alike address(es) that first sent it dust.",
                    evidence=[f"{t.tx_hash}: {t.amount} {t.token_symbol} to {t.to_address}" for t in paid][
                        :MAX_EVIDENCE
                    ],
                )
            )
        else:
            findings.append(
                Finding(
                    code="poisoning_target",
                    severity=Severity.INFO,
                    points=0,
                    title="Targeted by address poisoning",
                    detail="Received dust from addresses imitating its real counterparties. "
                    "Always check the full address before sending.",
                    evidence=[f"{fake} imitates {real}" for fake, real in lookalikes.items()][:MAX_EVIDENCE],
                )
            )
    counterparties = {t.counterparty_for(address) for t in clean} - {address}
    if len(counterparties) >= SERVICE_COUNTERPARTIES:
        for f in findings:
            if f.code in ROUTINE_FOR_SERVICES:
                f.points = 0
        findings.append(
            Finding(
                code="likely_service",
                severity=Severity.INFO,
                points=0,
                title="Probably an exchange or service wallet",
                detail=f"Dealt with {len(counterparties):,}"
                + ("+" if truncated else "")
                + " different addresses. Mass payouts and deposits are routine for exchanges and payment "
                "services; transfers with it do not show common ownership.",
            )
        )
    return findings


def timeline(
    address: str, transfers: list[Transfer], bucket: str = "day", token: str | None = None
) -> list[dict]:
    """In/out totals per period and token, oldest first."""
    rows: dict[tuple[str, str, str | None], dict] = {}
    for t in transfers:
        if not t.success or is_spam(t):
            continue
        if token and token.upper() != t.token_symbol.upper() and token != t.token_contract:
            continue
        d = t.timestamp
        if bucket == "month":
            period = f"{d.year:04d}-{d.month:02d}"
        elif bucket == "week":
            monday = (d - timedelta(days=d.weekday())).date()
            period = monday.isoformat()
        else:
            period = d.date().isoformat()
        key = (period, t.token_symbol, t.token_contract)
        row = rows.setdefault(
            key,
            {
                "period": period,
                "token_symbol": t.token_symbol,
                "token_contract": t.token_contract,
                "amount_in": Decimal(0),
                "amount_out": Decimal(0),
                "count_in": 0,
                "count_out": 0,
            },
        )
        direction = t.direction_for(address)
        if direction == Direction.IN:
            row["amount_in"] += t.amount
            row["count_in"] += 1
        elif direction == Direction.OUT:
            row["amount_out"] += t.amount
            row["count_out"] += 1
    return sorted(rows.values(), key=lambda r: r["period"])
