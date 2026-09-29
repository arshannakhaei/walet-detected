"""Fund-flow graph and amount tracing."""

import asyncio
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from app.api.deps import Target, call, normalized_filter, resolve, target, transfer_filter
from app.models import Chain
from app.services.graph import Graph, GraphBuilder, GraphParams
from app.services.graph_image import render_png
from app.services.tracer import (
    LotMethod,
    TraceDirection,
    Tracer,
    TraceParams,
    TraceResult,
    TraceStartNotFound,
)
from app.services.wallet import TransferFilter

router = APIRouter(prefix="/api", tags=["analysis"])


@router.get("/graph/{address}", response_model=Graph)
async def graph(
    request: Request,
    t: Target = Depends(target),
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
        filter=normalized_filter(flt, t.chain),
    )
    builder: GraphBuilder = request.app.state.graph_builder
    return await call(builder.build(t.chain, t.address, params))


@router.get("/graph/{address}/image.png", response_class=Response)
async def graph_png(
    request: Request,
    t: Target = Depends(target),
    depth_in: int = Query(1, ge=0, le=3),
    depth_out: int = Query(2, ge=0, le=3),
    max_nodes: int = Query(40, ge=2, le=150),
) -> Response:
    builder: GraphBuilder = request.app.state.graph_builder
    result = await call(
        builder.build(t.chain, t.address, GraphParams(depth_in=depth_in, depth_out=depth_out, max_nodes=max_nodes))
    )
    png = await asyncio.to_thread(render_png, result, max_nodes)
    return Response(png, media_type="image/png")


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

    def params(self) -> TraceParams:
        return TraceParams(
            direction=self.direction,
            method=self.method,
            max_hops=self.max_hops,
            min_amount=self.min_amount,
            max_branches=self.max_branches,
            tolerance=self.tolerance,
            exact_window_hours=self.exact_window_hours,
            max_steps=self.max_steps,
        )


async def run_trace(request: Request, body: TraceRequest) -> TraceResult:
    tracer: Tracer = request.app.state.tracer
    t = resolve(request, body.address, body.chain)
    token = body.token.lower() if body.token and body.token.startswith("0x") else body.token
    try:
        start = await call(tracer.find_start(t.chain, t.address, body.tx_hash.strip(), token))
    except TraceStartNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    return await call(tracer.trace(t.chain, start, body.params(), body.amount))


@router.post("/trace", response_model=TraceResult)
async def trace(body: TraceRequest, request: Request) -> TraceResult:
    return await run_trace(request, body)
