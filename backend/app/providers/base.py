"""Interface every blockchain data provider implements."""

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx

from app.models import Chain, TokenBalance, Transfer
from app.providers.ratelimit import RateLimiter

log = logging.getLogger(__name__)


class ProviderError(Exception):
    """Raised when a data source fails after retries."""


@dataclass
class TransferPage:
    transfers: list[Transfer]
    truncated: bool  # more history exists beyond what was fetched


class ChainProvider(ABC):
    chain: Chain

    def __init__(self, client: httpx.AsyncClient, limiter: RateLimiter, retries: int = 3):
        self._client = client
        self._limiter = limiter
        self._retries = retries

    async def _get_json(self, url: str, params: dict | None = None, headers: dict | None = None) -> dict:
        last_error: Exception | None = None
        for attempt in range(self._retries):
            await self._limiter.wait()
            try:
                resp = await self._client.get(url, params=params, headers=headers)
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        f"HTTP {resp.status_code}", request=resp.request, response=resp
                    )
                resp.raise_for_status()
                return resp.json()
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                log.warning("request to %s failed (attempt %d): %s", url, attempt + 1, exc)
                await asyncio.sleep(0.5 * 2**attempt)
        raise ProviderError(f"{self.chain.value}: request failed: {last_error}") from last_error

    @abstractmethod
    async def get_transfers(self, address: str, max_items: int) -> TransferPage:
        """All transfers (native + tokens) involving `address`, newest first."""

    @abstractmethod
    async def get_balances(self, address: str) -> list[TokenBalance]:
        """Current balances of the native coin and known tokens."""
