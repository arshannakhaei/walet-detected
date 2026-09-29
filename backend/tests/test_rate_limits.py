"""Behaviour under free-API rate limits: retries, partial results, one fetch per
address, and adding API keys from the dashboard."""

import asyncio

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.config import Settings, update_env_file
from app.main import create_app
from app.models import Chain
from app.providers.base import RateLimitError
from app.providers.evm import EvmProvider
from app.providers.ratelimit import RateLimiter
from app.providers.tron import TronProvider
from app.services.wallet import WalletService
from tests.conftest import WALLET, trc20
from tests.test_tracing import SCAMMER, SCENARIO, FakeProvider, FakeRegistry

TG = "https://api.trongrid.io"
ETHERSCAN = "https://api.etherscan.io/v2/api"
EVM = "0x" + "ab" * 20


def fast(provider):
    provider.retry_delays = (0.01, 0.01, 0.01)
    provider._retries = 4
    return provider


async def test_retry_after_header_is_honoured_and_403_block_is_retried():
    async with httpx.AsyncClient() as client, respx.mock() as r:
        route = r.get(f"{TG}/v1/accounts/{WALLET}").mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "0"}),
                httpx.Response(403),  # TronGrid's temporary block
                httpx.Response(200, json={"data": [{"balance": 2_000_000}]}),
            ]
        )
        provider = fast(TronProvider(client, RateLimiter(0), TG))
        assert (await provider.get_balances(WALLET))[0].amount == 2
        assert route.call_count == 3


async def test_persistent_429_raises_rate_limit_error():
    async with httpx.AsyncClient() as client, respx.mock() as r:
        r.get(f"{TG}/v1/accounts/{WALLET}").mock(return_value=httpx.Response(429))
        provider = fast(TronProvider(client, RateLimiter(0), TG))
        with pytest.raises(RateLimitError, match="API key"):
            await provider.get_balances(WALLET)


async def test_tron_keeps_pages_already_downloaded():
    page = [trc20(f"tx{i}", WALLET, WALLET[:-1] + "x", 1_000_000, i) for i in range(3)]
    async with httpx.AsyncClient() as client, respx.mock() as r:
        r.get(f"{TG}/v1/accounts/{WALLET}/transactions/trc20", params={"fingerprint": "p2"}).mock(
            return_value=httpx.Response(429)
        )
        r.get(f"{TG}/v1/accounts/{WALLET}/transactions/trc20").mock(
            return_value=httpx.Response(200, json={"data": page, "meta": {"fingerprint": "p2"}})
        )
        r.get(f"{TG}/v1/accounts/{WALLET}/transactions").mock(return_value=httpx.Response(429))
        provider = fast(TronProvider(client, RateLimiter(0), TG, page_size=3))
        result = await provider.get_transfers(WALLET, 100)
        assert len(result.transfers) == 3 and result.truncated


async def test_etherscan_rate_limit_inside_a_200_response_is_retried():
    ok = {"status": "1", "message": "OK", "result": str(10**18)}
    limited = {"status": "0", "message": "NOTOK", "result": "Max rate limit reached"}
    async with httpx.AsyncClient() as client, respx.mock() as r:
        route = r.get(ETHERSCAN).mock(side_effect=[httpx.Response(200, json=limited), httpx.Response(200, json=ok)])
        provider = fast(EvmProvider(Chain.ETHEREUM, client, RateLimiter(0), ETHERSCAN, api_key="K"))
        assert (await provider.get_balances(EVM))[0].amount == 1
        assert route.call_count == 2


async def test_one_download_per_address_at_a_time(tmp_path):
    from app.db import Database

    class SlowProvider(FakeProvider):
        async def get_transfers(self, address, max_items):
            await asyncio.sleep(0.2)
            return await super().get_transfers(address, max_items)

    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'sf.db'}")
    await db.init()
    provider = SlowProvider(SCENARIO)
    wallets = WalletService(db, FakeRegistry(provider), 1000, 3600)
    results = await asyncio.gather(*(wallets.load_transfers(Chain.TRON, SCAMMER) for _ in range(5)))
    assert provider.calls == [SCAMMER]  # fetched once, the others read the cache
    assert all(len(r[0]) == len(results[0][0]) for r in results)
    await db.close()


def test_automatic_rates():
    s = Settings(_env_file=None)
    assert s.tron_rate < 1 and s.evm_rate == 3
    s = Settings(_env_file=None, trongrid_api_key="k", etherscan_api_key="e")
    assert s.tron_rate > 10 and s.evm_rate == 4


def test_update_env_file_keeps_other_lines(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# comment\nPORT=8765\nTRONGRID_API_KEY=\n", encoding="utf-8")
    update_env_file({"TRONGRID_API_KEY": "abc", "ETHERSCAN_API_KEY": "xyz"}, env)
    assert env.read_text(encoding="utf-8") == "# comment\nPORT=8765\nTRONGRID_API_KEY=abc\nETHERSCAN_API_KEY=xyz\n"


def test_settings_api_saves_keys_and_enables_chains(tmp_path):
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 's.db'}", monitor_interval_seconds=0, _env_file=None
    )
    env = tmp_path / ".env"
    env.write_text("PORT=8765\n", encoding="utf-8")
    with TestClient(create_app(settings)) as c:
        c.app.state.services.env_file = env
        before = c.get("/api/settings").json()
        assert before["etherscan_api_key"] is False and before["can_edit"] is True
        assert "bsc" not in c.get("/api/health").json()["chains"]

        after = c.put("/api/settings/keys", json={"etherscan_api_key": " KEY123 ", "trongrid_api_key": "TG"}).json()
        assert after["etherscan_api_key"] and after["trongrid_api_key"]
        assert after["tron_requests_per_second"] > 10
        assert "bsc" in c.get("/api/health").json()["chains"]  # applied without a restart
        text = env.read_text(encoding="utf-8")
        assert "ETHERSCAN_API_KEY=KEY123" in text and "PORT=8765" in text
        assert "KEY123" not in c.get("/api/settings").text  # never echoed back

        assert c.put("/api/settings/keys", json={"solana_rpc_url": "ftp://x"}).status_code == 400


def test_settings_api_refuses_remote_clients(tmp_path, monkeypatch):
    from app.api import settings as settings_api

    monkeypatch.setattr(settings_api, "LOCAL_CLIENTS", {"127.0.0.1"})
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'r.db'}", monitor_interval_seconds=0, _env_file=None
    )
    with TestClient(create_app(settings)) as c:
        assert c.get("/api/settings").json()["can_edit"] is False
        assert c.put("/api/settings/keys", json={"trongrid_api_key": "x"}).status_code == 403
