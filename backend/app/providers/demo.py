"""Offline demo data: a synthetic scam-and-launder story on Tron.

Enabled with DEMO_MODE=true. Lets the dashboard, bot and MCP tools be shown
without internet access or API keys. All addresses are valid Tron addresses
generated from fixed seeds, except the exchange hot wallet and the sanctioned
address, which come from the built-in label lists so their labels apply.

The story (USDT unless noted):
  1. 14 victims pay the SCAMMER within a day (fan-in); older unrelated funds
     already sit in the wallet.
  2. The scammer splits the proceeds to four MULE wallets in round amounts.
  3. Mules forward almost everything within minutes (pass-through):
     - mule A -> peel chain of three hops, each peeling ~10% to a
       deposit address and forwarding the rest, ending at the exchange;
     - mule B -> straight to the exchange;
     - mule C -> an OFAC-sanctioned address;
     - mule D -> keeps part of it (still unspent).
  4. A spammer sends the scammer a zero-value fake "USDT" token (hidden by the spam filter).
"""

import hashlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.models import Chain, TokenBalance, Transfer
from app.providers.base import ChainProvider, TransferPage
from app.services.addresses import tron_hex_to_base58

USDT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
FAKE_USDT = "TXLAQ63Xg1NAzckPwKHvzw7CSEmLMEqcdj"
EXCHANGE = "TV6MuMXfmLbBqPZvBHdwFsDnQeVfnmiuSi"  # in data/labels/tron.json
SANCTIONED = "TUCsTq7TofTCJRRoHk6RvhMoS2mJLm5Yzq"  # in data/labels/tron.ofac.json
T0 = datetime(2025, 3, 3, 9, 0, tzinfo=timezone.utc)


def addr(seed: str) -> str:
    return tron_hex_to_base58("41" + hashlib.sha256(seed.encode()).hexdigest()[:40])


SCAMMER = addr("scammer")
MULES = [addr(f"mule-{i}") for i in "ABCD"]
PEEL = [addr(f"peel-{i}") for i in range(3)]
DEPOSITS = [addr(f"deposit-{i}") for i in range(3)]
VICTIMS = [addr(f"victim-{i}") for i in range(14)]
OLD_FRIEND = addr("old-friend")
FEE_PAYER = addr("fee-payer")


class _Builder:
    def __init__(self) -> None:
        self.transfers: list[Transfer] = []
        self._n = 0

    def add(self, frm: str, to: str, amount: str | int, minutes: float, token: str = "USDT", contract: str | None = USDT):
        self._n += 1
        tx = hashlib.sha256(f"demo-tx-{self._n}".encode()).hexdigest()
        decimals = 6
        self.transfers.append(
            Transfer(
                chain=Chain.TRON,
                transfer_id=f"{tx}:demo",
                tx_hash=tx,
                timestamp=T0 + timedelta(minutes=minutes),
                from_address=frm,
                to_address=to,
                amount=Decimal(str(amount)),
                token_symbol=token,
                token_contract=contract if token != "TRX" else None,
                token_decimals=decimals,
            )
        )


def build_story() -> list[Transfer]:
    b = _Builder()
    # Older, unrelated funds in the scammer wallet.
    b.add(OLD_FRIEND, SCAMMER, 1200, -60 * 24 * 20)
    b.add(FEE_PAYER, SCAMMER, 300, -60 * 24 * 20, token="TRX")
    for m in MULES + PEEL:
        b.add(FEE_PAYER, m, 60, -60 * 24, token="TRX")

    # 1. Victims pay within one day.
    amounts = [2500, 1800, 3200, 950, 4100, 1500, 2750, 600, 5000, 1320, 2200, 880, 3900, 1600]
    for i, (victim, amount) in enumerate(zip(VICTIMS, amounts)):
        b.add(FEE_PAYER, victim, 40, -60 * 24 * 3 + i, token="TRX")
        b.add(addr(f"exchange-withdrawal-{i % 3}"), victim, amount + 100, -60 * 24 * 2 + i * 7)
        b.add(victim, SCAMMER, amount, i * 90)

    # 2. Split to mules in round amounts.
    split_at = 60 * 22
    b.add(SCAMMER, MULES[0], 12000, split_at)
    b.add(SCAMMER, MULES[1], 9000, split_at + 3)
    b.add(SCAMMER, MULES[2], 6000, split_at + 7)
    b.add(SCAMMER, MULES[3], 5000, split_at + 11)

    # 3a. Peel chain from mule A.
    hops = [MULES[0]] + PEEL + [EXCHANGE]
    amount = Decimal(11990)  # the mule forwards almost all of it
    t = split_at + 6
    for i in range(len(hops) - 1):
        if i > 0:
            peel = (amount * Decimal("0.1")).quantize(Decimal(1))
            b.add(hops[i], DEPOSITS[i - 1], peel, t - 1)
            amount -= peel
        b.add(hops[i], hops[i + 1], amount, t)
        t += 12
    b.add(DEPOSITS[0], EXCHANGE, 1199, t + 30)

    # 3b-d.
    b.add(MULES[1], EXCHANGE, 8995, split_at + 15)
    b.add(MULES[2], SANCTIONED, 5990, split_at + 25)
    b.add(MULES[3], addr("cold-storage"), 3000, split_at + 60 * 30)

    # 4. Zero-value fake "USDT" spam.
    b.add(addr("spammer"), SCAMMER, 0, split_at + 20, token="USDT", contract=FAKE_USDT)
    return b.transfers


class DemoProvider(ChainProvider):
    chain = Chain.TRON

    def __init__(self):  # no network client needed
        self._transfers = build_story()

    async def get_transfers(self, address: str, max_items: int) -> TransferPage:
        mine = [t for t in self._transfers if address in (t.from_address, t.to_address)]
        mine.sort(key=lambda t: t.timestamp, reverse=True)
        # The exchange hot wallet stands in for a busy service: report it as truncated.
        return TransferPage(mine[:max_items], truncated=address == EXCHANGE or len(mine) > max_items)

    async def get_balances(self, address: str) -> list[TokenBalance]:
        totals: dict[str, Decimal] = {}
        for t in self._transfers:
            if t.amount == 0 or t.token_contract == FAKE_USDT:
                continue
            sign = 1 if t.to_address == address else -1 if t.from_address == address else 0
            totals[t.token_symbol] = totals.get(t.token_symbol, Decimal(0)) + sign * t.amount
        return [
            TokenBalance(token_symbol=s, token_contract=USDT if s == "USDT" else None, amount=a)
            for s, a in totals.items()
            if a > 0 or s == "TRX"
        ]
