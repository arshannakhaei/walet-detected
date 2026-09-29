"""Bitcoin via an Esplora API (mempool.space or Blockstream).

Bitcoin has no account-to-account transfers: a transaction spends inputs and
creates outputs. To fit the common Transfer model, each transaction is turned
into edges between the watched address and its counterparties:

* the address is an input (it pays): one transfer to every output that does
  not go back to an input address (i.e. not change), scaled by the address's
  share of the inputs when several addresses co-spend;
* the address only receives: one transfer from every input address, scaled by
  that input's share of the total input value.
"""

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal

import httpx

from app.models import Chain, TokenBalance, Transfer
from app.providers.base import ChainProvider, TransferPage
from app.providers.ratelimit import RateLimiter

SATS = Decimal(100_000_000)
PAGE = 25  # Esplora returns confirmed history 25 transactions at a time


def _sum_by_address(entries: list[dict]) -> dict[str, int]:
    totals: dict[str, int] = defaultdict(int)
    for entry in entries:
        if entry and entry.get("scriptpubkey_address"):
            totals[entry["scriptpubkey_address"]] += int(entry.get("value") or 0)
    return totals


def parse_btc_tx(tx: dict, address: str) -> list[Transfer]:
    status = tx.get("status") or {}
    ts = datetime.fromtimestamp(status.get("block_time") or 0, tz=timezone.utc)
    if not status.get("confirmed"):
        ts = datetime.now(timezone.utc)
    inputs = _sum_by_address([vin.get("prevout") or {} for vin in tx.get("vin") or []])
    outputs = _sum_by_address(tx.get("vout") or [])
    total_in = sum(inputs.values())

    edges: list[tuple[str, str, Decimal]] = []
    if address in inputs and total_in:
        share = Decimal(inputs[address]) / Decimal(total_in)
        for out_addr, value in outputs.items():
            if out_addr in inputs:
                continue  # change back to a spender
            edges.append((address, out_addr, Decimal(value) * share))
    elif address in outputs and total_in:
        received = Decimal(outputs[address])
        for in_addr, value in inputs.items():
            edges.append((in_addr, address, received * Decimal(value) / Decimal(total_in)))

    return [
        Transfer(
            chain=Chain.BITCOIN,
            transfer_id=f"{tx['txid']}:{frm}:{to}",
            tx_hash=tx["txid"],
            timestamp=ts,
            from_address=frm,
            to_address=to,
            amount=(sats / SATS).quantize(Decimal("0.00000001")),
            token_symbol="BTC",
            token_decimals=8,
        )
        for frm, to, sats in edges
        if sats > 0
    ]


class BitcoinProvider(ChainProvider):
    chain = Chain.BITCOIN

    def __init__(self, client: httpx.AsyncClient, limiter: RateLimiter, base_url: str):
        super().__init__(client, limiter)
        self._base = base_url.rstrip("/")

    async def get_transfers(self, address: str, max_items: int) -> TransferPage:
        txs: list[dict] = list(await self._get_json(f"{self._base}/address/{address}/txs/mempool"))
        confirmed: list[dict] = []
        last_seen: str | None = None
        truncated = False
        while True:
            path = f"/address/{address}/txs/chain" + (f"/{last_seen}" if last_seen else "")
            batch = await self._get_json(f"{self._base}{path}")
            confirmed.extend(batch)
            if len(batch) < PAGE:
                break
            # Each tx yields at least one transfer, so this bounds the transfer count too.
            if len(confirmed) >= max_items:
                truncated = True
                break
            last_seen = batch[-1]["txid"]
        transfers = [t for tx in txs + confirmed for t in parse_btc_tx(tx, address)]
        transfers.sort(key=lambda t: t.timestamp, reverse=True)
        return TransferPage(transfers, truncated)

    async def get_balances(self, address: str) -> list[TokenBalance]:
        data = await self._get_json(f"{self._base}/address/{address}")
        sats = 0
        for key in ("chain_stats", "mempool_stats"):
            stats = data.get(key) or {}
            sats += int(stats.get("funded_txo_sum", 0)) - int(stats.get("spent_txo_sum", 0))
        return [TokenBalance(token_symbol="BTC", amount=Decimal(sats) / SATS)]
