"""Chain-agnostic data models shared by providers, services and the API."""

from datetime import datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field


class Chain(str, Enum):
    TRON = "tron"
    ETHEREUM = "ethereum"
    BSC = "bsc"
    POLYGON = "polygon"
    ARBITRUM = "arbitrum"
    OPTIMISM = "optimism"
    BASE = "base"
    AVALANCHE = "avalanche"
    BITCOIN = "bitcoin"
    SOLANA = "solana"


class Direction(str, Enum):
    IN = "in"
    OUT = "out"
    SELF = "self"


class Transfer(BaseModel):
    """A single value movement from one address to another.

    One on-chain transaction may produce several transfers (e.g. a contract
    call that moves several tokens), so `transfer_id` rather than `tx_hash`
    is the unique key.
    """

    chain: Chain
    transfer_id: str
    tx_hash: str
    timestamp: datetime
    from_address: str
    to_address: str
    amount: Decimal
    token_symbol: str
    token_contract: str | None = None  # None for the chain's native coin
    token_decimals: int
    success: bool = True

    def direction_for(self, address: str) -> Direction:
        if self.from_address == address and self.to_address == address:
            return Direction.SELF
        return Direction.OUT if self.from_address == address else Direction.IN

    def counterparty_for(self, address: str) -> str:
        return self.to_address if self.from_address == address else self.from_address


class TokenBalance(BaseModel):
    token_symbol: str
    token_contract: str | None = None
    amount: Decimal
    usd_value: Decimal | None = None
    # True when computed from transfer history (in - out) rather than read from the chain.
    derived: bool = False


class TokenFlow(BaseModel):
    token_symbol: str
    token_contract: str | None = None
    total_in: Decimal = Decimal(0)
    total_out: Decimal = Decimal(0)
    count_in: int = 0
    count_out: int = 0
    usd_in: Decimal | None = None  # at today's price
    usd_out: Decimal | None = None


class WalletOverview(BaseModel):
    chain: Chain
    address: str
    balances: list[TokenBalance]
    total_usd: Decimal | None = None
    first_seen: datetime | None
    last_seen: datetime | None
    transfer_count: int
    counterparty_count: int
    flows: list[TokenFlow]
    truncated: bool = Field(
        description="True when the wallet has more history than the fetch limit allows."
    )
    loading_more: bool = Field(False, description="The rest of the history is still downloading.")


class Counterparty(BaseModel):
    address: str
    token_symbol: str
    token_contract: str | None = None
    received_from: Decimal = Decimal(0)  # amount the wallet received from this counterparty
    sent_to: Decimal = Decimal(0)  # amount the wallet sent to this counterparty
    count_in: int = 0
    count_out: int = 0
    first_seen: datetime
    last_seen: datetime


class TransferView(BaseModel):
    """A transfer as seen from one wallet's point of view."""

    transfer: Transfer
    direction: Direction
    counterparty: str
