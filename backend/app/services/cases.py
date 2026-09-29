"""Investigation cases: addresses, saved traces and notes grouped under a title."""

import json
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel
from sqlalchemy import select

from app.db import CaseItemRow, CaseRow, Database
from app.models import Chain


class ItemKind(str, Enum):
    ADDRESS = "address"
    TRACE = "trace"
    NOTE = "note"


class CaseItem(BaseModel):
    id: int
    kind: ItemKind
    chain: Chain | None = None
    address: str | None = None
    title: str = ""
    note: str = ""
    data: dict | None = None
    created_at: datetime


class CaseSummary(BaseModel):
    id: int
    title: str
    description: str
    status: str
    created_at: datetime
    updated_at: datetime
    item_count: int = 0


class Case(CaseSummary):
    items: list[CaseItem]


class CaseNotFound(Exception):
    pass


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _item(row: CaseItemRow) -> CaseItem:
    return CaseItem(
        id=row.id,
        kind=ItemKind(row.kind),
        chain=Chain(row.chain) if row.chain else None,
        address=row.address,
        title=row.title,
        note=row.note,
        data=json.loads(row.data) if row.data else None,
        created_at=_utc(row.created_at),
    )


def _summary(row: CaseRow, count: int) -> CaseSummary:
    return CaseSummary(
        id=row.id,
        title=row.title,
        description=row.description,
        status=row.status,
        created_at=_utc(row.created_at),
        updated_at=_utc(row.updated_at),
        item_count=count,
    )


class CaseService:
    def __init__(self, db: Database):
        self._db = db

    async def list_cases(self) -> list[CaseSummary]:
        async with self._db.sessions() as session:
            cases = list(await session.scalars(select(CaseRow).order_by(CaseRow.updated_at.desc())))
            items = list(await session.scalars(select(CaseItemRow.case_id)))
        counts: dict[int, int] = {}
        for case_id in items:
            counts[case_id] = counts.get(case_id, 0) + 1
        return [_summary(c, counts.get(c.id, 0)) for c in cases]

    async def get(self, case_id: int) -> Case:
        async with self._db.sessions() as session:
            row = await session.get(CaseRow, case_id)
            if row is None:
                raise CaseNotFound(case_id)
            items = list(
                await session.scalars(
                    select(CaseItemRow).where(CaseItemRow.case_id == case_id).order_by(CaseItemRow.created_at)
                )
            )
        return Case(**_summary(row, len(items)).model_dump(), items=[_item(i) for i in items])

    async def create(self, title: str, description: str = "") -> Case:
        async with self._db.sessions.begin() as session:
            row = CaseRow(title=title, description=description)
            session.add(row)
            await session.flush()
            case_id = row.id
        return await self.get(case_id)

    async def update(
        self, case_id: int, title: str | None = None, description: str | None = None, status: str | None = None
    ) -> Case:
        async with self._db.sessions.begin() as session:
            row = await session.get(CaseRow, case_id)
            if row is None:
                raise CaseNotFound(case_id)
            if title is not None:
                row.title = title
            if description is not None:
                row.description = description
            if status is not None:
                row.status = status
            row.updated_at = datetime.now(timezone.utc)
        return await self.get(case_id)

    async def delete(self, case_id: int) -> None:
        async with self._db.sessions.begin() as session:
            row = await session.get(CaseRow, case_id)
            if row is None:
                raise CaseNotFound(case_id)
            await session.delete(row)

    async def add_item(
        self,
        case_id: int,
        kind: ItemKind,
        chain: Chain | None = None,
        address: str | None = None,
        title: str = "",
        note: str = "",
        data: dict | None = None,
    ) -> CaseItem:
        async with self._db.sessions.begin() as session:
            case = await session.get(CaseRow, case_id)
            if case is None:
                raise CaseNotFound(case_id)
            row = CaseItemRow(
                case_id=case_id,
                kind=kind.value,
                chain=chain.value if chain else None,
                address=address,
                title=title,
                note=note,
                data=json.dumps(data, default=str) if data is not None else None,
            )
            session.add(row)
            case.updated_at = datetime.now(timezone.utc)
            await session.flush()
            await session.refresh(row)
            return _item(row)

    async def delete_item(self, case_id: int, item_id: int) -> None:
        async with self._db.sessions.begin() as session:
            row = await session.get(CaseItemRow, item_id)
            if row is None or row.case_id != case_id:
                raise CaseNotFound(item_id)
            await session.delete(row)
