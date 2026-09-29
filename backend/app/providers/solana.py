"""Solana via JSON-RPC (public endpoint, or a free Helius/QuickNode URL).

Solana has no "transfers by address" call: we list the address's transaction
signatures, then fetch each transaction (jsonParsed) and pick out SOL system
transfers and SPL token transfers. That is one request per transaction, so the
default history limit for Solana is lower than for other chains.

SPL tokens move between token accounts, not wallets; the owner wallet of each
token account is read from the transaction's pre/post token balances.
"""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import httpx

from app.models import Chain, TokenBalance, Transfer
from app.providers.base import ChainProvider, ProviderError, TransferPage
from app.providers.ratelimit import RateLimiter
from app.services.tokens import known_symbol

LAMPORTS = 9
TOKEN_PROGRAMS = ("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA", "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb")


def _mint_symbol(mint: str) -> str:
    return known_symbol(Chain.SOLANA, mint) or f"{mint[:4]}…{mint[-4:]}"


def _instructions(tx: dict) -> list[dict]:
    message = (tx.get("transaction") or {}).get("message") or {}
    out = list(message.get("instructions") or [])
    for inner in (tx.get("meta") or {}).get("innerInstructions") or []:
        out.extend(inner.get("instructions") or [])
    return out


def parse_sol_tx(tx: dict, signature: str) -> list[Transfer]:
    meta = tx.get("meta") or {}
    success = meta.get("err") is None
    ts = datetime.fromtimestamp(tx.get("blockTime") or 0, tz=timezone.utc)
    keys = [
        k["pubkey"] if isinstance(k, dict) else k
        for k in ((tx.get("transaction") or {}).get("message") or {}).get("accountKeys") or []
    ]
    # token account -> (owner, mint, decimals)
    token_accounts: dict[str, tuple[str, str, int]] = {}
    for bal in (meta.get("preTokenBalances") or []) + (meta.get("postTokenBalances") or []):
        idx = bal.get("accountIndex")
        if idx is not None and idx < len(keys) and bal.get("owner"):
            decimals = int((bal.get("uiTokenAmount") or {}).get("decimals") or 0)
            token_accounts[keys[idx]] = (bal["owner"], bal.get("mint", ""), decimals)

    transfers: list[Transfer] = []
    for n, ins in enumerate(_instructions(tx)):
        parsed = ins.get("parsed")
        if not isinstance(parsed, dict):
            continue
        kind, info = parsed.get("type"), parsed.get("info") or {}
        program = ins.get("program")
        if program == "system" and kind == "transfer":
            transfers.append(
                Transfer(
                    chain=Chain.SOLANA,
                    transfer_id=f"{signature}:{n}",
                    tx_hash=signature,
                    timestamp=ts,
                    from_address=info["source"],
                    to_address=info["destination"],
                    amount=Decimal(int(info["lamports"])).scaleb(-LAMPORTS),
                    token_symbol="SOL",
                    token_decimals=LAMPORTS,
                    success=success,
                )
            )
        elif program in ("spl-token", "spl-token-2022") and kind in ("transfer", "transferChecked"):
            src = token_accounts.get(info.get("source"))
            dst = token_accounts.get(info.get("destination"))
            if src is None and dst is None:
                continue
            mint = info.get("mint") or (src or dst)[1]
            if "tokenAmount" in info:
                raw = int(info["tokenAmount"]["amount"])
                decimals = int(info["tokenAmount"]["decimals"])
            else:
                raw = int(info["amount"])
                decimals = (src or dst)[2]
            transfers.append(
                Transfer(
                    chain=Chain.SOLANA,
                    transfer_id=f"{signature}:{n}",
                    tx_hash=signature,
                    timestamp=ts,
                    # Fall back to the signing authority when the source account closed.
                    from_address=src[0] if src else info.get("authority", info["source"]),
                    to_address=dst[0] if dst else info["destination"],
                    amount=Decimal(raw).scaleb(-decimals),
                    token_symbol=_mint_symbol(mint),
                    token_contract=mint,
                    token_decimals=decimals,
                    success=success,
                )
            )
    return transfers


class SolanaProvider(ChainProvider):
    chain = Chain.SOLANA

    def __init__(self, client: httpx.AsyncClient, limiter: RateLimiter, rpc_url: str, concurrency: int = 4):
        super().__init__(client, limiter)
        self._url = rpc_url
        self._sem = asyncio.Semaphore(concurrency)

    async def _rpc(self, method: str, params: list):
        data = await self._post_json(self._url, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        if data.get("error"):
            raise ProviderError(f"solana: {method}: {data['error'].get('message')}")
        return data.get("result")

    async def get_transfers(self, address: str, max_items: int) -> TransferPage:
        signatures: list[dict] = []
        before: str | None = None
        truncated = False
        while True:
            opts = {"limit": min(1000, max_items)}
            if before:
                opts["before"] = before
            batch = await self._rpc("getSignaturesForAddress", [address, opts])
            signatures.extend(batch)
            if len(batch) < opts["limit"]:
                break
            if len(signatures) >= max_items:
                truncated = True
                break
            before = batch[-1]["signature"]
        signatures = signatures[:max_items]

        async def fetch(sig: str) -> list[Transfer]:
            async with self._sem:
                tx = await self._rpc(
                    "getTransaction",
                    [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
                )
            return parse_sol_tx(tx, sig) if tx else []

        results = await asyncio.gather(*(fetch(s["signature"]) for s in signatures))
        transfers = [t for batch in results for t in batch if address in (t.from_address, t.to_address)]
        transfers.sort(key=lambda t: t.timestamp, reverse=True)
        return TransferPage(transfers, truncated)

    async def get_balances(self, address: str) -> list[TokenBalance]:
        lamports = await self._rpc("getBalance", [address])
        balances = [TokenBalance(token_symbol="SOL", amount=Decimal(int(lamports["value"])).scaleb(-LAMPORTS))]
        for program in TOKEN_PROGRAMS:
            result = await self._rpc(
                "getTokenAccountsByOwner", [address, {"programId": program}, {"encoding": "jsonParsed"}]
            )
            for acc in result.get("value") or []:
                info = acc["account"]["data"]["parsed"]["info"]
                amount = Decimal(info["tokenAmount"].get("uiAmountString") or "0")
                if amount:
                    balances.append(
                        TokenBalance(token_symbol=_mint_symbol(info["mint"]), token_contract=info["mint"], amount=amount)
                    )
        return balances
