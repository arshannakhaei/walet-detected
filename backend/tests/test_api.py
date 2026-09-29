"""End-to-end API tests with TronGrid mocked by respx."""

from decimal import Decimal

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.conftest import ALICE, BOB, CAROL, USDT, WALLET

API = "https://api.trongrid.io"


@pytest.fixture
def mock_tron(trc20_items, trx_items):
    with respx.mock(assert_all_called=False) as router:
        # Page 1 of TRC20 returns a fingerprint; page 2 ends the history.
        router.get(f"{API}/v1/accounts/{WALLET}/transactions/trc20", params={"fingerprint": "p2"}).mock(
            return_value=httpx.Response(200, json={"data": trc20_items[2:], "meta": {}})
        )
        trc20_route = router.get(f"{API}/v1/accounts/{WALLET}/transactions/trc20").mock(
            return_value=httpx.Response(200, json={"data": trc20_items[:2], "meta": {"fingerprint": "p2"}})
        )
        router.get(f"{API}/v1/accounts/{WALLET}/transactions").mock(
            return_value=httpx.Response(200, json={"data": trx_items, "meta": {}})
        )
        router.get(f"{API}/v1/accounts/{WALLET}").mock(
            return_value=httpx.Response(
                200,
                json={"data": [{"balance": 40_000_000, "trc20": [{USDT: "50000000"}]}]},
            )
        )
        router.get("https://api.coingecko.com/api/v3/simple/price").mock(
            return_value=httpx.Response(200, json={"tron": {"usd": 0.25}})
        )
        yield trc20_route


@pytest.fixture
def client(tmp_path, mock_tron):
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        tron_requests_per_second=0,
        _env_file=None,
    )
    with TestClient(create_app(settings)) as c:
        yield c


def test_health(client):
    assert client.get("/api/health").json()["status"] == "ok"


def test_detect(client):
    assert client.get(f"/api/detect/{WALLET}").json()["chains"] == [{"chain": "tron", "supported": True}]


def test_overview(client):
    data = client.get(f"/api/wallet/{WALLET}/overview").json()
    assert data["chain"] == "tron"
    assert data["transfer_count"] == 6  # 4 USDT + 2 TRX (one failed)
    assert data["counterparty_count"] == 3
    assert data["truncated"] is False
    balances = {b["token_symbol"]: Decimal(b["amount"]) for b in data["balances"]}
    assert balances == {"TRX": Decimal("40"), "USDT": Decimal("50")}
    assert Decimal(data["total_usd"]) == Decimal("60")  # 40 TRX * 0.25 + 50 USDT
    usdt = next(f for f in data["flows"] if f["token_symbol"] == "USDT")
    assert Decimal(usdt["total_in"]) == 1500 and Decimal(usdt["total_out"]) == 1450
    trx = next(f for f in data["flows"] if f["token_symbol"] == "TRX")
    assert trx["count_out"] == 0  # failed transfer excluded


def test_counterparties(client):
    cps = client.get(f"/api/wallet/{WALLET}/counterparties").json()
    by_addr = {(c["address"], c["token_symbol"]): c for c in cps}
    assert Decimal(by_addr[(ALICE, "USDT")]["received_from"]) == 1500
    assert by_addr[(ALICE, "USDT")]["count_in"] == 2
    assert Decimal(by_addr[(BOB, "USDT")]["sent_to"]) == 1200
    assert Decimal(by_addr[(CAROL, "TRX")]["received_from"]) == 50
    # Largest total first.
    assert cps[0]["address"] == ALICE

    outgoing = client.get(f"/api/wallet/{WALLET}/counterparties", params={"direction": "out"}).json()
    assert {c["address"] for c in outgoing} == {BOB, CAROL}


def test_transfers_filters(client):
    data = client.get(
        f"/api/wallet/{WALLET}/transfers", params={"token": "usdt", "min_amount": "600"}
    ).json()
    assert data["total"] == 2
    assert {i["counterparty"] for i in data["items"]} == {ALICE, BOB}
    assert data["items"][0]["direction"] == "out"  # newest first

    with_failed = client.get(
        f"/api/wallet/{WALLET}/transfers", params={"token": "TRX", "include_failed": "true"}
    ).json()
    assert with_failed["total"] == 2


def test_cache_prevents_refetch(client, mock_tron):
    client.get(f"/api/wallet/{WALLET}/overview")
    calls = mock_tron.call_count
    client.get(f"/api/wallet/{WALLET}/counterparties")
    assert mock_tron.call_count == calls
    client.post(f"/api/wallet/{WALLET}/refresh")
    assert mock_tron.call_count > calls


def test_invalid_address(client):
    assert client.get("/api/wallet/nope/overview").status_code == 400
    evm = "0xdAC17F958D2ee523a2206206994597C13D831ec7"
    # BSC needs an Etherscan key (no public Blockscout), and none is configured.
    assert client.get(f"/api/wallet/{evm}/overview", params={"chain": "bsc"}).status_code == 501
    assert client.get(f"/api/wallet/{WALLET}/overview", params={"chain": "ethereum"}).status_code == 400


def test_trace_endpoint(client):
    body = {"address": WALLET, "tx_hash": "tx1", "max_hops": 1}
    data = client.post("/api/trace", json=body).json()
    assert Decimal(data["traced_amount"]) == 1000
    # FIFO: the first 1000 USDT in leave with the 1200 USDT transfer to Bob.
    assert data["endpoints"][0]["address"] == BOB
    assert data["endpoints"][0]["reason"] == "max_hops"
    assert Decimal(data["endpoints"][0]["amount"]) == 1000


def test_trace_unknown_tx(client):
    resp = client.post("/api/trace", json={"address": WALLET, "tx_hash": "nope"})
    assert resp.status_code == 404


def test_graph_endpoint_root_only(client):
    data = client.get(f"/api/graph/{WALLET}", params={"depth_in": 0, "depth_out": 1, "token": "USDT"}).json()
    assert {n["address"] for n in data["nodes"]} == {WALLET, BOB, CAROL}
    assert all(n["stop_reason"] == "max_depth" for n in data["nodes"] if n["address"] != WALLET)


def test_labels_crud(client):
    assert client.put(f"/api/labels/tron/{BOB}", json={"name": "Bob", "category": "personal"}).status_code == 200
    labels = {l["address"]: l for l in client.get("/api/labels", params={"chain": "tron"}).json()}
    assert labels[BOB]["source"] == "user"
    assert labels[USDT]["category"] == "token_contract"  # built-in list
    assert client.put(f"/api/labels/tron/0xabc", json={"name": "x", "category": "other"}).status_code == 400
    assert client.delete(f"/api/labels/tron/{BOB}").status_code == 204
    assert client.delete(f"/api/labels/tron/{BOB}").status_code == 404
