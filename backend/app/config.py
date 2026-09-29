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

    # Fetch limits
    max_transfers_per_address: int = 2000
    page_size: int = 200
    # Re-fetch an address from the chain when its cached data is older than this.
    cache_ttl_seconds: int = 300

    http_timeout_seconds: float = 20.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
