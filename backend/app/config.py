"""Application settings, loaded from environment variables or a `.env` file."""

from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
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
    port: int = 8765  # 8000 is often reserved on Windows (Hyper-V/WSL) or taken

    # Offline demo: Tron serves a built-in synthetic scam scenario (no internet needed)
    demo_mode: bool = False
    # Allow API keys to be changed from any client, not only 127.0.0.1. Docker
    # needs this (requests arrive from the bridge network); compose publishes the
    # port on 127.0.0.1 only, so it stays local.
    settings_from_any_client: bool = False

    # Storage
    database_url: str = f"sqlite+aiosqlite:///{(BASE_DIR / 'data' / 'chaintrace.db').as_posix()}"

    # Tron
    trongrid_base_url: str = "https://api.trongrid.io"
    trongrid_api_key: str = ""  # optional; raises rate limits when set
    # None = automatic: TronGrid allows 1 request/s without a key and ~15/s with one.
    tron_requests_per_second: float | None = None

    # EVM chains. With a (free) Etherscan key, Etherscan V2 serves all EVM
    # chains; without one, Blockscout is used where a public instance exists.
    etherscan_api_key: str = ""
    etherscan_base_url: str = "https://api.etherscan.io/v2/api"
    # None = automatic: 4/s with an Etherscan key (limit 5), 3/s on shared Blockscout.
    evm_requests_per_second: float | None = None

    # Alchemy (optional): faster RPC for the free Chainalysis sanctions oracle on
    # EVM chains; without it public RPCs are used.
    alchemy_api_key: str = ""

    # Bitcoin (Esplora API: mempool.space or blockstream.info/api)
    bitcoin_api_url: str = "https://mempool.space/api"
    bitcoin_requests_per_second: float = 2.0
    # Esplora returns 25 transactions per request; keep busy addresses fast.
    bitcoin_max_transactions: int = 300

    # Solana JSON-RPC (a free Helius / QuickNode URL is much faster than the public one)
    solana_rpc_url: str = "https://api.mainnet-beta.solana.com"
    solana_requests_per_second: float = 4.0
    # Solana needs one request per transaction, so it has its own lower cap.
    solana_max_transactions: int = 300

    # Prices
    coingecko_base_url: str = "https://api.coingecko.com/api/v3"
    coingecko_api_key: str = ""
    # Toman per US dollar. Empty = live market rate (Nobitex, then Wallex); set it
    # when those are unreachable (e.g. outside Iran) or to fix the rate for a report.
    usd_toman_rate: Decimal | None = None
    nobitex_base_url: str = "https://api.nobitex.ir"
    wallex_base_url: str = "https://api.wallex.ir"
    binance_base_url: str = "https://api.binance.com"

    # Fetch limits
    max_transfers_per_address: int = 1000
    page_size: int = 200
    # While building graphs and traces, an address with more transfers than
    # this is treated as a hub (exchange, service) and not expanded further.
    hub_threshold: int = 500
    # Re-fetch an address from the chain when its cached data is older than this.
    cache_ttl_seconds: int = 300

    http_timeout_seconds: float = 20.0

    # Watchlist polling (seconds between checks; 0 disables the background monitor)
    monitor_interval_seconds: int = 120

    # Telegram bot: token from @BotFather; comma-separated user ids allowed to use it
    telegram_bot_token: str = ""
    telegram_allowed_users: str = ""
    # Address the dashboard is reachable at, used for links in bot messages
    public_url: str = ""

    @field_validator("usd_toman_rate", mode="before")
    @classmethod
    def _empty_rate(cls, v):
        return None if v in ("", None) else v

    @property
    def tron_rate(self) -> float:
        if self.tron_requests_per_second is not None:
            return self.tron_requests_per_second
        return 12.0 if self.trongrid_api_key else 0.9

    @property
    def evm_rate(self) -> float:
        if self.evm_requests_per_second is not None:
            return self.evm_requests_per_second
        return 4.0 if self.etherscan_api_key else 3.0

    @property
    def telegram_user_ids(self) -> set[int]:
        return {int(x) for x in self.telegram_allowed_users.replace(" ", "").split(",") if x}


@lru_cache
def get_settings() -> Settings:
    return Settings()


ENV_FILE = PROJECT_ROOT / ".env"


def update_env_file(updates: dict[str, str], path: Path = ENV_FILE) -> None:
    """Set KEY=value lines in .env, keeping every other line (and comments) as they are."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    pending = {k.upper(): v for k, v in updates.items()}
    out = []
    for line in lines:
        key = line.split("=", 1)[0].strip().upper() if "=" in line and not line.lstrip().startswith("#") else None
        if key in pending:
            out.append(f"{key}={pending.pop(key)}")
        else:
            out.append(line)
    out.extend(f"{k}={v}" for k, v in pending.items())
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
