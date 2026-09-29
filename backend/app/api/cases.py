"""Investigation cases."""

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from app.api.analysis import TraceRequest, run_trace
from app.api.deps import Target, resolve
from app.api.insights import render_wallet
from app.models import Chain
from app.services import report
from app.services.cases import Case, CaseItem, CaseNotFound, CaseService, CaseSummary, ItemKind

router = APIRouter(prefix="/api/cases", tags=["cases"])


def _svc(request: Request) -> CaseService:
    return request.app.state.cases


class CaseIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = Field("", max_length=20_000)


class CasePatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=300)
    description: str | None = Field(None, max_length=20_000)
    status: str | None = Field(None, pattern="^(open|closed)$")


class ItemIn(BaseModel):
    kind: ItemKind
    chain: Chain | None = None
    address: str | None = None
    title: str = Field("", max_length=300)
    note: str = Field("", max_length=20_000)
    trace: TraceRequest | None = Field(None, description="For kind=trace: runs the trace and stores a snapshot.")
    data: dict | None = Field(None, description="For kind=trace: an existing trace result to store as is.")


async def _guard(coro):
    try:
        return await coro
    except CaseNotFound as exc:
        raise HTTPException(404, "case or item not found") from exc


@router.get("", response_model=list[CaseSummary])
async def list_cases(request: Request) -> list[CaseSummary]:
    return await _svc(request).list()


@router.post("", response_model=Case, status_code=201)
async def create_case(body: CaseIn, request: Request) -> Case:
    return await _svc(request).create(body.title, body.description)


@router.get("/{case_id}", response_model=Case)
async def get_case(case_id: int, request: Request) -> Case:
    return await _guard(_svc(request).get(case_id))


@router.patch("/{case_id}", response_model=Case)
async def update_case(case_id: int, body: CasePatch, request: Request) -> Case:
    return await _guard(_svc(request).update(case_id, body.title, body.description, body.status))


@router.delete("/{case_id}", status_code=204)
async def delete_case(case_id: int, request: Request) -> None:
    await _guard(_svc(request).delete(case_id))


@router.post("/{case_id}/items", response_model=CaseItem, status_code=201)
async def add_item(case_id: int, body: ItemIn, request: Request) -> CaseItem:
    svc = _svc(request)
    chain, address, data = body.chain, body.address, body.data
    if body.kind == ItemKind.ADDRESS:
        if not address:
            raise HTTPException(400, "address is required")
        t: Target = resolve(request, address, chain)
        chain, address = t.chain, t.address
    elif body.kind == ItemKind.TRACE:
        if body.trace is not None:
            result = await run_trace(request, body.trace)
            data = result.model_dump(mode="json")
        if not data:
            raise HTTPException(400, "trace or data is required")
        chain = Chain(data["chain"])
        address = (data.get("start") or {}).get("from_address")
    return await _guard(svc.add_item(case_id, body.kind, chain, address, body.title, body.note, data))


@router.delete("/{case_id}/items/{item_id}", status_code=204)
async def delete_item(case_id: int, item_id: int, request: Request) -> None:
    await _guard(_svc(request).delete_item(case_id, item_id))


@router.get("/{case_id}/export.json")
async def export_case(case_id: int, request: Request) -> JSONResponse:
    case = await _guard(_svc(request).get(case_id))
    return JSONResponse(
        case.model_dump(mode="json"),
        headers={"Content-Disposition": f'attachment; filename="case_{case_id}.json"'},
    )


@router.get("/{case_id}/report", response_class=HTMLResponse)
async def case_report(case_id: int, request: Request, lang: str = Query("fa", pattern="^(fa|en)$")) -> str:
    case = await _guard(_svc(request).get(case_id))
    sections = {}
    for item in case.items:
        if item.kind == ItemKind.ADDRESS and item.chain and item.address:
            try:
                sections[(item.chain, item.address)] = await render_wallet(
                    request, Target(item.chain, item.address), lang
                )
            except HTTPException as exc:  # keep the report usable if one chain API is down
                sections[(item.chain, item.address)] = f"<p class='muted'>⚠ {exc.detail}</p>"
    return report.case_report(lang, case, sections)
