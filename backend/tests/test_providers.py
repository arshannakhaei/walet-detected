"""Parsers and HTTP flows of the EVM, Bitcoin and Solana providers (APIs mocked)."""

from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest
import respx

from app.models import Chain, Transfer
from app.providers.base import ProviderError
from app.providers.bitcoin import BitcoinProvider, parse_btc_tx
from app.providers.evm import EvmProvider, parse_internal, parse_native, parse_tokens
from app.providers.ratelimit import RateLimiter
from app.providers.solana import SolanaProvider, parse_sol_tx
from app.services.tokens import is_spam

A = "0x" + "a" * 40
B = "0x" + "b" * 40
C = "0x" + "c" * 40
USDT_ETH = "0xdac17f958d2ee523a2206206994597c13d831ec7"
ETHERSCAN = "https://api.etherscan.io/v2/api"


def _eth_tx(h, frm, to, wei, ts=1_700_000_000, err="0"):
    return {"hash": h, "timeStamp": str(ts), "from": frm, "to": to, "value": str(wei), "isError": err}


def _token_tx(h, frm, to, value, contract=USDT_ETH, symbol="USDT", decimals="6", log="1"):
    return {
        "hash": h, "timeStamp": "1700000100", "from": frm, "to": to, "value": str(value),
        "contractAddress": contract, "tokenSymbol": symbol, "tokenDecimal": decimals, "logIndex": log,
    }


# --- EVM --------------------------------------------------------------------


def test_evm_parsers():
    native = parse_native(
        [
            _eth_tx("0x1", A.upper().replace("0X", "0x"), B, 10**18),
            _eth_tx("0x2", A, B, 0),  # plain contract call
            _eth_tx("0x3", A, "", 5),  # contract creation
            _eth_tx("0x4", A, B, 2 * 10**18, err="1"),
        ],
        Chain.ETHEREUM,
        "ETH",
    )
    assert [t.tx_hash for t in native] == ["0x1", "0x4"]
    assert native[0].from_address == A  # lower-cased
    assert native[0].amount == 1 and native[1].success is False

    internal = parse_internal([{**_eth_tx("0x5", C, A, 3 * 10**17), "traceId": "0_1"}], Chain.ETHEREUM, "ETH")
    assert internal[0].amount == Decimal("0.3") and "internal" in internal[0].transfer_id

    tokens = parse_tokens([_token_tx("0x6", A, B, 2_500_000)], Chain.ETHEREUM)
    assert tokens[0].amount == Decimal("2.5") and tokens[0].token_contract == USDT_ETH
    assert tokens[0].transfer_id == "0x6:log:1"


@pytest.mark.parametrize("key", ["", "KEY"])
async def test_evm_provider_pagination_and_chain_id(key):
    async with httpx.AsyncClient() as client, respx.mock() as router:
        def respond(request):
            params = request.url.params
            assert params["chainid"] == "56"
            assert params.get("apikey", "") == key
            if params["action"] == "txlist":
                page = int(params["page"])
                items = [_eth_tx(f"0x{page}{i}", A, B, 10**18) for i in range(2 if page == 1 else 1)]
                return httpx.Response(200, json={"status": "1", "message": "OK", "result": items})
            if params["action"] == "balance":
                return httpx.Response(200, json={"status": "1", "message": "OK", "result": str(3 * 10**18)})
            return httpx.Response(200, json={"status": "0", "message": "No transactions found", "result": []})

        router.get(ETHERSCAN).mock(side_effect=respond)
        provider = EvmProvider(Chain.BSC, client, RateLimiter(0), ETHERSCAN, api_key=key, page_size=2)
        page = await provider.get_transfers(A, 100)
        assert len(page.transfers) == 3 and not page.truncated
        assert page.transfers[0].token_symbol == "BNB"
        balances = await provider.get_balances(A)
        assert balances[0].amount == 3 and balances[0].token_symbol == "BNB"


async def test_evm_provider_truncates_and_reports_errors():
    async with httpx.AsyncClient() as client, respx.mock() as router:
        full_page = {"status": "1", "message": "OK", "result": [_eth_tx(f"0x{i}", A, B, 1) for i in range(2)]}
        router.get(ETHERSCAN, params={"action": "txlist"}).mock(return_value=httpx.Response(200, json=full_page))
        router.get(ETHERSCAN, params={"action": "tokentx"}).mock(
            return_value=httpx.Response(200, json={"status": "0", "message": "NOTOK", "result": "Invalid API Key"})
        )
        provider = EvmProvider(Chain.ETHEREUM, client, RateLimiter(0), ETHERSCAN, page_size=2)
        with pytest.raises(ProviderError, match="Invalid API Key"):
            await provider.get_transfers(A, 2)


# --- Bitcoin ----------------------------------------------------------------

BTC_A, BTC_B, BTC_C, BTC_D = "bc1qaaa", "bc1qbbb", "bc1qccc", "bc1qddd"


def _btc_tx(txid, inputs, outputs, t=1_700_000_000):
    return {
        "txid": txid,
        "status": {"confirmed": True, "block_time": t},
        "vin": [{"prevout": {"scriptpubkey_address": a, "value": v}} for a, v in inputs],
        "vout": [{"scriptpubkey_address": a, "value": v} for a, v in outputs],
    }


def test_btc_send_excludes_change():
    tx = _btc_tx("t1", [(BTC_A, 100_000_000)], [(BTC_B, 60_000_000), (BTC_A, 39_990_000)])
    [t] = parse_btc_tx(tx, BTC_A)
    assert (t.from_address, t.to_address, t.amount) == (BTC_A, BTC_B, Decimal("0.6"))


def test_btc_receive_splits_by_input_share():
    tx = _btc_tx("t2", [(BTC_A, 30_000_000), (BTC_C, 10_000_000)], [(BTC_B, 20_000_000), (BTC_D, 19_000_000)])
    transfers = {t.from_address: t.amount for t in parse_btc_tx(tx, BTC_B)}
    assert transfers == {BTC_A: Decimal("0.15"), BTC_C: Decimal("0.05")}


def test_btc_co_spend_scales_by_share():
    tx = _btc_tx("t3", [(BTC_A, 30_000_000), (BTC_C, 10_000_000)], [(BTC_B, 40_000_000)])
    [t] = parse_btc_tx(tx, BTC_A)
    assert t.amount == Decimal("0.3")


async def test_btc_provider_paginates():
    base = "https://mempool.space/api"
    page1 = [_btc_tx(f"p1-{i}", [(BTC_C, 1000)], [(BTC_A, 900)]) for i in range(25)]
    page2 = [_btc_tx("p2-0", [(BTC_A, 5000)], [(BTC_B, 4000), (BTC_A, 900)])]
    async with httpx.AsyncClient() as client, respx.mock() as router:
        router.get(f"{base}/address/{BTC_A}/txs/mempool").mock(return_value=httpx.Response(200, json=[]))
        router.get(f"{base}/address/{BTC_A}/txs/chain").mock(return_value=httpx.Response(200, json=page1))
        router.get(f"{base}/address/{BTC_A}/txs/chain/p1-24").mock(return_value=httpx.Response(200, json=page2))
        router.get(f"{base}/address/{BTC_A}").mock(
            return_value=httpx.Response(
                200,
                json={"chain_stats": {"funded_txo_sum": 150_000_000, "spent_txo_sum": 50_000_000},
                      "mempool_stats": {"funded_txo_sum": 0, "spent_txo_sum": 0}},
            )
        )
        provider = BitcoinProvider(client, RateLimiter(0), base)
        page = await provider.get_transfers(BTC_A, 1000)
        assert len(page.transfers) == 26 and not page.truncated
        assert (await provider.get_balances(BTC_A))[0].amount == 1


# --- Solana -----------------------------------------------------------------

SOL_A, SOL_B = "SoLAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", "SoLBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
TA_A, TA_B = "TokAccAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", "TokAccBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
USDC_SOL = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


def _sol_tx():
    return {
        "blockTime": 1_700_000_000,
        "meta": {
            "err": None,
            "preTokenBalances": [
                {"accountIndex": 2, "mint": USDC_SOL, "owner": SOL_A, "uiTokenAmount": {"decimals": 6}},
            ],
            "postTokenBalances": [
                {"accountIndex": 3, "mint": USDC_SOL, "owner": SOL_B, "uiTokenAmount": {"decimals": 6}},
            ],
            "innerInstructions": [
                {"instructions": [
                    {"program": "spl-token", "parsed": {"type": "transfer", "info": {
                        "source": TA_A, "destination": TA_B, "amount": "7000000", "authority": SOL_A}}}
                ]}
            ],
        },
        "transaction": {
            "message": {
                "accountKeys": [{"pubkey": SOL_A}, {"pubkey": SOL_B}, {"pubkey": TA_A}, {"pubkey": TA_B}],
                "instructions": [
                    {"program": "system", "parsed": {"type": "transfer", "info": {
                        "source": SOL_A, "destination": SOL_B, "lamports": 1_500_000_000}}},
                    {"program": "spl-token", "parsed": {"type": "transferChecked", "info": {
                        "source": TA_A, "destination": TA_B, "mint": USDC_SOL,
                        "tokenAmount": {"amount": "2500000", "decimals": 6}}}},
                ],
            }
        },
    }


def test_solana_parser_maps_token_accounts_to_owners():
    transfers = parse_sol_tx(_sol_tx(), "sig1")
    sol, checked, inner = transfers
    assert (sol.token_symbol, sol.amount) == ("SOL", Decimal("1.5"))
    assert (checked.from_address, checked.to_address) == (SOL_A, SOL_B)
    assert (checked.token_symbol, checked.amount) == ("USDC", Decimal("2.5"))
    assert inner.amount == Decimal("7") and inner.token_contract == USDC_SOL
    assert len({t.transfer_id for t in transfers}) == 3


async def test_solana_provider_flow():
    url = "https://rpc.example"

    def respond(request):
        body = __import__("json").loads(request.content)
        method = body["method"]
        if method == "getSignaturesForAddress":
            return httpx.Response(200, json={"result": [{"signature": "sig1"}]})
        if method == "getTransaction":
            return httpx.Response(200, json={"result": _sol_tx()})
        if method == "getBalance":
            return httpx.Response(200, json={"result": {"value": 2_000_000_000}})
        if method == "getTokenAccountsByOwner":
            accounts = [{"account": {"data": {"parsed": {"info": {
                "mint": USDC_SOL, "tokenAmount": {"uiAmountString": "12.5"}}}}}}]
            is_legacy = body["params"][1]["programId"].startswith("Tokenkeg")
            return httpx.Response(200, json={"result": {"value": accounts if is_legacy else []}})
        return httpx.Response(200, json={"error": {"message": "unknown"}})

    async with httpx.AsyncClient() as client, respx.mock() as router:
        router.post(url).mock(side_effect=respond)
        provider = SolanaProvider(client, RateLimiter(0), url)
        page = await provider.get_transfers(SOL_A, 50)
        assert len(page.transfers) == 3
        balances = {b.token_symbol: b.amount for b in await provider.get_balances(SOL_A)}
        assert balances == {"SOL": Decimal(2), "USDC": Decimal("12.5")}


# --- spam -------------------------------------------------------------------


def _t(symbol, contract, amount=1):
    return Transfer(
        chain=Chain.ETHEREUM, transfer_id="x", tx_hash="x", timestamp=datetime.now(timezone.utc),
        from_address=A, to_address=B, amount=Decimal(amount), token_symbol=symbol,
        token_contract=contract, token_decimals=6,
    )


def test_spam_detection():
    assert not is_spam(_t("USDT", USDT_ETH))
    assert is_spam(_t("USDT", "0x" + "9" * 40))  # fake USDT
    assert is_spam(_t("USDT", USDT_ETH, amount=0))  # zero-value poisoning
    assert not is_spam(_t("PEPE", "0x" + "9" * 40))  # unknown but not impersonating
    assert not is_spam(_t("ETH", None))
