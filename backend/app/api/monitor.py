"""Watchlist and alerts."""

from decimal import Decimal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.deps import call, resolve
from app.models import Chain
from app.services.monitor import Alert, MonitorService, Watch

router = APIRouter(prefix="/api", tags=["monitor"])


def _svc(request: Request) -> MonitorService:
    return request.app.state.monitor


class WatchIn(BaseModel):
    address: str
    chain: Chain | None = None
    name: str = Field("", max_length=200)
    min_amount: Decimal = Field(Decimal(0), ge=0)
    token: str | None = None
    telegram_chat_id: str | None = Field(None, description="Also notify this Telegram chat")


class MarkRead(BaseModel):
    ids: list[int] | None = Field(None, description="Omit to mark all as read")


@router.get("/watchlist", response_model=list[Watch])
async def list_watches(request: Request) -> list[Watch]:
    return await _svc(request).list_watches()


@router.post("/watchlist", response_model=Watch, status_code=201)
async def add_watch(body: WatchIn, request: Request) -> Watch:
    t = resolve(request, body.address, body.chain)
    return await call(
        _svc(request).add(t.chain, t.address, body.name, body.min_amount, body.token, body.telegram_chat_id)
    )


@router.delete("/watchlist/{watch_id}", status_code=204)
async def remove_watch(watch_id: int, request: Request) -> None:
    if not await _svc(request).remove(watch_id):
        raise HTTPException(404, "not in watchlist")


@router.post("/watchlist/check", response_model=list[Alert])
async def check_now(request: Request) -> list[Alert]:
    """Poll every watched address now instead of waiting for the next interval."""
    return await _svc(request).check_all()


@router.get("/alerts", response_model=list[Alert])
async def alerts(request: Request, unread_only: bool = False, limit: int = 200) -> list[Alert]:
    return await _svc(request).alerts(unread_only, min(limit, 1000))


@router.post("/alerts/read", status_code=204)
async def mark_read(body: MarkRead, request: Request) -> None:
    await _svc(request).mark_read(body.ids)
