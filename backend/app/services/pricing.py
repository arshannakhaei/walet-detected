"""Current USD prices (CoinGecko free API), cached in memory.

Prices are best effort: when CoinGecko is unreachable or rate-limited the
dashboard simply shows no USD values. Stablecoins are priced at 1 without a
request.
"""

import logging
import time
from decimal import Decimal

import httpx

from app.models import Chain
from app.services.tokens import STABLECOINS, known_symbol

log = logging.getLogger(__name__)

# Symbol -> CoinGecko id, for native coins and wrapped versions of them.
COINGECKO_IDS = {
    "TRX": "tron",
    "ETH": "ethereum",
    "WETH": "ethereum",
    "BNB": "binancecoin",
    "POL": "polygon-ecosystem-token",
    "AVAX": "avalanche-2",
    "BTC": "bitcoin",
    "WBTC": "bitcoin",
    "SOL": "solana",
}


class PriceService:
    def __init__(self, client: httpx.AsyncClient, base_url: str, api_key: str = "", ttl: int = 600):
        self._client = client
        self._base = base_url.rstrip("/")
        self._headers = {"x-cg-demo-api-key": api_key} if api_key else {}
        self._ttl = ttl
        self._prices: dict[str, Decimal] = {}
        self._fetched_at = 0.0

    async def _refresh(self) -> None:
        if time.monotonic() - self._fetched_at < self._ttl and self._prices:
            return
        ids = ",".join(sorted(set(COINGECKO_IDS.values())))
        try:
            resp = await self._client.get(
                f"{self._base}/simple/price",
                params={"ids": ids, "vs_currencies": "usd"},
                headers=self._headers,
            )
            resp.raise_for_status()
            data = resp.json()
            self._prices = {
                cg_id: Decimal(str(entry["usd"])) for cg_id, entry in data.items() if "usd" in entry
            }
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            log.warning("price refresh failed: %s", exc)
        # Also back off after a failure, so a dead API is not hit on every request.
        self._fetched_at = time.monotonic()

    async def price(self, chain: Chain, symbol: str, contract: str | None) -> Decimal | None:
        if contract is not None:
            # Only trust symbols of tokens we know; anyone can deploy a token called "USDT".
            symbol = known_symbol(chain, contract) or ""
        symbol = symbol.upper()
        if symbol in STABLECOINS:
            return Decimal(1)
        cg_id = COINGECKO_IDS.get(symbol)
        if cg_id is None:
            return None
        await self._refresh()
        return self._prices.get(cg_id)
