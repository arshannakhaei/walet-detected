from decimal import Decimal

from app.providers.tron import parse_trc20_transfers, parse_trx_transfers
from tests.conftest import ALICE, BOB, USDT, WALLET, trc20


def test_parse_trc20(trc20_items):
    transfers = parse_trc20_transfers(trc20_items)
    assert len(transfers) == 4
    first = transfers[0]
    assert first.from_address == ALICE and first.to_address == WALLET
    assert first.amount == Decimal("1000")
    assert first.token_symbol == "USDT" and first.token_contract == USDT


def test_parse_trc20_duplicate_transfers_get_unique_ids():
    dup = trc20("txd", WALLET, BOB, 1, 0)
    transfers = parse_trc20_transfers([dup, dict(dup)])
    assert len({t.transfer_id for t in transfers}) == 2


def test_parse_trx(trx_items):
    transfers = parse_trx_transfers(trx_items)
    assert len(transfers) == 2  # freeze contract skipped
    incoming, failed = transfers
    assert incoming.token_symbol == "TRX" and incoming.amount == Decimal("50")
    assert incoming.to_address == WALLET
    assert failed.success is False
