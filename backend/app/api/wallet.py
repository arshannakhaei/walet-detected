"""Wallet lookups: overview, transfers, counterparties."""

import asyncio

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from app.api.deps import Target, call, normalized_filter, target, transfer_filter
from app.models import Chain, Counterparty, TransferView, WalletOverview
from app.providers import ProviderError
from app.services.addresses import detect_chains, normalize_address
from app.services.wallet import TransferFilter, WalletService

router = APIRouter(prefix="/api", tags=["wallet"])


def service(request: Request) -> WalletService:
    return request.app.state.wallet_service


class ChainInfo(BaseModel):
    chain: Chain
    supported: bool


class DetectResponse(BaseModel):
    address: str
    chains: list[ChainInfo]


class ActivityItem(BaseModel):
    chain: Chain
    active: bool | None  # None = could not check
    error: str | None = None


class TransfersResponse(BaseModel):
    total: int
    items: list[TransferView]


@router.get("/health")
async def health(request: Request) -> dict:
    return {"status": "ok", "chains": request.app.state.providers.supported_chains}


@router.get("/chains", response_model=list[ChainInfo])
async def chains(request: Request) -> list[ChainInfo]:
    supported = request.app.state.providers.supported_chains
    return [ChainInfo(chain=c, supported=c in supported) for c in Chain]


@router.get("/detect/{address}", response_model=DetectResponse)
async def detect(address: str, request: Request) -> DetectResponse:
    supported = request.app.state.providers.supported_chains
    return DetectResponse(
        address=address,
        chains=[ChainInfo(chain=c, supported=c in supported) for c in detect_chains(address.strip())],
    )


@router.get("/wallet/{address}/activity", response_model=list[ActivityItem])
async def activity(address: str, request: Request) -> list[ActivityItem]:
    """Which of the chains this address fits has it actually been used on?"""
    registry = request.app.state.providers

    async def check(chain: Chain) -> ActivityItem:
        provider = registry.get(chain)
        if provider is None:
            return ActivityItem(chain=chain, active=None, error="not configured")
        try:
            page = await provider.get_transfers(normalize_address(chain, address), 1)
            return ActivityItem(chain=chain, active=bool(page.transfers))
        except ProviderError as exc:
            return ActivityItem(chain=chain, active=None, error=str(exc))

    return list(await asyncio.gather(*(check(c) for c in detect_chains(address.strip()))))


@router.get("/wallet/{address}/overview", response_model=WalletOverview)
async def overview(t: Target = Depends(target), svc: WalletService = Depends(service)) -> WalletOverview:
    return await call(svc.overview(t.chain, t.address))


@router.get("/wallet/{address}/transfers", response_model=TransfersResponse)
async def transfers(
    t: Target = Depends(target),
    flt: TransferFilter = Depends(transfer_filter),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    svc: WalletService = Depends(service),
) -> TransfersResponse:
    items, total = await call(svc.transfers(t.chain, t.address, normalized_filter(flt, t.chain), limit, offset))
    return TransfersResponse(total=total, items=items)


@router.get("/wallet/{address}/counterparties", response_model=list[Counterparty])
async def counterparties(
    t: Target = Depends(target),
    flt: TransferFilter = Depends(transfer_filter),
    limit: int = Query(200, ge=1, le=5000),
    svc: WalletService = Depends(service),
) -> list[Counterparty]:
    result = await call(svc.counterparties(t.chain, t.address, normalized_filter(flt, t.chain)))
    return result[:limit]


@router.post("/wallet/{address}/refresh", response_model=WalletOverview)
async def refresh(t: Target = Depends(target), svc: WalletService = Depends(service)) -> WalletOverview:
    await call(svc.load_transfers(t.chain, t.address, refresh=True))
    return await call(svc.overview(t.chain, t.address))
