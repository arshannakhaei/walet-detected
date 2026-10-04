"""Dollar and toman rates for showing token amounts as money."""

from datetime import date

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.models import Chain
from app.services.fx import Quote, TomanRate

router = APIRouter(prefix="/api/prices", tags=["prices"])


@router.get("/rates", response_model=TomanRate)
async def rates(request: Request) -> TomanRate:
    """Toman per dollar today, and where it came from (manual, nobitex or wallex)."""
    return await request.app.state.services.values.toman_now()


class QuoteItem(BaseModel):
    chain: Chain
    symbol: str
    contract: str | None = None
    day: date | None = Field(None, description="Day of the transfer (UTC); empty for today's prices only.")


class QuotesIn(BaseModel):
    items: list[QuoteItem] = Field(max_length=1000)


@router.post("/quotes", response_model=list[Quote])
async def quotes(body: QuotesIn, request: Request) -> list[Quote]:
    """Per item: dollars per token then/now and toman per dollar then/now. Unknown tokens get nulls."""
    values = request.app.state.services.values
    return await values.quotes([(i.chain, i.symbol, i.contract, i.day) for i in body.items])
