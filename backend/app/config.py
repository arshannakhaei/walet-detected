"""Application settings, loaded from environment variables or a `.env` file."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BASE_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Server
    host: str = "127.0.0.1"
    port: int = 8000

    # Storage
    database_url: str = f"sqlite+aiosqlite:///{(BASE_DIR / 'data' / 'chaintrace.db').as_posix()}"

    # Tron
    trongrid_base_url: str = "https://api.trongrid.io"
    trongrid_api_key: str = ""  # optional; raises rate limits when set
    tron_requests_per_second: float = 3.0

    # EVM chains. With a (free) Etherscan key, Etherscan V2 serves all EVM
    # chains; without one, Blockscout is used where a public instance exists.
    etherscan_api_key: str = ""
    etherscan_base_url: str = "https://api.etherscan.io/v2/api"
    evm_requests_per_second: float = 4.0

    # Bitcoin (Esplora API: mempool.space or blockstream.info/api)
    bitcoin_api_url: str = "https://mempool.space/api"
    bitcoin_requests_per_second: float = 2.0

    # Solana JSON-RPC (a free Helius / QuickNode URL is much faster than the public one)
    solana_rpc_url: str = "https://api.mainnet-beta.solana.com"
    solana_requests_per_second: float = 4.0
    # Solana needs one request per transaction, so it has its own lower cap.
    solana_max_transactions: int = 300

    # Prices
    coingecko_base_url: str = "https://api.coingecko.com/api/v3"
    coingecko_api_key: str = ""

    # Fetch limits
    max_transfers_per_address: int = 2000
    page_size: int = 200
    # While building graphs and traces, an address with more transfers than
    # this is treated as a hub (exchange, service) and not expanded further.
    hub_threshold: int = 1000
    # Re-fetch an address from the chain when its cached data is older than this.
    cache_ttl_seconds: int = 300

    http_timeout_seconds: float = 20.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
