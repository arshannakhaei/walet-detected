"""Registry that builds one provider per supported chain."""

import logging

import httpx

from app.config import Settings
from app.models import Chain
from app.providers.base import ChainProvider, ProviderError, RateLimitError, TransferPage
from app.providers.bitcoin import BitcoinProvider
from app.providers.evm import BLOCKSCOUT_URLS, EVM_NETWORKS, EvmProvider
from app.providers.ratelimit import RateLimiter
from app.providers.solana import SolanaProvider
from app.providers.tron import TronProvider

__all__ = ["ChainProvider", "ProviderError", "ProviderRegistry", "RateLimitError", "TransferPage"]

log = logging.getLogger(__name__)


class ProviderRegistry:
    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self._client = client
        self._providers: dict[Chain, ChainProvider] = {}
        self.reload(settings)

    def reload(self, settings: Settings) -> None:
        """(Re)build every provider from the settings, e.g. after an API key was added.

        The registry object stays the same, so services holding it see the change.
        """
        client = self._client
        providers: dict[Chain, ChainProvider] = {
            Chain.TRON: TronProvider(
                client,
                RateLimiter(settings.tron_rate),
                base_url=settings.trongrid_base_url,
                api_key=settings.trongrid_api_key,
                page_size=settings.page_size,
            ),
        }
        bitcoin = BitcoinProvider(client, RateLimiter(settings.bitcoin_requests_per_second), settings.bitcoin_api_url)
        bitcoin.history_cap = settings.bitcoin_max_transactions
        providers[Chain.BITCOIN] = bitcoin

        solana = SolanaProvider(client, RateLimiter(settings.solana_requests_per_second), settings.solana_rpc_url)
        solana.history_cap = settings.solana_max_transactions
        providers[Chain.SOLANA] = solana

        # One Etherscan key has one rate limit shared by all chains.
        etherscan_limiter = RateLimiter(settings.evm_rate)
        for chain in EVM_NETWORKS:
            if settings.etherscan_api_key:
                providers[chain] = EvmProvider(
                    chain, client, etherscan_limiter, settings.etherscan_base_url, settings.etherscan_api_key
                )
            elif chain in BLOCKSCOUT_URLS:
                providers[chain] = EvmProvider(chain, client, RateLimiter(settings.evm_rate), BLOCKSCOUT_URLS[chain])
            else:
                log.info("%s disabled: set ETHERSCAN_API_KEY to enable it", chain.value)

        if settings.demo_mode:
            from app.providers.demo import DemoProvider

            providers[Chain.TRON] = DemoProvider()
            log.warning("DEMO MODE: Tron data is synthetic")
        self._providers = providers

    def get(self, chain: Chain) -> ChainProvider | None:
        return self._providers.get(chain)

    @property
    def supported_chains(self) -> list[Chain]:
        return list(self._providers)
