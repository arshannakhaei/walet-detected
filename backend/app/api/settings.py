"""Read and change API keys from the dashboard (stored in .env, applied without a restart)."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.config import update_env_file

router = APIRouter(prefix="/api/settings", tags=["settings"])

# Setting name -> .env variable. Only these can be changed from the dashboard.
EDITABLE = {
    "trongrid_api_key": "TRONGRID_API_KEY",
    "etherscan_api_key": "ETHERSCAN_API_KEY",
    "coingecko_api_key": "COINGECKO_API_KEY",
    "solana_rpc_url": "SOLANA_RPC_URL",
}
LOCAL_CLIENTS = {"127.0.0.1", "::1", "localhost", "testclient"}


class KeyStatus(BaseModel):
    trongrid_api_key: bool
    etherscan_api_key: bool
    coingecko_api_key: bool
    solana_rpc_url: str
    demo_mode: bool
    tron_requests_per_second: float
    evm_requests_per_second: float
    can_edit: bool = Field(description="Keys can be changed only from the computer running the app.")


class KeysIn(BaseModel):
    trongrid_api_key: str | None = Field(None, max_length=200)
    etherscan_api_key: str | None = Field(None, max_length=200)
    coingecko_api_key: str | None = Field(None, max_length=200)
    solana_rpc_url: str | None = Field(None, max_length=500)


def _is_local(request: Request) -> bool:
    return (request.client.host if request.client else "") in LOCAL_CLIENTS


def _status(request: Request) -> KeyStatus:
    s = request.app.state.services.settings
    return KeyStatus(
        trongrid_api_key=bool(s.trongrid_api_key),
        etherscan_api_key=bool(s.etherscan_api_key),
        coingecko_api_key=bool(s.coingecko_api_key),
        solana_rpc_url=s.solana_rpc_url,
        demo_mode=s.demo_mode,
        tron_requests_per_second=s.tron_rate,
        evm_requests_per_second=s.evm_rate,
        can_edit=_is_local(request),
    )


@router.get("", response_model=KeyStatus)
async def get_settings(request: Request) -> KeyStatus:
    return _status(request)


@router.put("/keys", response_model=KeyStatus)
async def set_keys(body: KeysIn, request: Request) -> KeyStatus:
    if not _is_local(request):
        raise HTTPException(403, "API keys can only be changed on the computer running ChainTrace")
    changes = {k: v.strip() for k, v in body.model_dump().items() if v is not None}
    if "solana_rpc_url" in changes and changes["solana_rpc_url"] and not changes["solana_rpc_url"].startswith("https://"):
        raise HTTPException(400, "Solana RPC URL must start with https://")
    if not changes:
        return _status(request)
    services = request.app.state.services
    settings = services.settings
    for name, value in changes.items():
        if name == "solana_rpc_url" and not value:
            continue  # keep the current RPC rather than an empty one
        setattr(settings, name, value)
    update_env_file({EDITABLE[k]: v for k, v in changes.items() if not (k == "solana_rpc_url" and not v)}, services.env_file)
    services.providers.reload(settings)
    services.prices.set_api_key(settings.coingecko_api_key)
    return _status(request)
