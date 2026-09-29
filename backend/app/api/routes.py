"""REST endpoints for wallet lookups."""

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from app.models import Chain, Counterparty, Direction, TransferView, WalletOverview
from app.providers import ProviderError
from app.services.addresses import detect_chains
from app.services.wallet import TransferFilter, UnsupportedChainError, WalletService

router = APIRouter(prefix="/api")


def get_service(request: Request) -> WalletService:
    return request.app.state.wallet_service


def resolve_chain(address: str, chain: Chain | None) -> Chain:
    candidates = detect_chains(address)
    if chain is not None:
        if chain not in candidates:
            raise HTTPException(400, f"address is not a valid {chain.value} address")
        return chain
    if not candidates:
        raise HTTPException(400, "unrecognized address format")
    return candidates[0]


def transfer_filter(
    token: str | None = None,
    direction: Direction | None = None,
    min_amount: Decimal | None = None,
    max_amount: Decimal | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    counterparty: str | None = None,
    include_failed: bool = False,
) -> TransferFilter:
    return TransferFilter(
        token=token,
        direction=direction,
        min_amount=min_amount,
        max_amount=max_amount,
        start=start,
        end=end,
        counterparty=counterparty,
        include_failed=include_failed,
    )


class DetectResponse(BaseModel):
    address: str
    chains: list[Chain]


class TransfersResponse(BaseModel):
    total: int
    items: list[TransferView]


@router.get("/health")
async def health(request: Request) -> dict:
    return {"status": "ok", "chains": request.app.state.providers.supported_chains}


@router.get("/detect/{address}", response_model=DetectResponse)
async def detect(address: str) -> DetectResponse:
    return DetectResponse(address=address, chains=detect_chains(address))


async def _call(coro):
    try:
        return await coro
    except UnsupportedChainError as exc:
        raise HTTPException(501, str(exc)) from exc
    except ProviderError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.get("/wallet/{address}/overview", response_model=WalletOverview)
async def wallet_overview(
    address: str,
    chain: Chain | None = None,
    service: WalletService = Depends(get_service),
) -> WalletOverview:
    return await _call(service.overview(resolve_chain(address, chain), address))


@router.get("/wallet/{address}/transfers", response_model=TransfersResponse)
async def wallet_transfers(
    address: str,
    chain: Chain | None = None,
    flt: TransferFilter = Depends(transfer_filter),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    service: WalletService = Depends(get_service),
) -> TransfersResponse:
    items, total = await _call(
        service.transfers(resolve_chain(address, chain), address, flt, limit, offset)
    )
    return TransfersResponse(total=total, items=items)


@router.get("/wallet/{address}/counterparties", response_model=list[Counterparty])
async def wallet_counterparties(
    address: str,
    chain: Chain | None = None,
    flt: TransferFilter = Depends(transfer_filter),
    limit: int = Query(200, ge=1, le=5000),
    service: WalletService = Depends(get_service),
) -> list[Counterparty]:
    result = await _call(service.counterparties(resolve_chain(address, chain), address, flt))
    return result[:limit]


@router.post("/wallet/{address}/refresh", response_model=WalletOverview)
async def wallet_refresh(
    address: str,
    chain: Chain | None = None,
    service: WalletService = Depends(get_service),
) -> WalletOverview:
    resolved = resolve_chain(address, chain)
    await _call(service.load_transfers(resolved, address, refresh=True))
    return await _call(service.overview(resolved, address))
