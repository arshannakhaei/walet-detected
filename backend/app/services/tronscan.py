"""TronScan as an independent second source, to verify numbers taken from TronGrid.

An investigation report states transfer counts and totals; before anyone relies
on them they are compared with what the TronScan explorer reports for the same
wallet and token. The two services index the chain separately, so agreement
between them is good evidence the numbers are right.
"""

import asyncio
import logging
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal

import httpx
from pydantic import BaseModel

from app.models import Transfer

log = logging.getLogger(__name__)

PAGE = 50  # TronScan's maximum page size
MAX_OFFSET = 10_000  # start + limit may not exceed this
TX_URL = "https://tronscan.org/#/transaction/{}"
ADDRESS_URL = "https://tronscan.org/#/address/{}"


class TronScanError(Exception):
    pass


class ScanTransfer(BaseModel):
    tx_hash: str
    timestamp: datetime
    from_address: str
    to_address: str
    amount: Decimal


class Verification(BaseModel):
    """Our numbers for one wallet and token next to TronScan's."""

    address: str
    token_symbol: str
    status: str  # "verified", "mismatch" or "unavailable"
    our_count: int
    our_in: Decimal
    our_out: Decimal
    scan_count: int | None = None
    scan_in: Decimal | None = None
    scan_out: Decimal | None = None
    scan_reported_total: int | None = None  # the "total" field TronScan itself reports
    only_ours: list[str] = []  # tx hashes we have and TronScan does not (at most 20)
    only_scan: list[str] = []
    note: str | None = None


class AccountInfo(BaseModel):
    address: str
    tag: str | None = None  # e.g. "Binance-Hot 2"
    is_contract: bool = False
    name: str | None = None
    transactions: int | None = None


EXCHANGE_WORDS = (
    "binance", "okx", "okex", "huobi", "htx", "kucoin", "bybit", "gate", "mexc", "bitget", "kraken", "coinbase",
    "poloniex", "bitfinex", "bitmart", "bingx", "whitebit", "exchange", "nobitex", "wallex", "bitpin", "ramzinex",
    "tabdeal", "exir", "coinex", "lbank", "bitstamp", "crypto.com", "upbit", "bithumb", "hot", "deposit",
)  # fmt: skip


def tag_category(tag: str) -> str:
    """A TronScan name tag marks a known entity: an exchange when the name says so, else a service."""
    lower = tag.lower()
    return "exchange" if any(w in lower for w in EXCHANGE_WORDS) else "service"


def parse_transfers(items: list[dict]) -> list[ScanTransfer]:
    """Normalize `token_transfers` items of `/token_trc20/transfers`."""
    out = []
    for item in items:
        info = item.get("tokenInfo") or {}
        decimals = int(info.get("tokenDecimal") or item.get("decimals") or 0)
        if item.get("contractRet") not in (None, "SUCCESS") or item.get("revert"):
            continue
        out.append(
            ScanTransfer(
                tx_hash=item["transaction_id"],
                timestamp=datetime.fromtimestamp(int(item["block_ts"]) / 1000, tz=timezone.utc),
                from_address=item["from_address"],
                to_address=item["to_address"],
                amount=Decimal(str(item["quant"])).scaleb(-decimals),
            )
        )
    return out


def compare(address: str, symbol: str, ours: list[Transfer], scan: list[ScanTransfer] | None) -> Verification:
    """`ours`: this wallet's transfers of the token (successful, non-zero)."""
    v = Verification(
        address=address,
        token_symbol=symbol,
        status="unavailable",
        our_count=len(ours),
        our_in=sum((t.amount for t in ours if t.to_address == address), Decimal(0)),
        our_out=sum((t.amount for t in ours if t.from_address == address), Decimal(0)),
    )
    if scan is None:
        return v
    scan = [t for t in scan if t.amount > 0]
    v.scan_count = len(scan)
    v.scan_in = sum((t.amount for t in scan if t.to_address == address), Decimal(0))
    v.scan_out = sum((t.amount for t in scan if t.from_address == address), Decimal(0))
    # The same transaction can hold several transfers: compare as multisets.
    mine = Counter((t.tx_hash, t.from_address, t.to_address, t.amount) for t in ours)
    theirs = Counter((t.tx_hash, t.from_address, t.to_address, t.amount) for t in scan)
    v.only_ours = sorted({k[0] for k in (mine - theirs)})[:20]
    v.only_scan = sorted({k[0] for k in (theirs - mine)})[:20]
    same = mine == theirs and v.our_in == v.scan_in and v.our_out == v.scan_out
    v.status = "verified" if same else "mismatch"
    return v


class TronScanClient:
    def __init__(
        self, client: httpx.AsyncClient, base_url: str, api_key: str = "", requests_per_second: float = 4.0
    ):
        self._client = client
        self._base = base_url.rstrip("/")
        self._headers = {"TRON-PRO-API-KEY": api_key} if api_key else {}
        self._interval = 1.0 / requests_per_second
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def _get(self, path: str, params: dict) -> dict:
        last: Exception | None = None
        for attempt in range(4):
            async with self._lock:  # one request at a time, spaced out
                wait = self._last + self._interval - asyncio.get_running_loop().time()
                if wait > 0:
                    await asyncio.sleep(wait)
                self._last = asyncio.get_running_loop().time()
                try:
                    resp = await self._client.get(f"{self._base}{path}", params=params, headers=self._headers)
                    if resp.status_code in (403, 429) or resp.status_code >= 500:
                        raise httpx.HTTPStatusError(f"HTTP {resp.status_code}", request=resp.request, response=resp)
                    resp.raise_for_status()
                    data = resp.json()
                    if isinstance(data, dict):
                        return data
                    raise ValueError("unexpected answer")
                except (httpx.HTTPError, ValueError) as exc:
                    last = exc
            await asyncio.sleep(1.5 * (attempt + 1))
        raise TronScanError(f"TronScan request failed: {last}")

    async def trc20_transfers(self, address: str, contract: str) -> tuple[list[ScanTransfer], int | None]:
        """Every transfer of one TRC20 token involving `address`, and the total TronScan reports."""
        out: list[ScanTransfer] = []
        reported: int | None = None
        start = 0
        while start < MAX_OFFSET:
            data = await self._get(
                "/token_trc20/transfers",
                {
                    "relatedAddress": address,
                    "contract_address": contract,
                    "limit": PAGE,
                    "start": start,
                },
            )
            items = data.get("token_transfers") or []
            # TronScan answers "10000" when it has not counted; only a smaller number is a real count.
            if reported is None and isinstance(data.get("total"), int) and data["total"] < MAX_OFFSET:
                reported = data["total"]
            out.extend(parse_transfers(items))
            if len(items) < PAGE:
                return out, reported
            start += PAGE
        raise TronScanError(f"more than {MAX_OFFSET} transfers: TronScan does not page that far")

    async def verify(self, address: str, symbol: str, contract: str, ours: list[Transfer]) -> Verification:
        try:
            scan, reported = await self.trc20_transfers(address, contract)
        except TronScanError as exc:
            log.warning("verification of %s skipped: %s", address, exc)
            v = compare(address, symbol, ours, None)
            v.note = str(exc)
            return v
        v = compare(address, symbol, ours, scan)
        v.scan_reported_total = reported
        return v

    async def account(self, address: str) -> "AccountInfo | None":
        """Public facts TronScan shows for an address: name tag, contract or not."""
        try:
            data = await self._get("/accountv2", {"address": address})
        except TronScanError:
            return None
        tag = next(
            (data[k].strip() for k in ("addressTag", "publicTag", "blueTag") if isinstance(data.get(k), str) and data[k].strip()),
            None,
        )
        name = data.get("name") if isinstance(data.get("name"), str) else None
        return AccountInfo(
            address=address,
            tag=tag,
            is_contract=data.get("accountType") == 2 or name == "CreatedByContract",
            name=name or None,
            transactions=data.get("totalTransactionCount") if isinstance(data.get("totalTransactionCount"), int) else None,
        )
