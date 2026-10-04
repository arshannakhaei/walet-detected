"""Dollar and toman values of token amounts, at the time of a transfer and today.

- Toman per dollar (USDT/IRT market rate): today from Nobitex, falling back to
  Wallex; per day from Nobitex's daily candles. A rate set by hand
  (USD_TOMAN_RATE) replaces today's rate and fills days with no history.
- Dollar price per coin per day: CoinGecko daily prices for the last year,
  Binance daily candles for older days (or when CoinGecko is unavailable).
  Stablecoins are always worth one dollar.

Daily values are cached in the database, so each day is downloaded once.
Everything is best effort: when a source is unreachable, the value is None
and the dashboard shows the token amount alone.
"""

import asyncio
import logging
import time
from datetime import date, datetime, time as dtime, timedelta, timezone
from decimal import Decimal

import httpx
from pydantic import BaseModel

from app.db import Database
from app.models import Chain
from app.services.pricing import COINGECKO_IDS, PriceService
from app.services.tokens import STABLECOINS, known_symbol

log = logging.getLogger(__name__)

NOBITEX_URL = "https://api.nobitex.ir"
WALLEX_URL = "https://api.wallex.ir"
BINANCE_URL = "https://api.binance.com"
TOMAN_SERIES = "toman"
BINANCE_SYMBOLS = {
    "tron": "TRXUSDT",
    "ethereum": "ETHUSDT",
    "binancecoin": "BNBUSDT",
    "polygon-ecosystem-token": "POLUSDT",
    "avalanche-2": "AVAXUSDT",
    "bitcoin": "BTCUSDT",
    "solana": "SOLUSDT",
}
NOW_TTL = 300  # seconds today's toman rate is reused
RETRY_AFTER = 600  # seconds before a failed source is asked again
# A missing day uses the closest earlier day up to this far back (or a later one up to 3 days).
LOOKBACK_DAYS = 7


class Quote(BaseModel):
    """Prices for one (token, day): multiply the token amount by them."""

    usd_then: Decimal | None = None  # dollars per token on that day
    usd_now: Decimal | None = None
    toman_rate_then: Decimal | None = None  # toman per dollar on that day
    toman_rate_now: Decimal | None = None


class Value(BaseModel):
    usd_then: Decimal | None = None
    usd_now: Decimal | None = None
    toman_then: Decimal | None = None
    toman_now: Decimal | None = None


class TomanRate(BaseModel):
    rate: Decimal | None
    source: str | None  # "manual", "nobitex", "wallex" or None
    updated_at: datetime | None


def _day(d: date) -> str:
    return d.isoformat()


def _closest(values: dict[str, Decimal], d: date) -> Decimal | None:
    for back in range(LOOKBACK_DAYS + 1):
        v = values.get(_day(d - timedelta(days=back)))
        if v is not None:
            return v
    for ahead in range(1, 4):
        v = values.get(_day(d + timedelta(days=ahead)))
        if v is not None:
            return v
    return None


def _ts(d: date) -> int:
    return int(datetime.combine(d, dtime(), tzinfo=timezone.utc).timestamp())


class ValueService:
    def __init__(
        self,
        client: httpx.AsyncClient,
        db: Database,
        prices: PriceService,
        manual_rate: Decimal | None = None,
        nobitex_url: str = NOBITEX_URL,
        wallex_url: str = WALLEX_URL,
        binance_url: str = BINANCE_URL,
        coingecko_url: str | None = None,
    ):
        self._client = client
        self._db = db
        self._prices = prices
        self.manual_rate = manual_rate
        self._nobitex = nobitex_url.rstrip("/")
        self._wallex = wallex_url.rstrip("/")
        self._binance = binance_url.rstrip("/")
        self._coingecko = (coingecko_url or prices.base_url).rstrip("/")
        self._now: TomanRate = TomanRate(rate=None, source=None, updated_at=None)
        self._now_checked = 0.0
        self._failed: dict[str, float] = {}  # source -> time of the last failure
        self._coingecko_done: dict[str, str] = {}  # coin -> day its last year was fetched
        self._locks: dict[str, asyncio.Lock] = {}

    # --- sources ------------------------------------------------------------

    def _may_try(self, source: str) -> bool:
        failed = self._failed.get(source)
        return failed is None or time.monotonic() - failed > RETRY_AFTER

    async def _get(self, source: str, url: str, params: dict | None = None, headers: dict | None = None):
        try:
            resp = await self._client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            self._failed.pop(source, None)
            return data
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("%s unavailable: %s", source, exc)
            self._failed[source] = time.monotonic()
            return None

    async def _nobitex_now(self) -> Decimal | None:
        data = await self._get(
            "nobitex", f"{self._nobitex}/market/stats", {"srcCurrency": "usdt", "dstCurrency": "rls"}
        )
        try:
            return Decimal(str(data["stats"]["usdt-rls"]["latest"])) / 10  # rial -> toman
        except (TypeError, KeyError, ArithmeticError):
            return None

    async def _wallex_now(self) -> Decimal | None:
        data = await self._get("wallex", f"{self._wallex}/v1/markets")
        try:
            return Decimal(str(data["result"]["symbols"]["USDTTMN"]["stats"]["lastPrice"]))
        except (TypeError, KeyError, ArithmeticError):
            return None

    async def _nobitex_history(self, start: date, end: date) -> dict[str, Decimal]:
        data = await self._get(
            "nobitex-history",
            f"{self._nobitex}/market/udf/history",
            {"symbol": "USDTIRT", "resolution": "D", "from": _ts(start), "to": _ts(end + timedelta(days=1))},
        )
        if not isinstance(data, dict) or data.get("s") != "ok":
            return {}
        out = {}
        for t, close in zip(data.get("t", []), data.get("c", [])):
            day = datetime.fromtimestamp(int(t), tz=timezone.utc).date()
            out[_day(day)] = Decimal(str(close)) / 10
        return out

    async def _coingecko_history(self, coin: str) -> dict[str, Decimal]:
        data = await self._get(
            "coingecko-history",
            f"{self._coingecko}/coins/{coin}/market_chart",
            {"vs_currency": "usd", "days": "365", "interval": "daily"},
            self._prices.headers,
        )
        out = {}
        for ms, price in (data or {}).get("prices", []) if isinstance(data, dict) else []:
            day = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date()
            out[_day(day)] = Decimal(str(price))
        return out

    async def _binance_history(self, coin: str, start: date, end: date) -> dict[str, Decimal]:
        symbol = BINANCE_SYMBOLS.get(coin)
        if symbol is None:
            return {}
        out: dict[str, Decimal] = {}
        cursor = start
        while cursor <= end:  # 1000 days per request
            data = await self._get(
                "binance",
                f"{self._binance}/api/v3/klines",
                {
                    "symbol": symbol,
                    "interval": "1d",
                    "startTime": _ts(cursor) * 1000,
                    "endTime": _ts(end + timedelta(days=1)) * 1000,
                    "limit": 1000,
                },
            )
            if not isinstance(data, list) or not data:
                break
            for candle in data:
                day = datetime.fromtimestamp(candle[0] / 1000, tz=timezone.utc).date()
                out[_day(day)] = Decimal(str(candle[4]))  # close
            cursor = datetime.fromtimestamp(data[-1][0] / 1000, tz=timezone.utc).date() + timedelta(days=1)
            if len(data) < 1000:
                break
        return out

    # --- rates --------------------------------------------------------------

    async def toman_now(self) -> TomanRate:
        if self.manual_rate:
            return TomanRate(rate=self.manual_rate, source="manual", updated_at=None)
        if time.monotonic() - self._now_checked < NOW_TTL and self._now.rate is not None:
            return self._now
        async with self._locks.setdefault("toman-now", asyncio.Lock()):
            if time.monotonic() - self._now_checked < NOW_TTL and self._now.rate is not None:
                return self._now
            for source, fetch in (("nobitex", self._nobitex_now), ("wallex", self._wallex_now)):
                if not self._may_try(source):
                    continue
                rate = await fetch()
                if rate:
                    self._now = TomanRate(rate=rate, source=source, updated_at=datetime.now(timezone.utc))
                    break
            self._now_checked = time.monotonic()
        return self._now

    async def _series(self, series: str, days: set[date], fetch) -> dict[str, Decimal]:
        """Cached daily values covering `days`; `fetch(start, end)` downloads missing ones."""
        if not days:
            return {}
        start, end = min(days) - timedelta(days=LOOKBACK_DAYS), max(days) + timedelta(days=3)
        async with self._locks.setdefault(series, asyncio.Lock()):
            values = await self._db.daily_rates(series, _day(start), _day(end))
            today = datetime.now(timezone.utc).date()
            missing = [d for d in days if _closest(values, d) is None and d <= today]
            if missing:
                fetched = await fetch(min(missing) - timedelta(days=LOOKBACK_DAYS), max(missing) + timedelta(days=1))
                if fetched:
                    await self._db.save_daily_rates(series, fetched)
                    values.update(fetched)
        return values

    async def _toman_history(self, days: set[date]) -> dict[str, Decimal]:
        async def fetch(start: date, end: date) -> dict[str, Decimal]:
            return await self._nobitex_history(start, end) if self._may_try("nobitex-history") else {}

        return await self._series(TOMAN_SERIES, days, fetch)

    async def _usd_history(self, coin: str, days: set[date]) -> dict[str, Decimal]:
        async def fetch(start: date, end: date) -> dict[str, Decimal]:
            out: dict[str, Decimal] = {}
            today = _day(datetime.now(timezone.utc).date())
            year_ago = datetime.now(timezone.utc).date() - timedelta(days=360)
            if end >= year_ago and self._coingecko_done.get(coin) != today and self._may_try("coingecko-history"):
                out.update(await self._coingecko_history(coin))
                if out:
                    self._coingecko_done[coin] = today
            if (start < year_ago or not out) and self._may_try("binance"):
                out.update({k: v for k, v in (await self._binance_history(coin, start, end)).items() if k not in out})
            return out

        return await self._series(f"usd:{coin}", days, fetch)

    def coin(self, chain: Chain, symbol: str, contract: str | None) -> str | None:
        """'stable', a CoinGecko id, or None for tokens without a known price."""
        if contract is not None:
            # Anyone can deploy a token called "USDT"; only trust known contracts.
            symbol = known_symbol(chain, contract) or ""
        symbol = symbol.upper()
        if symbol in STABLECOINS:
            return "stable"
        return COINGECKO_IDS.get(symbol)

    async def quotes(self, items: list[tuple[Chain, str, str | None, date | None]]) -> list[Quote]:
        """Prices for (chain, symbol, contract, day) items; day None = today only."""
        coins = [self.coin(c, s, k) for c, s, k, _ in items]
        toman_now = (await self.toman_now()).rate
        toman_days = {d for (_, _, _, d), coin in zip(items, coins) if d is not None and coin is not None}
        toman_hist = await self._toman_history(toman_days)
        usd_days: dict[str, set[date]] = {}
        for (_, _, _, d), coin in zip(items, coins):
            if coin not in (None, "stable") and d is not None:
                usd_days.setdefault(coin, set()).add(d)
        usd_hist = {coin: await self._usd_history(coin, days) for coin, days in usd_days.items()}

        out = []
        for (chain, symbol, contract, d), coin in zip(items, coins):
            if coin is None:
                out.append(Quote())
                continue
            now = Decimal(1) if coin == "stable" else await self._prices.price(chain, symbol, contract)
            then = None
            toman_then = None
            if d is not None:
                then = Decimal(1) if coin == "stable" else _closest(usd_hist.get(coin, {}), d)
                toman_then = _closest(toman_hist, d) or self.manual_rate
            out.append(Quote(usd_then=then, usd_now=now, toman_rate_then=toman_then, toman_rate_now=toman_now))
        return out

    async def values(
        self, items: list[tuple[Chain, str, str | None, Decimal, datetime | None]]
    ) -> list[Value]:
        """Dollar and toman value of each (chain, symbol, contract, amount, time) item."""
        quotes = await self.quotes([(c, s, k, t.astimezone(timezone.utc).date() if t else None) for c, s, k, _, t in items])
        out = []
        for (_, _, _, amount, _), q in zip(items, quotes):
            usd_then = amount * q.usd_then if q.usd_then is not None else None
            usd_now = amount * q.usd_now if q.usd_now is not None else None
            out.append(
                Value(
                    usd_then=usd_then,
                    usd_now=usd_now,
                    toman_then=usd_then * q.toman_rate_then if usd_then is not None and q.toman_rate_then else None,
                    toman_now=usd_now * q.toman_rate_now if usd_now is not None and q.toman_rate_now else None,
                )
            )
        return out
