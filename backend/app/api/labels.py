"""Address labels."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.models import Chain
from app.services.addresses import detect_chains, normalize_address
from app.services.labels import Label, LabelCategory, LabelService

router = APIRouter(prefix="/api", tags=["labels"])


class LabelIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    category: LabelCategory
    note: str | None = Field(None, max_length=2000)


@router.get("/labels", response_model=list[Label])
async def list_labels(request: Request, chain: Chain | None = None) -> list[Label]:
    return request.app.state.labels.all(chain)


@router.put("/labels/{chain}/{address}", response_model=Label)
async def set_label(chain: Chain, address: str, body: LabelIn, request: Request) -> Label:
    if chain not in detect_chains(address.strip()):
        raise HTTPException(400, f"address is not a valid {chain.value} address")
    labels: LabelService = request.app.state.labels
    return await labels.set(chain, normalize_address(chain, address), body.name, body.category, body.note)


@router.delete("/labels/{chain}/{address}", status_code=204)
async def delete_label(chain: Chain, address: str, request: Request) -> None:
    if not await request.app.state.labels.remove(chain, normalize_address(chain, address)):
        raise HTTPException(404, "no user label for this address")
