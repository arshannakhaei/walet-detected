"""Investigation report from the dashboard: runs in the background, then offers the page and a ZIP."""

import asyncio
import logging
import uuid
from datetime import date
from enum import Enum
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.config import PROJECT_ROOT
from app.models import Chain
from app.services.addresses import AddressError, resolve_address
from app.services.investigation_report import write_report
from app.services.links import resolve_members, split_addresses
from app.services.wallet import UnsupportedChainError

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["investigation"])

REPORTS_DIR = PROJECT_ROOT / "reports"
MAX_FOCUS = 5
KEEP = 20


class JobState(str, Enum):
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class InvestigationRequest(BaseModel):
    focus: list[str] | str = Field(description="The key wallets (1-5), a list or text.")
    addresses: list[str] | str = Field(description="The full list; the key wallets are added if missing.")
    chain: Chain | None = None
    token: str = "USDT"
    title: str | None = None
    case_date: str | None = None


class InvestigationJob(BaseModel):
    id: str
    state: JobState
    stage: str = "histories"  # histories, labels, links, profiles, verification, rendering
    done: int = 0
    total: int = 0
    error: str | None = None
    warnings: list[str] = []
    verification: dict[str, str] = {}  # "#2" -> verified / mismatch / unavailable
    report_url: str | None = None
    zip_url: str | None = None


class _Jobs:
    def __init__(self) -> None:
        self.jobs: dict[str, InvestigationJob] = {}
        self.folders: dict[str, Path] = {}
        self.tasks: dict[str, asyncio.Task] = {}


def _jobs(request: Request) -> _Jobs:
    state = request.app.state
    if not hasattr(state, "investigations"):
        state.investigations = _Jobs()
    return state.investigations


@router.post("/investigation", response_model=InvestigationJob)
async def start_investigation(body: InvestigationRequest, request: Request) -> InvestigationJob:
    supported = request.app.state.providers.supported_chains
    try:
        focus_raw = split_addresses(body.focus)
        if not 1 <= len(focus_raw) <= MAX_FOCUS:
            raise ValueError(f"give 1 to {MAX_FOCUS} key wallets")
        chain, members = resolve_members(split_addresses(body.addresses) + focus_raw, body.chain, supported)
        focus = [resolve_address(a, chain, supported)[1] for a in focus_raw]
    except UnsupportedChainError as exc:
        raise HTTPException(501, str(exc)) from exc
    except (ValueError, AddressError) as exc:
        raise HTTPException(400, str(exc)) from exc

    jobs = _jobs(request)
    job = InvestigationJob(id=uuid.uuid4().hex[:12], state=JobState.RUNNING, total=len(members))
    folder = REPORTS_DIR / f"{date.today().isoformat()}-{job.id}"
    jobs.jobs[job.id], jobs.folders[job.id] = job, folder
    investigator = request.app.state.services.investigator

    def progress(stage: str, done: int, total: int) -> None:
        job.stage, job.done, job.total = stage, done, total

    async def run() -> None:
        try:
            inv = await investigator.run(chain, focus, members, token=body.token.strip() or "USDT", progress=progress)
            job.stage, job.done, job.total = "rendering", 0, 1
            # Drawing the graphs is CPU work: keep the server responsive meanwhile.
            await asyncio.to_thread(write_report, inv, folder, body.title or None, body.case_date or None)
            job.warnings = inv.warnings
            job.verification = {f"#{p.index}": p.verification.status for p in inv.focus if p.verification}
            job.report_url = f"/api/investigation/{job.id}/files/report.html"
            job.zip_url = f"/api/investigation/{job.id}/report.zip"
            job.done, job.state = 1, JobState.DONE
        except Exception as exc:  # shown in the dashboard
            log.exception("investigation report failed")
            job.error, job.state = str(exc), JobState.FAILED

    jobs.tasks[job.id] = asyncio.create_task(run())
    while len(jobs.jobs) > KEEP:
        old = next(iter(jobs.jobs))
        jobs.jobs.pop(old)
        jobs.folders.pop(old, None)
        task = jobs.tasks.pop(old, None)
        if task is not None and not task.done():
            task.cancel()
    return job


def _done(request: Request, job_id: str) -> Path:
    jobs = _jobs(request)
    job = jobs.jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "report not found (the server may have restarted); run it again")
    if job.state != JobState.DONE:
        raise HTTPException(409, "the report is not ready yet")
    return jobs.folders[job_id]


@router.get("/investigation/{job_id}", response_model=InvestigationJob)
async def get_investigation(job_id: str, request: Request) -> InvestigationJob:
    job = _jobs(request).jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "report not found (the server may have restarted); run it again")
    return job


@router.get("/investigation/{job_id}/report.zip")
async def investigation_zip(job_id: str, request: Request) -> FileResponse:
    folder = _done(request, job_id)
    archive = folder.parent / f"ChainTrace-Report-{folder.name}.zip"
    if not archive.is_file():
        raise HTTPException(404, "archive missing")
    return FileResponse(archive, media_type="application/zip", filename=f"ChainTrace-Report-{folder.name[:10]}.zip")


@router.get("/investigation/{job_id}/files/{path:path}")
async def investigation_file(job_id: str, path: str, request: Request) -> FileResponse:
    """The report page and the files it refers to (graphs/, data/)."""
    folder = _done(request, job_id).resolve()
    file = (folder / path).resolve()
    if folder not in file.parents or not file.is_file():
        raise HTTPException(404)
    return FileResponse(file)
