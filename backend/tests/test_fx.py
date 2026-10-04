"""Dollar and toman values: live rate fallbacks, daily history, caching."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest
import respx

from app.db import Database
from app.models import Chain
from app.services.fx import ValueService
from app.services.pricing import PriceService
from tests.conftest import USDT
from tests.test_api import client, mock_tron  # noqa: F401

NOBITEX = "https://nobitex.test"
WALLEX = "https://wallex.test"
BINANCE = "https://binance.test"
COINGECKO = "https://coingecko.test/api/v3"
TODAY = datetime.now(timezone.utc).date()


def ts(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())


@pytest.fixture
async def make(tmp_path):
    made = []

    async def build(manual=None):
        db = Database(f"sqlite+aiosqlite:///{tmp_path / 'fx.db'}")
        await db.init()
        client = httpx.AsyncClient()
        prices = PriceService(client, COINGECKO)
        svc = ValueService(client, db, prices, manual, nobitex_url=NOBITEX, wallex_url=WALLEX, binance_url=BINANCE)
        made.append((db, client))
        return svc

    yield build
    for db, client in made:
        await client.aclose()
        await db.close()


def nobitex_stats(r, rial="950000"):
    return r.get(f"{NOBITEX}/market/stats").respond(json={"status": "ok", "stats": {"usdt-rls": {"latest": rial}}})


async def test_toman_now_from_nobitex_in_toman(make):
    svc = await make()
    with respx.mock(assert_all_called=False) as r:
        route = nobitex_stats(r)
        rate = await svc.toman_now()
        await svc.toman_now()  # cached
    assert rate.rate == Decimal("95000") and rate.source == "nobitex"
    assert route.call_count == 1


async def test_toman_now_falls_back_to_wallex_then_manual(make):
    svc = await make()
    with respx.mock(assert_all_called=False) as r:
        r.get(f"{NOBITEX}/market/stats").respond(503)
        r.get(f"{WALLEX}/v1/markets").respond(
            json={"result": {"symbols": {"USDTTMN": {"stats": {"lastPrice": "94500"}}}}}
        )
        rate = await svc.toman_now()
    assert rate.rate == Decimal("94500") and rate.source == "wallex"

    manual = await make(Decimal("90000"))
    with respx.mock() as r:  # no request at all
        rate = await manual.toman_now()
    assert rate.rate == Decimal("90000") and rate.source == "manual"


async def test_stablecoin_value_at_transfer_time_and_now(make):
    svc = await make()
    day = TODAY - timedelta(days=30)
    with respx.mock(assert_all_called=False) as r:
        nobitex_stats(r, "1000000")
        hist = r.get(f"{NOBITEX}/market/udf/history").respond(
            json={"s": "ok", "t": [ts(day - timedelta(days=1)), ts(day)], "c": [800000, 820000]}
        )
        when = datetime(day.year, day.month, day.day, 15, tzinfo=timezone.utc)
        [value] = await svc.values([(Chain.TRON, "USDT", USDT, Decimal("3999"), when)])
        # The same day again comes from the database, not the API.
        await svc.values([(Chain.TRON, "USDT", USDT, Decimal("1"), when)])
    assert value.usd_then == value.usd_now == Decimal("3999")
    assert value.toman_then == Decimal("3999") * 82000
    assert value.toman_now == Decimal("3999") * 100000
    assert hist.call_count == 1
    assert hist.calls[0].request.url.params["symbol"] == "USDTIRT"


async def test_missing_day_uses_closest_earlier_day(make):
    svc = await make()
    day = TODAY - timedelta(days=10)
    with respx.mock(assert_all_called=False) as r:
        nobitex_stats(r)
        r.get(f"{NOBITEX}/market/udf/history").respond(json={"s": "ok", "t": [ts(day - timedelta(days=3))], "c": [700000]})
        [q] = await svc.quotes([(Chain.TRON, "USDT", USDT, day)])
    assert q.toman_rate_then == Decimal("70000")


async def test_coin_history_from_coingecko_and_binance_for_old_days(make):
    svc = await make(Decimal("100000"))
    recent, old = TODAY - timedelta(days=5), TODAY - timedelta(days=800)
    ms = lambda d: ts(d) * 1000  # noqa: E731
    with respx.mock(assert_all_called=False) as r:
        r.get(f"{COINGECKO}/simple/price").respond(json={"tron": {"usd": 0.3}})
        r.get(f"{COINGECKO}/coins/tron/market_chart").respond(json={"prices": [[ms(recent), 0.25]]})
        binance = r.get(f"{BINANCE}/api/v3/klines").respond(json=[[ms(old), "0.05", "0.06", "0.04", "0.055", "1"]])
        r.get(f"{NOBITEX}/market/udf/history").respond(json={"s": "no_data"})
        q_recent, q_old = await svc.quotes([(Chain.TRON, "TRX", None, recent), (Chain.TRON, "TRX", None, old)])
    assert q_recent.usd_then == Decimal("0.25") and q_recent.usd_now == Decimal("0.3")
    assert q_old.usd_then == Decimal("0.055")
    assert binance.calls[0].request.url.params["symbol"] == "TRXUSDT"
    # No toman history: the manual rate fills in.
    assert q_old.toman_rate_then == q_recent.toman_rate_now == Decimal("100000")


async def test_unknown_and_fake_tokens_have_no_value(make):
    svc = await make(Decimal("100000"))
    with respx.mock():
        fake, unknown = await svc.quotes(
            [(Chain.TRON, "USDT", "TXLAQ63Xg1NAzckPwKHvzw7CSEmLMEqcdj", TODAY), (Chain.TRON, "WIN", "TLa2f6VPqDgRE67v1736s7bJ8Ray5wYjU7", None)]
        )
    assert fake.usd_then is None and unknown.usd_now is None


async def test_unreachable_sources_give_none_and_back_off(make):
    svc = await make()
    with respx.mock(assert_all_called=False) as r:
        stats = r.get(f"{NOBITEX}/market/stats").mock(side_effect=httpx.ConnectError("blocked"))
        r.get(f"{WALLEX}/v1/markets").mock(side_effect=httpx.ConnectError("blocked"))
        r.get(f"{NOBITEX}/market/udf/history").mock(side_effect=httpx.ConnectError("blocked"))
        [v] = await svc.values([(Chain.TRON, "USDT", USDT, Decimal(5), datetime.now(timezone.utc))])
        svc._now_checked = 0  # cache expired, but the failed source is not asked again yet
        await svc.toman_now()
    assert v.usd_then == Decimal(5) and v.toman_then is None and v.toman_now is None
    assert stats.call_count == 1


def test_prices_api_and_manual_rate_setting(client, tmp_path):  # noqa: F811
    client.app.state.services.env_file = tmp_path / ".env"
    resp = client.put("/api/settings/keys", json={"usd_toman_rate": "61,500"})
    assert resp.status_code == 200 and resp.json()["usd_toman_rate"] == "61500"
    assert client.get("/api/prices/rates").json()["source"] == "manual"
    day = (TODAY - timedelta(days=2)).isoformat()
    quotes = client.post(
        "/api/prices/quotes", json={"items": [{"chain": "tron", "symbol": "USDT", "contract": USDT, "day": day}]}
    ).json()
    assert quotes[0]["usd_then"] == "1" and quotes[0]["toman_rate_now"] == "61500"
    assert client.put("/api/settings/keys", json={"usd_toman_rate": "abc"}).status_code == 400
    cleared = client.put("/api/settings/keys", json={"usd_toman_rate": ""}).json()
    assert cleared["usd_toman_rate"] is None
