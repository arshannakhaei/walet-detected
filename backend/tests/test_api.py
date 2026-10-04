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
        # Tether freeze list: nothing frozen.
        router.post(f"{API}/wallet/triggerconstantcontract").respond(json={"constant_result": ["0" * 64]})
        # Rate sources are unreachable in tests: values fall back to the manual rate or none.
        for host in ("api.nobitex.ir", "api.wallex.ir", "api.binance.com"):
            router.route(host=host).respond(503)
        router.get(url__regex=r"https://api\.coingecko\.com/api/v3/coins/.*").respond(503)
        yield trc20_route


@pytest.fixture
def client(tmp_path, mock_tron):
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        tron_requests_per_second=0,
        monitor_interval_seconds=0,
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


def test_risk_and_timeline(client):
    report = client.get(f"/api/wallet/{WALLET}/risk").json()
    assert 0 <= report["score"] <= 100 and report["stats"]["main_token"] == "USDT"
    rows = client.get(f"/api/wallet/{WALLET}/timeline").json()
    assert rows and rows[0]["period"] == "2023-11-14"


def test_csv_exports(client):
    resp = client.get(f"/api/wallet/{WALLET}/transfers.csv")
    assert resp.status_code == 200 and "text/csv" in resp.headers["content-type"]
    lines = resp.text.lstrip("﻿").strip().splitlines()
    assert lines[0].startswith("time_utc,tx_hash") and len(lines) == 6  # header + 5 successful
    cps = client.get(f"/api/wallet/{WALLET}/counterparties.csv").text
    assert ALICE in cps


def test_csv_and_report_include_dollar_and_toman(client, tmp_path):
    client.app.state.services.env_file = tmp_path / ".env"
    client.put("/api/settings/keys", json={"usd_toman_rate": "60000"})
    lines = client.get(f"/api/wallet/{WALLET}/transfers.csv").text.lstrip("\ufeff").strip().splitlines()
    assert lines[0].endswith("usd_then,toman_then,usd_now,toman_now")
    row = next(line for line in lines if "tx1" in line).split(",")  # 1000 USDT in from Alice
    assert row[-4:] == ["1000.00", "60000000.00", "1000.00", "60000000.00"]
    trx_row = next(line for line in lines if "tx5" in line).split(",")  # 50 TRX: no history in tests
    assert trx_row[-4] == "" and trx_row[-2] == "12.50"
    cps = client.get(f"/api/wallet/{WALLET}/counterparties.csv").text
    assert "received_toman_now" in cps
    html = client.get(f"/api/wallet/{WALLET}/report").text
    assert "تومان" in html and "60,000" in html


def test_wallet_report_html(client):
    html = client.get(f"/api/wallet/{WALLET}/report").text
    assert "dir='rtl'" in html and WALLET in html


def test_cases_flow(client):
    case = client.post("/api/cases", json={"title": "Scam #1", "description": "victim report"}).json()
    cid = case["id"]
    item = client.post(f"/api/cases/{cid}/items", json={"kind": "address", "address": WALLET, "note": "suspect"}).json()
    assert item["chain"] == "tron"
    trace = {"address": WALLET, "tx_hash": "tx1", "max_hops": 1}
    traced = client.post(f"/api/cases/{cid}/items", json={"kind": "trace", "trace": trace, "title": "t1"}).json()
    assert Decimal(traced["data"]["traced_amount"]) == 1000
    client.post(f"/api/cases/{cid}/items", json={"kind": "note", "title": "call", "note": "victim called"})

    full = client.get(f"/api/cases/{cid}").json()
    assert [i["kind"] for i in full["items"]] == ["address", "trace", "note"]
    assert client.get("/api/cases").json()[0]["item_count"] == 3

    html = client.get(f"/api/cases/{cid}/report", params={"lang": "en"}).text
    assert "Scam #1" in html and "victim called" in html and BOB in html

    assert client.patch(f"/api/cases/{cid}", json={"status": "closed"}).json()["status"] == "closed"
    assert client.delete(f"/api/cases/{cid}/items/{item['id']}").status_code == 204
    assert client.delete(f"/api/cases/{cid}").status_code == 204
    assert client.get(f"/api/cases/{cid}").status_code == 404


def test_watchlist_and_alerts_api(client):
    watch = client.post("/api/watchlist", json={"address": WALLET, "name": "w", "min_amount": "5"}).json()
    assert watch["chain"] == "tron" and watch["name"] == "w"
    assert len(client.get("/api/watchlist").json()) == 1
    assert client.post("/api/watchlist/check").json() == []  # nothing new since it was added
    assert client.get("/api/alerts").json() == []
    assert client.post("/api/alerts/read", json={}).status_code == 204
    assert client.delete(f"/api/watchlist/{watch['id']}").status_code == 204
    assert client.delete(f"/api/watchlist/{watch['id']}").status_code == 404


def test_graph_png(client):
    resp = client.get(f"/api/graph/{WALLET}/image.png", params={"depth_in": 0, "depth_out": 1})
    assert resp.status_code == 200 and resp.content.startswith(b"\x89PNG")


def test_demo_mode_story(tmp_path):
    from app.providers import demo

    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'demo.db'}",
        demo_mode=True,
        monitor_interval_seconds=0,
        _env_file=None,
    )
    with respx.mock(assert_all_called=False) as router:
        router.get("https://api.coingecko.com/api/v3/simple/price").mock(return_value=httpx.Response(200, json={}))
        with TestClient(create_app(settings)) as c:
            health = c.get("/api/health").json()
            assert health["demo"]["scammer"] == demo.SCAMMER
            risk = c.get(f"/api/wallet/{demo.SCAMMER}/risk").json()
            assert {"pass_through", "fan_in"} <= {f["code"] for f in risk["findings"]}
            mule_c = c.get(f"/api/wallet/{demo.MULES[2]}/risk").json()
            assert "direct_exposure" in {f["code"] for f in mule_c["findings"]}  # paid an OFAC address
            first_payment = c.get(f"/api/wallet/{demo.VICTIMS[0]}/transfers", params={"direction": "out"}).json()
            tx = first_payment["items"][0]["transfer"]["tx_hash"]
            trace = c.post("/api/trace", json={"address": demo.VICTIMS[0], "tx_hash": tx}).json()
            assert Decimal(trace["summary"]["labeled"]) > 0  # reaches the exchange hot wallet
