"""Shared fixtures: deterministic Tron addresses and canned TronGrid responses."""

import pytest

from app.services.addresses import tron_base58_to_hex, tron_hex_to_base58

USDT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
BASE_TS = 1_700_000_000_000  # ms


def make_address(n: int) -> str:
    return tron_hex_to_base58("41" + f"{n:040x}")


WALLET = make_address(1)
ALICE = make_address(2)
BOB = make_address(3)
CAROL = make_address(4)


def trc20(tx: str, frm: str, to: str, value: int, minutes: int) -> dict:
    return {
        "transaction_id": tx,
        "token_info": {"symbol": "USDT", "address": USDT, "decimals": 6, "name": "Tether USD"},
        "block_timestamp": BASE_TS + minutes * 60_000,
        "from": frm,
        "to": to,
        "type": "Transfer",
        "value": str(value),
    }


def trx(tx: str, frm: str, to: str, sun: int, minutes: int, result: str = "SUCCESS") -> dict:
    return {
        "txID": tx,
        "block_timestamp": BASE_TS + minutes * 60_000,
        "ret": [{"contractRet": result}],
        "raw_data": {
            "contract": [
                {
                    "type": "TransferContract",
                    "parameter": {
                        "value": {
                            "amount": sun,
                            "owner_address": tron_base58_to_hex(frm),
                            "to_address": tron_base58_to_hex(to),
                        }
                    },
                }
            ]
        },
    }


@pytest.fixture
def trc20_items() -> list[dict]:
    return [
        trc20("tx1", ALICE, WALLET, 1_000_000_000, 0),  # 1000 USDT in from Alice
        trc20("tx2", ALICE, WALLET, 500_000_000, 10),  # 500 USDT in from Alice
        trc20("tx3", WALLET, BOB, 1_200_000_000, 20),  # 1200 USDT out to Bob
        trc20("tx4", WALLET, CAROL, 250_000_000, 30),  # 250 USDT out to Carol
    ]


@pytest.fixture
def trx_items() -> list[dict]:
    return [
        trx("tx5", CAROL, WALLET, 50_000_000, 5),  # 50 TRX in from Carol
        trx("tx6", WALLET, BOB, 10_000_000, 25, result="REVERT"),  # failed
        # A non-transfer contract must be ignored.
        {"txID": "tx7", "block_timestamp": BASE_TS, "raw_data": {"contract": [{"type": "FreezeBalanceV2Contract"}]}},
    ]
