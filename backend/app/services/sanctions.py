"""Live sanctions and freeze checks that need no sign-up.

- Chainalysis Sanctions Oracle: a free smart contract on the main EVM chains
  (`isSanctioned(address)`), kept up to date by Chainalysis with US, EU and UN
  sanctions designations. Read with one `eth_call` (Alchemy when a key is set,
  otherwise a public RPC).
- Tether's freeze list: the USDT contracts on Tron and Ethereum answer
  `isBlackListed(address)`. Tether freezes wallets on request of law
  enforcement, typically for scams and hacks, so for USDT investigations this
  is a strong signal.

The built-in OFAC list (data/labels/*.ofac.json) is applied separately through
labels. Results are cached; a source that cannot be reached gives None
("unknown"), never "clean".
"""

import asyncio
import logging
import time

import httpx
from pydantic import BaseModel

from app.models import Chain
from app.services.addresses import tron_base58_to_hex

log = logging.getLogger(__name__)

ORACLE = "0x40c57923924b5c5c5455c48d93317139addac8fb"
SEL_IS_SANCTIONED = "df592f7d"  # keccak("isSanctioned(address)")[:4]
SEL_IS_BLACKLISTED = "e47d6060"  # keccak("isBlackListed(address)")[:4]
USDT_TRON = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
USDT_ETHEREUM = "0xdac17f958d2ee523a2206206994597c13d831ec7"

PUBLIC_RPC = {
    Chain.ETHEREUM: "https://ethereum-rpc.publicnode.com",
    Chain.BSC: "https://bsc-rpc.publicnode.com",
    Chain.POLYGON: "https://polygon-bor-rpc.publicnode.com",
    Chain.ARBITRUM: "https://arbitrum-one-rpc.publicnode.com",
    Chain.OPTIMISM: "https://optimism-rpc.publicnode.com",
    Chain.BASE: "https://base-rpc.publicnode.com",
    Chain.AVALANCHE: "https://avalanche-c-chain-rpc.publicnode.com",
}
ALCHEMY_NETWORKS = {
    Chain.ETHEREUM: "eth-mainnet",
    Chain.BSC: "bnb-mainnet",
    Chain.POLYGON: "polygon-mainnet",
    Chain.ARBITRUM: "arb-mainnet",
    Chain.OPTIMISM: "opt-mainnet",
    Chain.BASE: "base-mainnet",
    Chain.AVALANCHE: "avax-mainnet",
}
CACHE_SECONDS = 6 * 3600


class SanctionStatus(BaseModel):
    chain: Chain
    address: str
    sanctioned: bool | None = None  # Chainalysis oracle; None = not checked / unknown
    usdt_frozen: bool | None = None  # Tether freeze list; None = not checked / unknown

    @property
    def flagged(self) -> bool:
        return bool(self.sanctioned or self.usdt_frozen)


def _word(hex_address: str) -> str:
    """A 20-byte address as a 32-byte ABI word."""
    return hex_address.lower().removeprefix("0x").rjust(64, "0")


def _bool_word(value: str | None) -> bool | None:
    value = (value or "").removeprefix("0x")
    if len(value) < 64:
        return None  # no contract at that address, or an error
    return int(value[:64], 16) != 0


class SanctionsChecker:
    def __init__(
        self,
        client: httpx.AsyncClient,
        trongrid_url: str = "https://api.trongrid.io",
        trongrid_key: str = "",
        alchemy_key: str = "",
        rpc_urls: dict[Chain, str] | None = None,
        offline: dict[tuple[Chain, str], SanctionStatus] | None = None,
        concurrency: int = 4,
    ):
        self._client = client
        self._trongrid = trongrid_url.rstrip("/")
        self.trongrid_key = trongrid_key
        self.alchemy_key = alchemy_key
        self._rpc_override = rpc_urls or {}
        # Demo mode: fixed answers, no network.
        self._offline = offline
        self._sem = asyncio.Semaphore(concurrency)
        self._cache: dict[tuple[Chain, str], tuple[float, SanctionStatus]] = {}

    def rpc_url(self, chain: Chain) -> str | None:
        if chain in self._rpc_override:
            return self._rpc_override[chain]
        if self.alchemy_key and chain in ALCHEMY_NETWORKS:
            return f"https://{ALCHEMY_NETWORKS[chain]}.g.alchemy.com/v2/{self.alchemy_key}"
        return PUBLIC_RPC.get(chain)

    def supports(self, chain: Chain) -> bool:
        return chain == Chain.TRON or chain in PUBLIC_RPC or chain in self._rpc_override

    async def _eth_call(self, chain: Chain, to: str, data: str) -> bool | None:
        url = self.rpc_url(chain)
        if url is None:
            return None
        try:
            resp = await self._client.post(
                url,
                json={"jsonrpc": "2.0", "id": 1, "method": "eth_call", "params": [{"to": to, "data": data}, "latest"]},
            )
            resp.raise_for_status()
            return _bool_word(resp.json().get("result"))
        except (httpx.HTTPError, ValueError) as exc:
            log.info("sanctions: %s eth_call failed: %s", chain.value, type(exc).__name__)
            return None

    async def _tron_frozen(self, address: str) -> bool | None:
        try:
            param = _word(tron_base58_to_hex(address)[2:])  # drop the 0x41 Tron prefix
        except ValueError:
            return None
        headers = {"TRON-PRO-API-KEY": self.trongrid_key} if self.trongrid_key else {}
        try:
            resp = await self._client.post(
                f"{self._trongrid}/wallet/triggerconstantcontract",
                json={
                    "owner_address": address,
                    "contract_address": USDT_TRON,
                    "function_selector": "isBlackListed(address)",
                    "parameter": param,
                    "visible": True,
                },
                headers=headers,
            )
            resp.raise_for_status()
            results = resp.json().get("constant_result") or []
            return _bool_word(results[0]) if results else None
        except (httpx.HTTPError, ValueError) as exc:
            log.info("sanctions: Tron USDT freeze check failed: %s", type(exc).__name__)
            return None

    async def check(self, chain: Chain, address: str) -> SanctionStatus:
        if self._offline is not None:
            return self._offline.get((chain, address), SanctionStatus(chain=chain, address=address))
        key = (chain, address)
        cached = self._cache.get(key)
        if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
            return cached[1]
        status = SanctionStatus(chain=chain, address=address)
        async with self._sem:
            if chain == Chain.TRON:
                status.usdt_frozen = await self._tron_frozen(address)
            elif chain in PUBLIC_RPC or chain in self._rpc_override:
                word = _word(address)
                calls = [self._eth_call(chain, ORACLE, "0x" + SEL_IS_SANCTIONED + word)]
                if chain == Chain.ETHEREUM:
                    calls.append(self._eth_call(chain, USDT_ETHEREUM, "0x" + SEL_IS_BLACKLISTED + word))
                results = await asyncio.gather(*calls)
                status.sanctioned = results[0]
                if chain == Chain.ETHEREUM:
                    status.usdt_frozen = results[1]
        # Only remember answers; retry unknowns next time.
        if status.sanctioned is not None or status.usdt_frozen is not None:
            self._cache[key] = (time.monotonic(), status)
        return status

    async def check_many(self, chain: Chain, addresses: list[str]) -> list[SanctionStatus]:
        return list(await asyncio.gather(*(self.check(chain, a) for a in addresses)))
