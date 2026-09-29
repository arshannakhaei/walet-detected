"""Watchlist monitoring: poll watched addresses and raise alerts for new transfers."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from decimal import Decimal

from pydantic import BaseModel
from sqlalchemy import select, update

from app.db import AlertRow, Database, WatchRow
from app.models import Chain, Direction
from app.providers import ProviderError
from app.services.wallet import TransferFilter, WalletService

log = logging.getLogger(__name__)


class Watch(BaseModel):
    id: int
    chain: Chain
    address: str
    name: str
    min_amount: Decimal
    token: str | None
    telegram_chat_id: str | None
    last_checked_at: datetime | None
    created_at: datetime


class Alert(BaseModel):
    id: int
    watch_id: int
    watch_name: str = ""
    chain: Chain
    address: str
    tx_hash: str
    direction: Direction
    counterparty: str
    amount: Decimal
    token_symbol: str
    timestamp: datetime
    read: bool
    telegram_chat_id: str | None = None


Notifier = Callable[[Alert], Awaitable[None]]


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _watch(row: WatchRow) -> Watch:
    return Watch(
        id=row.id,
        chain=Chain(row.chain),
        address=row.address,
        name=row.name,
        min_amount=Decimal(row.min_amount),
        token=row.token,
        telegram_chat_id=row.telegram_chat_id,
        last_checked_at=_utc(row.last_checked_at),
        created_at=_utc(row.created_at),
    )


def _alert(row: AlertRow, watch_name: str = "", chat_id: str | None = None) -> Alert:
    return Alert(
        id=row.id,
        watch_id=row.watch_id,
        watch_name=watch_name,
        chain=Chain(row.chain),
        address=row.address,
        tx_hash=row.tx_hash,
        direction=Direction(row.direction),
        counterparty=row.counterparty,
        amount=Decimal(row.amount),
        token_symbol=row.token_symbol,
        timestamp=_utc(row.timestamp),
        read=row.read,
        telegram_chat_id=chat_id,
    )


class MonitorService:
    def __init__(self, db: Database, wallets: WalletService, poll_limit: int = 200):
        self._db = db
        self._wallets = wallets
        self._poll_limit = poll_limit
        self._notifiers: list[Notifier] = []
        self._task: asyncio.Task | None = None

    def add_notifier(self, notifier: Notifier) -> None:
        self._notifiers.append(notifier)

    # --- watchlist ----------------------------------------------------------

    async def list_watches(self) -> list[Watch]:
        async with self._db.sessions() as session:
            return [_watch(r) for r in await session.scalars(select(WatchRow).order_by(WatchRow.id))]

    async def add(
        self,
        chain: Chain,
        address: str,
        name: str = "",
        min_amount: Decimal = Decimal(0),
        token: str | None = None,
        telegram_chat_id: str | None = None,
    ) -> Watch:
        # Only transfers after this moment are news; existing history is not alerted.
        transfers, _ = await self._wallets.load_transfers(chain, address, limit=self._poll_limit)
        seen_until = max((t.timestamp for t in transfers), default=datetime.now(timezone.utc))
        async with self._db.sessions.begin() as session:
            existing = await session.scalar(
                select(WatchRow).where(
                    WatchRow.chain == chain.value,
                    WatchRow.address == address,
                    WatchRow.telegram_chat_id.is_(telegram_chat_id)
                    if telegram_chat_id is None
                    else WatchRow.telegram_chat_id == telegram_chat_id,
                )
            )
            row = existing or WatchRow(chain=chain.value, address=address, seen_until=seen_until)
            row.name = name or row.name or ""
            row.min_amount = str(min_amount)
            row.token = token
            row.telegram_chat_id = telegram_chat_id
            row.last_checked_at = datetime.now(timezone.utc)
            session.add(row)
            await session.flush()
            return _watch(row)

    async def remove(self, watch_id: int) -> bool:
        async with self._db.sessions.begin() as session:
            row = await session.get(WatchRow, watch_id)
            if row is None:
                return False
            await session.delete(row)
            return True

    # --- alerts -------------------------------------------------------------

    async def alerts(self, unread_only: bool = False, limit: int = 200) -> list[Alert]:
        async with self._db.sessions() as session:
            stmt = select(AlertRow, WatchRow).join(WatchRow, WatchRow.id == AlertRow.watch_id)
            if unread_only:
                stmt = stmt.where(AlertRow.read.is_(False))
            stmt = stmt.order_by(AlertRow.timestamp.desc()).limit(limit)
            return [_alert(a, w.name, w.telegram_chat_id) for a, w in (await session.execute(stmt)).all()]

    async def mark_read(self, ids: list[int] | None = None) -> None:
        async with self._db.sessions.begin() as session:
            stmt = update(AlertRow).values(read=True)
            if ids is not None:
                stmt = stmt.where(AlertRow.id.in_(ids))
            await session.execute(stmt)

    # --- polling ------------------------------------------------------------

    async def check_watch(self, watch_id: int) -> list[Alert]:
        async with self._db.sessions() as session:
            row = await session.get(WatchRow, watch_id)
        if row is None:
            return []
        watch = _watch(row)
        transfers, _ = await self._wallets.load_transfers(
            watch.chain, watch.address, refresh=True, limit=self._poll_limit
        )
        seen_until = _utc(row.seen_until) or datetime.min.replace(tzinfo=timezone.utc)
        flt = TransferFilter(token=watch.token, min_amount=watch.min_amount or None)
        new = [t for t in transfers if t.timestamp > seen_until and flt.matches(t, watch.address)]
        newest = max((t.timestamp for t in transfers), default=seen_until)

        created: list[Alert] = []
        async with self._db.sessions.begin() as session:
            fresh = await session.get(WatchRow, watch_id)
            if fresh is None:
                return []
            for t in sorted(new, key=lambda t: t.timestamp):
                alert = AlertRow(
                    watch_id=watch_id,
                    chain=watch.chain.value,
                    address=watch.address,
                    transfer_id=t.transfer_id,
                    tx_hash=t.tx_hash,
                    direction=t.direction_for(watch.address).value,
                    counterparty=t.counterparty_for(watch.address),
                    amount=str(t.amount),
                    token_symbol=t.token_symbol,
                    timestamp=t.timestamp,
                )
                session.add(alert)
                await session.flush()
                created.append(_alert(alert, watch.name, watch.telegram_chat_id))
            fresh.seen_until = max(newest, seen_until)
            fresh.last_checked_at = datetime.now(timezone.utc)

        for alert in created:
            for notify in self._notifiers:
                try:
                    await notify(alert)
                except Exception:  # a failing channel must not stop monitoring
                    log.exception("alert notification failed")
        return created

    async def check_all(self) -> list[Alert]:
        created: list[Alert] = []
        for watch in await self.list_watches():
            try:
                created.extend(await self.check_watch(watch.id))
            except ProviderError as exc:
                log.warning("monitor: %s %s: %s", watch.chain.value, watch.address, exc)
        return created

    def start(self, interval_seconds: int) -> None:
        async def loop():
            while True:
                await asyncio.sleep(interval_seconds)
                try:
                    await self.check_all()
                except Exception:
                    log.exception("monitor loop failed")

        self._task = asyncio.create_task(loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
