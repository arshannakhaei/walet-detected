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


class RateLimitError(ProviderError):
    """The API kept answering "too many requests" after all retries."""


@dataclass
class TransferPage:
    transfers: list[Transfer]
    truncated: bool  # more history exists beyond what was fetched


class ChainProvider(ABC):
    chain: Chain
    # Upper bound on history per address for chains where fetching is expensive.
    history_cap: int | None = None
    # False when get_balances only returns the native coin (token balances are
    # then derived from transfer history).
    reports_token_balances: bool = True

    # Extra HTTP statuses that mean "slow down" for this API (TronGrid answers 403
    # during its 30-second block after too many requests).
    rate_limit_statuses: frozenset[int] = frozenset()
    # Seconds to wait before each retry; free APIs need patience, not speed.
    retry_delays: tuple[float, ...] = (1, 2, 4, 8, 16)

    def __init__(self, client: httpx.AsyncClient, limiter: RateLimiter, retries: int | None = None):
        self._client = client
        self._limiter = limiter
        self._retries = retries if retries is not None else len(self.retry_delays) + 1

    def _retry_delay(self, attempt: int, response: httpx.Response | None) -> float:
        if response is not None:
            try:
                return min(30.0, float(response.headers.get("retry-after", "")))
            except ValueError:
                pass
        return self.retry_delays[min(attempt, len(self.retry_delays) - 1)]

    async def _request(self, method: str, url: str, **kwargs):
        last_error: Exception | None = None
        rate_limited = False
        timeouts = 0
        for attempt in range(self._retries):
            await self._limiter.wait()
            response: httpx.Response | None = None
            try:
                response = await self._client.request(method, url, **kwargs)
                status = response.status_code
                if status == 429 or status >= 500 or status in self.rate_limit_statuses:
                    rate_limited = rate_limited or status == 429 or status in self.rate_limit_statuses
                    raise httpx.HTTPStatusError(f"HTTP {status}", request=response.request, response=response)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as exc:
                last_error = exc
                status = exc.response.status_code
                if status < 500 and status != 429 and status not in self.rate_limit_statuses:
                    break  # client errors (bad address, bad key) will not fix themselves
            except httpx.TimeoutException as exc:
                last_error = exc
                timeouts += 1
                if timeouts >= 2:
                    break  # a slow API stays slow; do not wait minutes on it
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
            if attempt + 1 >= self._retries:
                break
            delay = self._retry_delay(attempt, response)
            log.warning("request to %s failed (attempt %d): %s; retrying in %.0fs", url, attempt + 1, last_error, delay)
            await asyncio.sleep(delay)
        if rate_limited:
            raise RateLimitError(
                f"{self.chain.value}: rate limit of the free API reached (HTTP 429). "
                "Wait a minute, or add a free API key in Settings."
            ) from last_error
        raise ProviderError(f"{self.chain.value}: request failed: {last_error}") from last_error

    async def _get_json(self, url: str, params: dict | None = None, headers: dict | None = None):
        return await self._request("GET", url, params=params, headers=headers)

    async def _post_json(self, url: str, payload: dict | list, headers: dict | None = None):
        return await self._request("POST", url, json=payload, headers=headers)

    @abstractmethod
    async def get_transfers(self, address: str, max_items: int) -> TransferPage:
        """All transfers (native + tokens) involving `address`, newest first."""

    @abstractmethod
    async def get_balances(self, address: str) -> list[TokenBalance]:
        """Current balances of the native coin and known tokens."""
