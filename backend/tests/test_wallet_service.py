"""WalletService details not covered by the API tests."""

from decimal import Decimal

from app.models import Chain
from tests.test_tracing import M1, M2, SCAMMER, env, usdt  # noqa: F401


async def test_token_balances_derived_when_provider_reports_native_only(env):
    # EVM explorers only return the native balance; token balances come from history.
    wallets, _, provider = await env([usdt("a", SCAMMER, M1, 500, 0), usdt("b", M1, M2, 120, 5)])
    provider.reports_token_balances = False
    overview = await wallets.overview(Chain.TRON, M1)
    assert [(b.token_symbol, b.amount, b.derived) for b in overview.balances] == [("USDT", Decimal(380), True)]
    # Nothing left: no derived balance line.
    assert (await wallets.overview(Chain.TRON, M2)).balances[0].amount == Decimal(120)
    assert (await wallets.overview(Chain.TRON, SCAMMER)).balances == []
