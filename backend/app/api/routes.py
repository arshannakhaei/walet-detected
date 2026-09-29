"""REST endpoints for wallet lookups."""

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.models import Chain, Counterparty, Direction, TransferView, WalletOverview
from app.providers import ProviderError
from app.services.addresses import detect_chains
from app.services.graph import Graph, GraphBuilder, GraphParams
from app.services.labels import Label, LabelCategory, LabelService
from app.services.tracer import (
    LotMethod,
    TraceDirection,
    Tracer,
    TraceParams,
    TraceResult,
    TraceStartNotFound,
)
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


# --- Graph ------------------------------------------------------------------


@router.get("/graph/{address}", response_model=Graph)
async def wallet_graph(
    address: str,
    request: Request,
    chain: Chain | None = None,
    depth_in: int = Query(2, ge=0, le=5),
    depth_out: int = Query(2, ge=0, le=5),
    max_nodes: int = Query(150, ge=2, le=2000),
    max_children: int = Query(20, ge=1, le=200),
    follow_time: bool = True,
    flt: TransferFilter = Depends(transfer_filter),
) -> Graph:
    params = GraphParams(
        depth_in=depth_in,
        depth_out=depth_out,
        max_nodes=max_nodes,
        max_children=max_children,
        follow_time=follow_time,
        filter=flt,
    )
    builder: GraphBuilder = request.app.state.graph_builder
    return await _call(builder.build(resolve_chain(address, chain), address, params))


# --- Trace ------------------------------------------------------------------


class TraceRequest(BaseModel):
    address: str = Field(description="A wallet that sent or received the starting transfer.")
    tx_hash: str
    chain: Chain | None = None
    token: str | None = Field(None, description="Symbol or contract, if the transaction moved several tokens.")
    amount: Decimal | None = Field(None, gt=0, description="Trace only part of the transfer.")
    direction: TraceDirection = TraceDirection.FORWARD
    method: LotMethod = LotMethod.FIFO
    max_hops: int = Field(6, ge=1, le=20)
    min_amount: Decimal = Field(Decimal("1"), ge=0)
    max_branches: int = Field(10, ge=1, le=100)
    tolerance: Decimal = Field(Decimal("0.01"), ge=0, le=Decimal("0.5"))
    exact_window_hours: int = Field(72, ge=1, le=24 * 90)
    max_steps: int = Field(200, ge=1, le=2000)


@router.post("/trace", response_model=TraceResult)
async def trace_funds(body: TraceRequest, request: Request) -> TraceResult:
    tracer: Tracer = request.app.state.tracer
    chain = resolve_chain(body.address, body.chain)
    try:
        start = await _call(tracer.find_start(chain, body.address, body.tx_hash, body.token))
    except TraceStartNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    params = TraceParams(
        direction=body.direction,
        method=body.method,
        max_hops=body.max_hops,
        min_amount=body.min_amount,
        max_branches=body.max_branches,
        tolerance=body.tolerance,
        exact_window_hours=body.exact_window_hours,
        max_steps=body.max_steps,
    )
    return await _call(tracer.trace(chain, start, params, body.amount))


# --- Labels -----------------------------------------------------------------


class LabelIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    category: LabelCategory
    note: str | None = Field(None, max_length=2000)


@router.get("/labels", response_model=list[Label])
async def list_labels(request: Request, chain: Chain | None = None) -> list[Label]:
    return request.app.state.labels.all(chain)


@router.put("/labels/{chain}/{address}", response_model=Label)
async def set_label(chain: Chain, address: str, body: LabelIn, request: Request) -> Label:
    if chain not in detect_chains(address):
        raise HTTPException(400, f"address is not a valid {chain.value} address")
    labels: LabelService = request.app.state.labels
    return await labels.set(chain, address, body.name, body.category, body.note)


@router.delete("/labels/{chain}/{address}", status_code=204)
async def delete_label(chain: Chain, address: str, request: Request) -> None:
    if not await request.app.state.labels.remove(chain, address):
        raise HTTPException(404, "no user label for this address")
