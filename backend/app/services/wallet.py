"""Wallet analysis: cached transfer history, overview stats and counterparties."""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from app.db import Database
from app.models import (
    Chain,
    Counterparty,
    Direction,
    TokenFlow,
    Transfer,
    TransferView,
    WalletOverview,
)
from app.providers import ProviderRegistry


class UnsupportedChainError(Exception):
    pass


@dataclass
class TransferFilter:
    token: str | None = None  # symbol (case-insensitive) or contract address
    direction: Direction | None = None
    min_amount: Decimal | None = None
    max_amount: Decimal | None = None
    start: datetime | None = None
    end: datetime | None = None
    counterparty: str | None = None
    include_failed: bool = False

    def matches(self, t: Transfer, address: str) -> bool:
        if not self.include_failed and not t.success:
            return False
        if self.token and self.token.upper() != t.token_symbol.upper() and self.token != t.token_contract:
            return False
        if self.direction and t.direction_for(address) != self.direction:
            return False
        if self.min_amount is not None and t.amount < self.min_amount:
            return False
        if self.max_amount is not None and t.amount > self.max_amount:
            return False
        if self.start and t.timestamp < self.start:
            return False
        if self.end and t.timestamp > self.end:
            return False
        if self.counterparty and t.counterparty_for(address) != self.counterparty:
            return False
        return True


class WalletService:
    def __init__(self, db: Database, providers: ProviderRegistry, max_transfers: int, cache_ttl: int):
        self._db = db
        self._providers = providers
        self._max_transfers = max_transfers
        self._cache_ttl = cache_ttl

    def _provider(self, chain: Chain):
        provider = self._providers.get(chain)
        if provider is None:
            raise UnsupportedChainError(f"chain '{chain.value}' is not supported yet")
        return provider

    async def load_transfers(
        self, chain: Chain, address: str, refresh: bool = False
    ) -> tuple[list[Transfer], bool]:
        """Return (transfers newest first, truncated), fetching from the chain when stale."""
        provider = self._provider(chain)
        sync = await self._db.get_sync(chain, address)
        stale = sync is None or refresh
        if sync is not None and not stale:
            synced_at = sync.synced_at
            if synced_at.tzinfo is None:
                synced_at = synced_at.replace(tzinfo=timezone.utc)
            stale = (datetime.now(timezone.utc) - synced_at).total_seconds() > self._cache_ttl
        if stale:
            page = await provider.get_transfers(address, self._max_transfers)
            await self._db.save_transfers(page.transfers)
            await self._db.set_sync(chain, address, page.truncated)
            truncated = page.truncated
        else:
            truncated = sync.truncated
        return await self._db.transfers_for(chain, address), truncated

    async def transfers(
        self,
        chain: Chain,
        address: str,
        flt: TransferFilter,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[TransferView], int]:
        """Filtered transfers from the wallet's point of view, plus the total match count."""
        all_transfers, _ = await self.load_transfers(chain, address)
        matched = [t for t in all_transfers if flt.matches(t, address)]
        views = [
            TransferView(transfer=t, direction=t.direction_for(address), counterparty=t.counterparty_for(address))
            for t in matched[offset : offset + limit]
        ]
        return views, len(matched)

    async def overview(self, chain: Chain, address: str) -> WalletOverview:
        transfers, truncated = await self.load_transfers(chain, address)
        balances = await self._provider(chain).get_balances(address)

        ok = [t for t in transfers if t.success]
        flows: dict[tuple[str, str | None], TokenFlow] = {}
        counterparties: set[str] = set()
        for t in ok:
            key = (t.token_symbol, t.token_contract)
            flow = flows.setdefault(key, TokenFlow(token_symbol=t.token_symbol, token_contract=t.token_contract))
            direction = t.direction_for(address)
            if direction == Direction.IN:
                flow.total_in += t.amount
                flow.count_in += 1
            elif direction == Direction.OUT:
                flow.total_out += t.amount
                flow.count_out += 1
            counterparties.add(t.counterparty_for(address))
        counterparties.discard(address)

        return WalletOverview(
            chain=chain,
            address=address,
            balances=balances,
            first_seen=min((t.timestamp for t in transfers), default=None),
            last_seen=max((t.timestamp for t in transfers), default=None),
            transfer_count=len(transfers),
            counterparty_count=len(counterparties),
            flows=sorted(flows.values(), key=lambda f: f.count_in + f.count_out, reverse=True),
            truncated=truncated,
        )

    async def counterparties(
        self, chain: Chain, address: str, flt: TransferFilter
    ) -> list[Counterparty]:
        """Who sent money to / received money from this wallet, per token, largest first."""
        transfers, _ = await self.load_transfers(chain, address)
        grouped: dict[tuple[str, str, str | None], Counterparty] = {}
        for t in transfers:
            if not flt.matches(t, address):
                continue
            direction = t.direction_for(address)
            if direction == Direction.SELF:
                continue
            other = t.counterparty_for(address)
            key = (other, t.token_symbol, t.token_contract)
            cp = grouped.get(key)
            if cp is None:
                cp = grouped[key] = Counterparty(
                    address=other,
                    token_symbol=t.token_symbol,
                    token_contract=t.token_contract,
                    first_seen=t.timestamp,
                    last_seen=t.timestamp,
                )
            if direction == Direction.IN:
                cp.received_from += t.amount
                cp.count_in += 1
            else:
                cp.sent_to += t.amount
                cp.count_out += 1
            cp.first_seen = min(cp.first_seen, t.timestamp)
            cp.last_seen = max(cp.last_seen, t.timestamp)
        return sorted(grouped.values(), key=lambda c: c.received_from + c.sent_to, reverse=True)
