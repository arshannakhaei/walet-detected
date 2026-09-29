"""Registry that builds one provider per supported chain."""

import httpx

from app.config import Settings
from app.models import Chain
from app.providers.base import ChainProvider, ProviderError, TransferPage
from app.providers.ratelimit import RateLimiter
from app.providers.tron import TronProvider

__all__ = ["ChainProvider", "ProviderError", "ProviderRegistry", "TransferPage"]


class ProviderRegistry:
    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self._providers: dict[Chain, ChainProvider] = {
            Chain.TRON: TronProvider(
                client,
                RateLimiter(settings.tron_requests_per_second),
                base_url=settings.trongrid_base_url,
                api_key=settings.trongrid_api_key,
                page_size=settings.page_size,
            ),
        }

    def get(self, chain: Chain) -> ChainProvider | None:
        return self._providers.get(chain)

    @property
    def supported_chains(self) -> list[Chain]:
        return list(self._providers)
