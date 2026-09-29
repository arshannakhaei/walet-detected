"""Whole-stack checks: every chain through the API, retries, migrations, escaping."""

import sqlite3
import time
from decimal import Decimal

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import Database
from app.main import create_app
from app.models import Chain
from app.providers.base import ProviderError
from app.providers.ratelimit import RateLimiter
from app.providers.tron import TronProvider
from app.services import report
from app.services.monitor import MonitorService
from tests.conftest import WALLET, trc20

EVM = "0x" + "ab" * 20
EVM_OTHER = "0x" + "cd" * 20
BLOCKSCOUT = "https://eth.blockscout.com/api"
MEMPOOL = "https://mempool.space/api"
BTC = "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"
BTC_OTHER = "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"
SOLANA_RPC = "https://api.mainnet-beta.solana.com"
SOL = "So11111111111111111111111111111111111111112"


def _settings(tmp_path, **kw) -> Settings:
    return Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'i.db'}",
        tron_requests_per_second=0,
        evm_requests_per_second=0,
        bitcoin_requests_per_second=0,
        solana_requests_per_second=0,
        monitor_interval_seconds=0,
        _env_file=None,
        **kw,
    )


@pytest.fixture
def router():
    with respx.mock(assert_all_called=False) as r:
        r.get("https://api.coingecko.com/api/v3/simple/price").mock(
            return_value=httpx.Response(200, json={"ethereum": {"usd": 3000}, "bitcoin": {"usd": 60000}, "solana": {"usd": 150}})
        )
        yield r


def test_ethereum_via_blockscout_without_key(tmp_path, router):
    def respond(request):
        action = request.url.params["action"]
        assert "apikey" not in request.url.params and "chainid" not in request.url.params
        if action == "txlist":
            items = [{"hash": "0x1", "timeStamp": "1700000000", "from": EVM_OTHER, "to": EVM, "value": str(2 * 10**18), "isError": "0"}]
        elif action == "tokentx":
            items = [{
                "hash": "0x2", "timeStamp": "1700000100", "from": EVM, "to": EVM_OTHER, "value": "5000000",
                "contractAddress": "0xdac17f958d2ee523a2206206994597c13d831ec7", "tokenSymbol": "USDT",
                "tokenDecimal": "6", "logIndex": "3",
            }, {
                "hash": "0x3", "timeStamp": "1700000200", "from": EVM_OTHER, "to": EVM, "value": "1000000000",
                "contractAddress": "0x" + "99" * 20, "tokenSymbol": "USDT", "tokenDecimal": "6", "logIndex": "1",
            }]
        elif action == "balance":
            return httpx.Response(200, json={"status": "1", "message": "OK", "result": str(10**18)})
        else:
            return httpx.Response(200, json={"status": "0", "message": "No transactions found", "result": []})
        return httpx.Response(200, json={"status": "1", "message": "OK", "result": items})

    router.get(BLOCKSCOUT).mock(side_effect=respond)
    with TestClient(create_app(_settings(tmp_path))) as c:
        # Mixed-case input resolves to the lower-case canonical address.
        o = c.get(f"/api/wallet/{EVM.upper().replace('0X', '0x')}/overview").json()
        assert o["chain"] == "ethereum" and o["address"] == EVM
        assert Decimal(o["total_usd"]) == 3000  # 1 ETH; fake USDT is not priced
        flows = {f["token_symbol"]: f for f in o["flows"]}
        assert Decimal(flows["ETH"]["total_in"]) == 2
        assert Decimal(flows["USDT"]["total_out"]) == 5  # the fake 1000 "USDT" is hidden as spam
        spam = c.get(f"/api/wallet/{EVM}/transfers", params={"hide_spam": "false"}).json()
        assert spam["total"] == 3
        assert c.get(f"/api/wallet/{EVM}/overview", params={"chain": "bsc"}).status_code == 501


def test_bitcoin_via_api(tmp_path, router):
    tx = {
        "txid": "b1",
        "status": {"confirmed": True, "block_time": 1_700_000_000},
        "vin": [{"prevout": {"scriptpubkey_address": BTC_OTHER, "value": 50_000_000}}],
        "vout": [{"scriptpubkey_address": BTC, "value": 40_000_000}, {"scriptpubkey_address": BTC_OTHER, "value": 9_990_000}],
    }
    router.get(f"{MEMPOOL}/address/{BTC}/txs/mempool").mock(return_value=httpx.Response(200, json=[]))
    router.get(f"{MEMPOOL}/address/{BTC}/txs/chain").mock(return_value=httpx.Response(200, json=[tx]))
    router.get(f"{MEMPOOL}/address/{BTC}").mock(
        return_value=httpx.Response(200, json={"chain_stats": {"funded_txo_sum": 40_000_000, "spent_txo_sum": 0}})
    )
    with TestClient(create_app(_settings(tmp_path))) as c:
        o = c.get(f"/api/wallet/{BTC}/overview").json()
        assert o["chain"] == "bitcoin" and Decimal(o["balances"][0]["amount"]) == Decimal("0.4")
        assert Decimal(o["total_usd"]) == 24000
        cps = c.get(f"/api/wallet/{BTC}/counterparties").json()
        assert cps[0]["address"] == BTC_OTHER and Decimal(cps[0]["received_from"]) == Decimal("0.4")


def test_solana_via_api(tmp_path, router):
    def respond(request):
        body = __import__("json").loads(request.content)
        if body["method"] == "getSignaturesForAddress":
            return httpx.Response(200, json={"result": [{"signature": "sig"}]})
        if body["method"] == "getTransaction":
            return httpx.Response(200, json={"result": {
                "blockTime": 1_700_000_000,
                "meta": {"err": None},
                "transaction": {"message": {"accountKeys": [], "instructions": [
                    {"program": "system", "parsed": {"type": "transfer", "info": {
                        "source": SOL, "destination": "Other1111111111111111111111111111111111111", "lamports": 250_000_000}}}
                ]}},
            }})
        if body["method"] == "getBalance":
            return httpx.Response(200, json={"result": {"value": 10**9}})
        return httpx.Response(200, json={"result": {"value": []}})

    router.post(SOLANA_RPC).mock(side_effect=respond)
    with TestClient(create_app(_settings(tmp_path))) as c:
        o = c.get(f"/api/wallet/{SOL}/overview").json()
        assert o["chain"] == "solana" and Decimal(o["flows"][0]["total_out"]) == Decimal("0.25")
        assert Decimal(o["total_usd"]) == 150


def test_prices_failing_does_not_break_overview(tmp_path):
    with respx.mock(assert_all_called=False) as r:
        r.get("https://api.coingecko.com/api/v3/simple/price").mock(return_value=httpx.Response(429))
        r.get(f"https://api.trongrid.io/v1/accounts/{WALLET}/transactions/trc20").mock(
            return_value=httpx.Response(200, json={"data": [], "meta": {}})
        )
        r.get(f"https://api.trongrid.io/v1/accounts/{WALLET}/transactions").mock(
            return_value=httpx.Response(200, json={"data": [], "meta": {}})
        )
        r.get(f"https://api.trongrid.io/v1/accounts/{WALLET}").mock(
            return_value=httpx.Response(200, json={"data": [{"balance": 5_000_000}]})
        )
        with TestClient(create_app(_settings(tmp_path))) as c:
            o = c.get(f"/api/wallet/{WALLET}/overview").json()
            assert o["total_usd"] is None and Decimal(o["balances"][0]["amount"]) == 5


def test_invalid_inputs(tmp_path, router):
    with TestClient(create_app(_settings(tmp_path))) as c:
        assert c.get(f"/api/wallet/{WALLET}/overview", params={"chain": "dogecoin"}).status_code == 422
        assert c.get(f"/api/graph/{WALLET}", params={"depth_in": 9}).status_code == 422
        assert c.post("/api/trace", json={"address": WALLET}).status_code == 422
        assert c.post("/api/cases", json={"title": ""}).status_code == 422
        assert c.put(f"/api/labels/tron/{WALLET}", json={"name": "x", "category": "nope"}).status_code == 422
        assert c.get("/api/cases/999").status_code == 404


# --- providers ------------------------------------------------------------


async def test_retries_on_429_then_succeeds_and_does_not_retry_400():
    url = "https://api.trongrid.io"
    async with httpx.AsyncClient() as client, respx.mock() as r:
        route = r.get(f"{url}/v1/accounts/{WALLET}").mock(
            side_effect=[httpx.Response(429), httpx.Response(200, json={"data": [{"balance": 1_000_000}]})]
        )
        provider = TronProvider(client, RateLimiter(0), url)
        balances = await provider.get_balances(WALLET)
        assert balances[0].amount == 1 and route.call_count == 2

        bad = r.get(f"{url}/v1/accounts/bad").mock(return_value=httpx.Response(400))
        with pytest.raises(ProviderError):
            await provider.get_balances("bad")
        assert bad.call_count == 1


async def test_tron_pagination_stops_at_limit():
    url = "https://api.trongrid.io"
    page = [trc20(f"tx{i}", WALLET, WALLET[:-1] + "x", 1, i) for i in range(3)]
    async with httpx.AsyncClient() as client, respx.mock() as r:
        trc = r.get(f"{url}/v1/accounts/{WALLET}/transactions/trc20").mock(
            return_value=httpx.Response(200, json={"data": page, "meta": {"fingerprint": "more"}})
        )
        r.get(f"{url}/v1/accounts/{WALLET}/transactions").mock(return_value=httpx.Response(200, json={"data": [], "meta": {}}))
        provider = TronProvider(client, RateLimiter(0), url, page_size=3)
        result = await provider.get_transfers(WALLET, 6)
        assert result.truncated and len(result.transfers) == 6 and trc.call_count == 2


async def test_rate_limiter_spaces_calls():
    limiter = RateLimiter(20)  # 50 ms apart
    start = time.monotonic()
    for _ in range(5):
        await limiter.wait()
    assert time.monotonic() - start >= 0.19


# --- storage --------------------------------------------------------------


async def test_migrates_database_from_first_version(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE address_sync (chain VARCHAR(16), address VARCHAR(100), synced_at DATETIME, truncated BOOLEAN,"
        " PRIMARY KEY (chain, address))"
    )
    conn.execute("INSERT INTO address_sync VALUES ('tron', 'T1', '2024-01-01 00:00:00', 0)")
    conn.commit()
    conn.close()
    db = Database(f"sqlite+aiosqlite:///{path}")
    await db.init()
    sync = await db.get_sync(Chain.TRON, "T1")
    assert sync.fetched_limit == 0
    await db.close()


# --- reports and monitor --------------------------------------------------


def test_report_escapes_user_text():
    html = report.trace_section(
        "en",
        "<script>alert(1)</script>",
        {"start": {"tx_hash": "<b>"}, "endpoints": [{"address": "x", "reason": "unspent", "amount": "1",
                                                    "confidence": "1", "label": {"name": "<img src=x onerror=1>"}}]},
    )
    assert "<script>" not in html and "<img" not in html and "&lt;script&gt;" in html


class _FailingWallets:
    async def load_transfers(self, *a, **kw):
        raise ProviderError("tron: down")


async def test_monitor_survives_provider_errors(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'm.db'}")
    await db.init()
    from app.db import WatchRow

    async with db.sessions.begin() as s:
        s.add(WatchRow(chain="tron", address=WALLET, name="w"))
    monitor = MonitorService(db, _FailingWallets())
    assert await monitor.check_all() == []
    await db.close()
