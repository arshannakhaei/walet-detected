"""Links between several wallets (runs in the background; poll the job for progress)."""

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.models import Chain
from app.services.links import LinkJob, LinkParams, resolve_members
from app.services.wallet import UnsupportedChainError

router = APIRouter(prefix="/api", tags=["links"])


class LinksRequest(BaseModel):
    addresses: list[str] | str = Field(
        description="Wallet addresses, as a list or as text (one per line, or separated by spaces/commas)."
    )
    chain: Chain | None = None
    token: str | None = Field("USDT", description="Symbol or contract; empty for every token.")
    min_amount: Decimal = Field(Decimal("1"), ge=0)
    start: datetime | None = None
    end: datetime | None = None
    deep: bool = Field(False, description="Also download intermediaries to find three-hop paths (slower).")
    match_window_hours: int = Field(72, ge=1, le=24 * 90)

    def params(self) -> LinkParams:
        token = (self.token or "").strip() or None
        if token and token.startswith("0x"):
            token = token.lower()
        return LinkParams(
            token=token,
            min_amount=self.min_amount,
            start=self.start,
            end=self.end,
            deep=self.deep,
            match_window_hours=self.match_window_hours,
        )


@router.post("/links", response_model=LinkJob, response_model_exclude={"result"})
async def start_links(body: LinksRequest, request: Request) -> LinkJob:
    try:
        chain, addresses = resolve_members(body.addresses, body.chain, request.app.state.providers.supported_chains)
    except UnsupportedChainError as exc:
        raise HTTPException(501, str(exc)) from exc
    except ValueError as exc:  # includes AddressError
        raise HTTPException(400, str(exc)) from exc
    return request.app.state.links.start(chain, addresses, body.params())


@router.get("/links/{job_id}", response_model=LinkJob)
async def get_links(job_id: str, request: Request) -> LinkJob:
    job = request.app.state.links.get(job_id)
    if job is None:
        raise HTTPException(404, "analysis not found (the server may have restarted); run it again")
    return job
