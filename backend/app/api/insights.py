"""Risk analysis, activity timeline, CSV exports and printable wallet reports."""

import csv
import io

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from app.api.deps import Target, call, normalized_filter, target, transfer_filter
from app.services import report
from app.services.risk import RiskAnalyzer, RiskReport, timeline
from app.services.wallet import TransferFilter, WalletService

router = APIRouter(prefix="/api", tags=["insights"])


class TimelineRow(BaseModel):
    period: str
    token_symbol: str
    token_contract: str | None
    amount_in: str
    amount_out: str
    count_in: int
    count_out: int


@router.get("/wallet/{address}/risk", response_model=RiskReport)
async def risk(
    request: Request,
    t: Target = Depends(target),
    deep: bool = Query(False, description="Also scan addresses two hops away (slower)"),
) -> RiskReport:
    analyzer: RiskAnalyzer = request.app.state.risk
    return await call(analyzer.analyze(t.chain, t.address, deep))


@router.get("/wallet/{address}/timeline", response_model=list[TimelineRow])
async def wallet_timeline(
    request: Request,
    t: Target = Depends(target),
    bucket: str = Query("day", pattern="^(day|week|month)$"),
    token: str | None = None,
) -> list[TimelineRow]:
    wallets: WalletService = request.app.state.wallet_service
    transfers, _ = await call(wallets.load_transfers(t.chain, t.address))
    rows = timeline(t.address, transfers, bucket, token)
    return [TimelineRow(**{**r, "amount_in": str(r["amount_in"]), "amount_out": str(r["amount_out"])}) for r in rows]


def _csv(rows: list[list], header: list[str], filename: str) -> StreamingResponse:
    buf = io.StringIO()
    buf.write("﻿")  # BOM so Excel opens UTF-8 correctly
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows(rows)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/wallet/{address}/transfers.csv")
async def transfers_csv(
    request: Request,
    t: Target = Depends(target),
    flt: TransferFilter = Depends(transfer_filter),
):
    wallets: WalletService = request.app.state.wallet_service
    items, _ = await call(wallets.transfers(t.chain, t.address, normalized_filter(flt, t.chain), 100_000, 0))
    rows = [
        [
            v.transfer.timestamp.isoformat(),
            v.transfer.tx_hash,
            v.direction.value,
            v.transfer.from_address,
            v.transfer.to_address,
            str(v.transfer.amount),
            v.transfer.token_symbol,
            v.transfer.token_contract or "",
            "ok" if v.transfer.success else "failed",
        ]
        for v in items
    ]
    header = ["time_utc", "tx_hash", "direction", "from", "to", "amount", "token", "token_contract", "status"]
    return _csv(rows, header, f"{t.chain.value}_{t.address[:12]}_transfers.csv")


@router.get("/wallet/{address}/counterparties.csv")
async def counterparties_csv(
    request: Request,
    t: Target = Depends(target),
    flt: TransferFilter = Depends(transfer_filter),
):
    wallets: WalletService = request.app.state.wallet_service
    labels = request.app.state.labels
    cps = await call(wallets.counterparties(t.chain, t.address, normalized_filter(flt, t.chain)))
    rows = []
    for c in cps:
        label = labels.get(t.chain, c.address)
        rows.append(
            [
                c.address,
                label.name if label else "",
                label.category.value if label else "",
                c.token_symbol,
                str(c.received_from),
                c.count_in,
                str(c.sent_to),
                c.count_out,
                c.first_seen.isoformat(),
                c.last_seen.isoformat(),
            ]
        )
    header = [
        "address", "label", "category", "token", "received_from", "count_in",
        "sent_to", "count_out", "first_seen", "last_seen",
    ]
    return _csv(rows, header, f"{t.chain.value}_{t.address[:12]}_counterparties.csv")


async def render_wallet(request: Request, t: Target, lang: str) -> str:
    wallets: WalletService = request.app.state.wallet_service
    analyzer: RiskAnalyzer = request.app.state.risk
    labels = request.app.state.labels
    overview = await call(wallets.overview(t.chain, t.address))
    risk_report = await call(analyzer.analyze(t.chain, t.address))
    cps = await call(wallets.counterparties(t.chain, t.address, TransferFilter()))
    names = {
        a: labels.get(t.chain, a).name
        for a in [t.address] + [c.address for c in cps[:15]]
        if labels.get(t.chain, a)
    }
    return report.wallet_section(lang, overview, risk_report, cps, names)


@router.get("/wallet/{address}/report", response_class=HTMLResponse)
async def wallet_report(
    request: Request, t: Target = Depends(target), lang: str = Query("fa", pattern="^(fa|en)$")
) -> str:
    body = await render_wallet(request, t, lang)
    return report.page(lang, f"{report.T[lang]['wallet_report']}: {t.address}", body)
