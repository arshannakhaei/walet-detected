"""Shared request dependencies: chain/address resolution, filters, error mapping."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from fastapi import HTTPException, Path, Query, Request

from app.models import Chain, Direction
from app.providers import ProviderError
from app.services.addresses import detect_chains, normalize_address
from app.services.wallet import TransferFilter, UnsupportedChainError


@dataclass
class Target:
    chain: Chain
    address: str  # canonical form


def resolve(request: Request, address: str, chain: Chain | None) -> Target:
    address = address.strip()
    candidates = detect_chains(address)
    if chain is not None:
        if chain not in candidates:
            raise HTTPException(400, f"address is not a valid {chain.value} address")
    elif not candidates:
        raise HTTPException(400, "unrecognized address format")
    else:
        # Prefer a chain we can actually query (an EVM address fits several).
        supported = request.app.state.providers.supported_chains
        chain = next((c for c in candidates if c in supported), candidates[0])
    return Target(chain, normalize_address(chain, address))


def target(request: Request, address: str = Path(), chain: Chain | None = None) -> Target:
    return resolve(request, address, chain)


def transfer_filter(
    token: str | None = None,
    direction: Direction | None = None,
    min_amount: Decimal | None = None,
    max_amount: Decimal | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    counterparty: str | None = Query(None, description="Only transfers with this address"),
    include_failed: bool = False,
    hide_spam: bool = Query(True, description="Hide zero-value and fake look-alike token transfers"),
) -> TransferFilter:
    return TransferFilter(
        token=token,
        direction=direction,
        min_amount=min_amount,
        max_amount=max_amount,
        start=start,
        end=end,
        counterparty=counterparty.strip() if counterparty else None,
        include_failed=include_failed,
        hide_spam=hide_spam,
    )


def normalized_filter(flt: TransferFilter, chain: Chain) -> TransferFilter:
    if flt.counterparty:
        flt.counterparty = normalize_address(chain, flt.counterparty)
    if flt.token and flt.token.startswith("0x"):
        flt.token = flt.token.lower()
    return flt


async def call(coro):
    """Await a service call, turning service errors into HTTP errors."""
    try:
        return await coro
    except UnsupportedChainError as exc:
        raise HTTPException(501, str(exc)) from exc
    except ProviderError as exc:
        raise HTTPException(502, str(exc)) from exc
