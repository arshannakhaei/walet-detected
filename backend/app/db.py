"""SQLite cache for fetched transfers, so each address is downloaded once per TTL."""

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, delete, or_, select, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.models import Chain, Transfer


class Base(DeclarativeBase):
    pass


class TransferRow(Base):
    __tablename__ = "transfers"

    chain: Mapped[str] = mapped_column(String(16), primary_key=True)
    transfer_id: Mapped[str] = mapped_column(String(300), primary_key=True)
    tx_hash: Mapped[str] = mapped_column(String(100), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    from_address: Mapped[str] = mapped_column(String(100), index=True)
    to_address: Mapped[str] = mapped_column(String(100), index=True)
    # Stored as text: SQLite has no exact decimal type.
    amount: Mapped[str] = mapped_column(String(80))
    token_symbol: Mapped[str] = mapped_column(String(40))
    token_contract: Mapped[str | None] = mapped_column(String(100), nullable=True)
    token_decimals: Mapped[int] = mapped_column(Integer)
    success: Mapped[bool] = mapped_column(Boolean, default=True)

    def to_model(self) -> Transfer:
        ts = self.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return Transfer(
            chain=Chain(self.chain),
            transfer_id=self.transfer_id,
            tx_hash=self.tx_hash,
            timestamp=ts,
            from_address=self.from_address,
            to_address=self.to_address,
            amount=Decimal(self.amount),
            token_symbol=self.token_symbol,
            token_contract=self.token_contract,
            token_decimals=self.token_decimals,
            success=self.success,
        )


class AddressSyncRow(Base):
    __tablename__ = "address_sync"

    chain: Mapped[str] = mapped_column(String(16), primary_key=True)
    address: Mapped[str] = mapped_column(String(100), primary_key=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    # How many transfers were requested in the last fetch; a truncated fetch
    # is redone when a caller later needs more than this.
    fetched_limit: Mapped[int] = mapped_column(Integer, default=0)


class LabelRow(Base):
    """A label the user attached to an address by hand."""

    __tablename__ = "labels"

    chain: Mapped[str] = mapped_column(String(16), primary_key=True)
    address: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(40))
    note: Mapped[str | None] = mapped_column(String(2000), nullable=True)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CaseRow(Base):
    """An investigation: a named collection of addresses, traces and notes."""

    __tablename__ = "cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CaseItemRow(Base):
    __tablename__ = "case_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # address, trace, note
    chain: Mapped[str | None] = mapped_column(String(16), nullable=True)
    address: Mapped[str | None] = mapped_column(String(100), nullable=True)
    title: Mapped[str] = mapped_column(String(300), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON snapshot (trace results)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class WatchRow(Base):
    """An address monitored for new transfers."""

    __tablename__ = "watchlist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chain: Mapped[str] = mapped_column(String(16))
    address: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200), default="")
    min_amount: Mapped[str] = mapped_column(String(80), default="0")
    token: Mapped[str | None] = mapped_column(String(100), nullable=True)
    telegram_chat_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Transfers at or before this moment have been reported already.
    seen_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AlertRow(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    watch_id: Mapped[int] = mapped_column(ForeignKey("watchlist.id", ondelete="CASCADE"), index=True)
    chain: Mapped[str] = mapped_column(String(16))
    address: Mapped[str] = mapped_column(String(100))
    transfer_id: Mapped[str] = mapped_column(String(300))
    tx_hash: Mapped[str] = mapped_column(String(100))
    direction: Mapped[str] = mapped_column(String(8))
    counterparty: Mapped[str] = mapped_column(String(100))
    amount: Mapped[str] = mapped_column(String(80))
    token_symbol: Mapped[str] = mapped_column(String(40))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class DailyRateRow(Base):
    """Cached daily prices: series "toman" (toman per USDT) or "usd:<coingecko id>"."""

    __tablename__ = "daily_rates"

    series: Mapped[str] = mapped_column(String(60), primary_key=True)
    day: Mapped[str] = mapped_column(String(10), primary_key=True)  # YYYY-MM-DD (UTC)
    value: Mapped[str] = mapped_column(String(40))


class Database:
    def __init__(self, url: str):
        self.engine: AsyncEngine = create_async_engine(url)
        if url.startswith("sqlite"):
            from sqlalchemy import event

            @event.listens_for(self.engine.sync_engine, "connect")
            def _fk_on(dbapi_conn, _record):  # SQLite ignores ON DELETE CASCADE without this
                cursor = dbapi_conn.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def init(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            # Databases created by v0.1 lack this column.
            cols = [r[1] for r in await conn.execute(text("PRAGMA table_info(address_sync)"))]
            if "fetched_limit" not in cols:
                await conn.execute(
                    text("ALTER TABLE address_sync ADD COLUMN fetched_limit INTEGER NOT NULL DEFAULT 0")
                )

    async def close(self) -> None:
        await self.engine.dispose()

    async def save_transfers(self, transfers: list[Transfer]) -> None:
        if not transfers:
            return
        rows = [
            {**t.model_dump(), "chain": t.chain.value, "amount": str(t.amount)} for t in transfers
        ]
        async with self.sessions.begin() as session:
            # SQLite caps bound parameters per statement, so insert in chunks.
            for i in range(0, len(rows), 500):
                stmt = sqlite_insert(TransferRow).values(rows[i : i + 500])
                await session.execute(stmt.on_conflict_do_nothing())

    async def transfers_for(self, chain: Chain, address: str) -> list[Transfer]:
        async with self.sessions() as session:
            result = await session.scalars(
                select(TransferRow)
                .where(
                    TransferRow.chain == chain.value,
                    or_(TransferRow.from_address == address, TransferRow.to_address == address),
                )
                .order_by(TransferRow.timestamp.desc())
            )
            return [row.to_model() for row in result]

    async def get_sync(self, chain: Chain, address: str) -> AddressSyncRow | None:
        async with self.sessions() as session:
            return await session.get(AddressSyncRow, (chain.value, address))

    async def set_sync(self, chain: Chain, address: str, truncated: bool, fetched_limit: int) -> None:
        async with self.sessions.begin() as session:
            await session.merge(
                AddressSyncRow(
                    chain=chain.value,
                    address=address,
                    synced_at=datetime.now(timezone.utc),
                    truncated=truncated,
                    fetched_limit=fetched_limit,
                )
            )

    async def list_labels(self, chain: Chain | None = None) -> list[LabelRow]:
        async with self.sessions() as session:
            stmt = select(LabelRow)
            if chain is not None:
                stmt = stmt.where(LabelRow.chain == chain.value)
            return list(await session.scalars(stmt))

    async def upsert_label(self, row: LabelRow) -> None:
        async with self.sessions.begin() as session:
            await session.merge(row)

    async def delete_label(self, chain: Chain, address: str) -> bool:
        async with self.sessions.begin() as session:
            result = await session.execute(
                delete(LabelRow).where(LabelRow.chain == chain.value, LabelRow.address == address)
            )
            return result.rowcount > 0

    async def daily_rates(self, series: str, start: str, end: str) -> dict[str, Decimal]:
        async with self.sessions() as session:
            rows = await session.scalars(
                select(DailyRateRow).where(
                    DailyRateRow.series == series, DailyRateRow.day >= start, DailyRateRow.day <= end
                )
            )
            return {r.day: Decimal(r.value) for r in rows}

    async def save_daily_rates(self, series: str, values: dict[str, Decimal]) -> None:
        if not values:
            return
        rows = [{"series": series, "day": d, "value": str(v)} for d, v in values.items()]
        async with self.sessions.begin() as session:
            for i in range(0, len(rows), 300):
                stmt = sqlite_insert(DailyRateRow).values(rows[i : i + 300])
                await session.execute(
                    stmt.on_conflict_do_update(index_elements=["series", "day"], set_={"value": stmt.excluded.value})
                )
