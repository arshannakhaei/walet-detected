"""EVM chains (Ethereum, BSC, Polygon, Arbitrum, Optimism, Base, Avalanche) through
Etherscan-compatible APIs.

With an Etherscan API key, the Etherscan V2 multichain endpoint serves every
chain with one key. Without a key, chains that have a public Blockscout
instance (same API shape, no key needed) use that instead.
"""

import asyncio
import logging
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal

import httpx

from app.models import Chain, TokenBalance, Transfer
from app.providers.base import ChainProvider, ProviderError, RateLimitError, TransferPage
from app.providers.ratelimit import RateLimiter

log = logging.getLogger(__name__)

# chain -> (chain id, native symbol)
EVM_NETWORKS: dict[Chain, tuple[int, str]] = {
    Chain.ETHEREUM: (1, "ETH"),
    Chain.BSC: (56, "BNB"),
    Chain.POLYGON: (137, "POL"),
    Chain.ARBITRUM: (42161, "ETH"),
    Chain.OPTIMISM: (10, "ETH"),
    Chain.BASE: (8453, "ETH"),
    Chain.AVALANCHE: (43114, "AVAX"),
}

BLOCKSCOUT_URLS: dict[Chain, str] = {
    Chain.ETHEREUM: "https://eth.blockscout.com/api",
    Chain.POLYGON: "https://polygon.blockscout.com/api",
    Chain.ARBITRUM: "https://arbitrum.blockscout.com/api",
    Chain.OPTIMISM: "https://optimism.blockscout.com/api",
    Chain.BASE: "https://base.blockscout.com/api",
}

NATIVE_DECIMALS = 18
# Etherscan refuses page * offset > 10000.
MAX_RESULT_WINDOW = 10_000


def _ts(seconds: str | int) -> datetime:
    return datetime.fromtimestamp(int(seconds), tz=timezone.utc)


def _scale(raw: str | int, decimals: int) -> Decimal:
    return Decimal(str(raw or 0)).scaleb(-decimals)


def _addr(value: str | None) -> str:
    return (value or "").lower()


def parse_native(items: list[dict], chain: Chain, symbol: str) -> list[Transfer]:
    out = []
    for item in items:
        if int(item.get("value") or 0) == 0 or not item.get("to"):
            continue  # contract calls without value, contract creations
        out.append(
            Transfer(
                chain=chain,
                transfer_id=f"{item['hash']}:native",
                tx_hash=item["hash"],
                timestamp=_ts(item["timeStamp"]),
                from_address=_addr(item["from"]),
                to_address=_addr(item["to"]),
                amount=_scale(item["value"], NATIVE_DECIMALS),
                token_symbol=symbol,
                token_decimals=NATIVE_DECIMALS,
                success=item.get("isError", "0") == "0",
            )
        )
    return out


def parse_internal(items: list[dict], chain: Chain, symbol: str) -> list[Transfer]:
    """Native value moved by contracts (e.g. a withdrawal from a DEX or mixer)."""
    out = []
    seen: Counter[str] = Counter()
    for item in items:
        if int(item.get("value") or 0) == 0 or not item.get("to"):
            continue
        key = f"{item['hash']}:internal:{item.get('traceId') or ''}:{_addr(item['from'])}:{_addr(item['to'])}"
        seen[key] += 1
        out.append(
            Transfer(
                chain=chain,
                transfer_id=key if seen[key] == 1 else f"{key}#{seen[key]}",
                tx_hash=item["hash"],
                timestamp=_ts(item["timeStamp"]),
                from_address=_addr(item["from"]),
                to_address=_addr(item["to"]),
                amount=_scale(item["value"], NATIVE_DECIMALS),
                token_symbol=symbol,
                token_decimals=NATIVE_DECIMALS,
                success=item.get("isError", "0") == "0",
            )
        )
    return out


def parse_tokens(items: list[dict], chain: Chain) -> list[Transfer]:
    out = []
    seen: Counter[str] = Counter()
    for item in items:
        decimals = int(item.get("tokenDecimal") or 0)
        if item.get("logIndex") not in (None, ""):
            key = f"{item['hash']}:log:{item['logIndex']}"
        else:
            key = f"{item['hash']}:{_addr(item['contractAddress'])}:{_addr(item['from'])}:{_addr(item['to'])}:{item['value']}"
        seen[key] += 1
        out.append(
            Transfer(
                chain=chain,
                transfer_id=key if seen[key] == 1 else f"{key}#{seen[key]}",
                tx_hash=item["hash"],
                timestamp=_ts(item["timeStamp"]),
                from_address=_addr(item["from"]),
                to_address=_addr(item["to"]),
                amount=_scale(item["value"], decimals),
                token_symbol=(item.get("tokenSymbol") or "UNKNOWN")[:40],
                token_contract=_addr(item["contractAddress"]),
                token_decimals=decimals,
            )
        )
    return out


class EvmProvider(ChainProvider):
    reports_token_balances = False

    def __init__(
        self,
        chain: Chain,
        client: httpx.AsyncClient,
        limiter: RateLimiter,
        base_url: str,
        api_key: str = "",
        page_size: int = 1000,
    ):
        super().__init__(client, limiter)
        self.chain = chain
        self._chain_id, self._symbol = EVM_NETWORKS[chain]
        self._base = base_url
        self._api_key = api_key
        self._page_size = page_size
        # Etherscan V2 needs the chain id; Blockscout instances are per chain.
        self._is_etherscan = "etherscan" in base_url

    async def _call(self, params: dict) -> list | str:
        params = dict(params)
        if self._is_etherscan:
            params["chainid"] = self._chain_id
        if self._api_key:
            params["apikey"] = self._api_key
        for attempt in range(len(self.retry_delays) + 1):
            data = await self._get_json(self._base, params=params)
            status = str(data.get("status", "1"))
            result = data.get("result")
            if status == "1":
                return result
            message = str(data.get("message", ""))
            if "no transactions found" in message.lower() or "no records found" in message.lower() or result == []:
                return []
            # Etherscan reports its rate limit inside a normal 200 response.
            if "rate limit" in f"{message} {result}".lower() and attempt < len(self.retry_delays):
                await asyncio.sleep(self.retry_delays[attempt])
                continue
            if "rate limit" in f"{message} {result}".lower():
                raise RateLimitError(
                    f"{self.chain.value}: rate limit of the free API reached. "
                    "Wait a minute, or add a free Etherscan API key in Settings."
                )
            raise ProviderError(f"{self.chain.value}: {message}: {result}")
        raise ProviderError(f"{self.chain.value}: no answer")  # pragma: no cover

    async def _list(self, action: str, address: str, max_items: int) -> tuple[list[dict], bool]:
        items: list[dict] = []
        offset = min(self._page_size, max_items, MAX_RESULT_WINDOW)
        page = 1
        while True:
            try:
                batch = await self._call(
                    {
                        "module": "account",
                        "action": action,
                        "address": address,
                        "startblock": 0,
                        "endblock": 99_999_999,
                        "page": page,
                        "offset": offset,
                    "sort": "desc",
                    }
                )
            except ProviderError:
                if not items:
                    raise
                log.warning("%s %s: stopped after %d items: API refused more", self.chain.value, action, len(items))
                return items, True  # keep what we have; the page shows it as incomplete
            items.extend(batch)
            if len(batch) < offset:
                return items, False
            if len(items) >= max_items or (page + 1) * offset > MAX_RESULT_WINDOW:
                return items[:max_items], True
            page += 1

    async def get_transfers(self, address: str, max_items: int) -> TransferPage:
        address = address.lower()
        # The three lists download side by side; the rate limiter still spaces requests.
        native_r, tokens_r, internal_r = await asyncio.gather(
            self._list("txlist", address, max_items),
            self._list("tokentx", address, max_items),
            self._list("txlistinternal", address, max_items),
            return_exceptions=True,
        )
        if isinstance(native_r, BaseException):
            raise native_r
        native, more_native = native_r
        if isinstance(tokens_r, RateLimitError) and native:
            tokens, more_tokens = [], True  # show native transfers rather than nothing
        elif isinstance(tokens_r, BaseException):
            raise tokens_r
        else:
            tokens, more_tokens = tokens_r
        if isinstance(internal_r, BaseException):  # optional: not every explorer supports it, and it is slow
            log.info("internal transactions unavailable on %s: %s", self.chain.value, internal_r)
            internal, more_internal = [], False
        else:
            internal, more_internal = internal_r
        transfers = (
            parse_native(native, self.chain, self._symbol)
            + parse_internal(internal, self.chain, self._symbol)
            + parse_tokens(tokens, self.chain)
        )
        transfers.sort(key=lambda t: t.timestamp, reverse=True)
        return TransferPage(transfers, truncated=more_native or more_tokens or more_internal)

    async def get_balances(self, address: str) -> list[TokenBalance]:
        raw = await self._call({"module": "account", "action": "balance", "address": address.lower(), "tag": "latest"})
        return [TokenBalance(token_symbol=self._symbol, amount=_scale(raw, NATIVE_DECIMALS))]
