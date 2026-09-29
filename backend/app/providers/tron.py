"""Tron data via the TronGrid v1 API: TRX transfers, TRC20 transfers, balances."""

import asyncio
import logging
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal

import httpx

from app.models import Chain, TokenBalance, Transfer
from app.providers.base import ChainProvider, ProviderError, TransferPage
from app.providers.ratelimit import RateLimiter
from app.services.addresses import tron_hex_to_base58

log = logging.getLogger(__name__)

TRX_DECIMALS = 6

# Well-known TRC20 tokens, so balances can be shown before any transfer history is seen.
KNOWN_TRC20: dict[str, tuple[str, int]] = {
    "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t": ("USDT", 6),
    "TEkxiTehnzSmSe2XqrBj4w32RUN966rdz8": ("USDC", 6),
}


def _ts(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def _scale(raw: str | int, decimals: int) -> Decimal:
    return Decimal(str(raw)).scaleb(-decimals)


def parse_trc20_transfers(items: list[dict]) -> list[Transfer]:
    """Normalize items from `/v1/accounts/{addr}/transactions/trc20`."""
    out: list[Transfer] = []
    seen: Counter[str] = Counter()
    for item in items:
        if item.get("type", "Transfer") != "Transfer":
            continue
        info = item.get("token_info") or {}
        decimals = int(info.get("decimals") or 0)
        contract = info.get("address")
        base_id = f"{item['transaction_id']}:{item['from']}:{item['to']}:{contract}:{item['value']}"
        # The same tx can repeat an identical transfer; keep ids unique.
        seen[base_id] += 1
        transfer_id = base_id if seen[base_id] == 1 else f"{base_id}#{seen[base_id]}"
        out.append(
            Transfer(
                chain=Chain.TRON,
                transfer_id=transfer_id,
                tx_hash=item["transaction_id"],
                timestamp=_ts(item["block_timestamp"]),
                from_address=item["from"],
                to_address=item["to"],
                amount=_scale(item["value"], decimals),
                token_symbol=info.get("symbol") or "UNKNOWN",
                token_contract=contract,
                token_decimals=decimals,
            )
        )
    return out


def parse_trx_transfers(items: list[dict]) -> list[Transfer]:
    """Normalize native TRX transfers from `/v1/accounts/{addr}/transactions`.

    Other contract types (votes, freezes, smart-contract calls) are skipped;
    token movements from contract calls come from the TRC20 endpoint instead.
    """
    out: list[Transfer] = []
    for item in items:
        contracts = (item.get("raw_data") or {}).get("contract") or []
        if not contracts or contracts[0].get("type") != "TransferContract":
            continue
        value = contracts[0]["parameter"]["value"]
        ret = (item.get("ret") or [{}])[0]
        out.append(
            Transfer(
                chain=Chain.TRON,
                transfer_id=f"{item['txID']}:native",
                tx_hash=item["txID"],
                timestamp=_ts(item["block_timestamp"]),
                from_address=tron_hex_to_base58(value["owner_address"]),
                to_address=tron_hex_to_base58(value["to_address"]),
                amount=_scale(value["amount"], TRX_DECIMALS),
                token_symbol="TRX",
                token_contract=None,
                token_decimals=TRX_DECIMALS,
                success=ret.get("contractRet", "SUCCESS") == "SUCCESS",
            )
        )
    return out


class TronProvider(ChainProvider):
    chain = Chain.TRON
    # TronGrid answers 403 during the 30-second block that follows too many requests.
    rate_limit_statuses = frozenset({403})

    def __init__(
        self,
        client: httpx.AsyncClient,
        limiter: RateLimiter,
        base_url: str,
        api_key: str = "",
        page_size: int = 200,
    ):
        super().__init__(client, limiter)
        self._base = base_url.rstrip("/")
        self._headers = {"TRON-PRO-API-KEY": api_key} if api_key else {}
        self._page_size = page_size
        # contract -> (symbol, decimals), learned from transfer history
        self._token_info: dict[str, tuple[str, int]] = dict(KNOWN_TRC20)

    async def _paginate(self, path: str, params: dict, max_items: int) -> tuple[list[dict], bool]:
        items: list[dict] = []
        params = {**params, "limit": self._page_size, "only_confirmed": "true"}
        while True:
            try:
                data = await self._get_json(f"{self._base}{path}", params=params, headers=self._headers)
            except ProviderError:
                if not items:
                    raise
                log.warning("tron %s: stopped after %d items: API refused more", path, len(items))
                return items, True  # keep what we have; the page shows it as incomplete
            items.extend(data.get("data") or [])
            fingerprint = (data.get("meta") or {}).get("fingerprint")
            if not fingerprint:
                return items, False
            if len(items) >= max_items:
                return items[:max_items], True
            params["fingerprint"] = fingerprint

    async def get_transfers(self, address: str, max_items: int) -> TransferPage:
        trc20_r, trx_r = await asyncio.gather(
            self._paginate(f"/v1/accounts/{address}/transactions/trc20", {}, max_items),
            self._paginate(f"/v1/accounts/{address}/transactions", {}, max_items),
            return_exceptions=True,
        )
        if isinstance(trc20_r, BaseException):
            raise trc20_r
        trc20_raw, trc20_more = trc20_r
        if isinstance(trx_r, ProviderError) and trc20_raw:
            trx_raw, trx_more = [], True  # show the token transfers rather than nothing
        elif isinstance(trx_r, BaseException):
            raise trx_r
        else:
            trx_raw, trx_more = trx_r
        transfers = parse_trc20_transfers(trc20_raw) + parse_trx_transfers(trx_raw)
        for t in transfers:
            if t.token_contract:
                self._token_info.setdefault(t.token_contract, (t.token_symbol, t.token_decimals))
        transfers.sort(key=lambda t: t.timestamp, reverse=True)
        return TransferPage(transfers=transfers, truncated=trc20_more or trx_more)

    async def get_balances(self, address: str) -> list[TokenBalance]:
        data = await self._get_json(f"{self._base}/v1/accounts/{address}", headers=self._headers)
        accounts = data.get("data") or []
        if not accounts:
            return [TokenBalance(token_symbol="TRX", amount=Decimal(0))]
        account = accounts[0]
        balances = [
            TokenBalance(token_symbol="TRX", amount=_scale(account.get("balance", 0), TRX_DECIMALS))
        ]
        for entry in account.get("trc20") or []:
            for contract, raw in entry.items():
                info = self._token_info.get(contract)
                if info is None:
                    log.debug("skipping balance of unknown TRC20 token %s", contract)
                    continue
                symbol, decimals = info
                amount = _scale(raw, decimals)
                if amount:
                    balances.append(
                        TokenBalance(token_symbol=symbol, token_contract=contract, amount=amount)
                    )
        return balances
