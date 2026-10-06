"""Check every API key in .env against the real service. Run:  python scripts/check_keys.py  (or check_keys.bat)

Each key is tried with one small request and reported as OK / FAIL (with the
reason) / not set. Keys are never printed: only their first 4 characters, so
the output can be shared safely when asking for help.

Also warns about common .env mistakes: a misspelled variable name, a key on a
commented-out line, spaces inside a value.
"""

from __future__ import annotations

import asyncio
import difflib
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
USDT_TRON = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
ZERO_EVM = "0x0000000000000000000000000000000000000000"
PUBLIC_SOLANA = "https://api.mainnet-beta.solana.com"

URLS = {
    "trongrid": "https://api.trongrid.io",
    "tronscan": "https://apilist.tronscanapi.com/api",
    "etherscan": "https://api.etherscan.io/v2/api",
    "coingecko": "https://api.coingecko.com/api/v3",
    "chainalysis": "https://public.chainalysis.com/api/v1",
    "telegram": "https://api.telegram.org",
    "nobitex": "https://api.nobitex.ir",
    "wallex": "https://api.wallex.ir",
}

# Every variable ChainTrace reads, plus keys collected for features still to come.
KNOWN = {
    "HOST", "PORT", "DEMO_MODE", "SETTINGS_FROM_ANY_CLIENT", "DATABASE_URL",
    "TRONGRID_BASE_URL", "TRONGRID_API_KEY", "TRON_REQUESTS_PER_SECOND", "TRONSCAN_BASE_URL",
    "ETHERSCAN_API_KEY", "ETHERSCAN_BASE_URL", "EVM_REQUESTS_PER_SECOND",
    "BITCOIN_API_URL", "BITCOIN_REQUESTS_PER_SECOND", "BITCOIN_MAX_TRANSACTIONS",
    "SOLANA_RPC_URL", "SOLANA_REQUESTS_PER_SECOND", "SOLANA_MAX_TRANSACTIONS", "ALCHEMY_API_KEY",
    "COINGECKO_BASE_URL", "COINGECKO_API_KEY", "USD_TOMAN_RATE",
    "NOBITEX_BASE_URL", "WALLEX_BASE_URL", "BINANCE_BASE_URL",
    "MAX_TRANSFERS_PER_ADDRESS", "PAGE_SIZE", "HUB_THRESHOLD", "CACHE_TTL_SECONDS",
    "HTTP_TIMEOUT_SECONDS", "MONITOR_INTERVAL_SECONDS",
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_USERS", "PUBLIC_URL",
    # collected now, used by upcoming features
    "TRONSCAN_API_KEY", "CHAINALYSIS_API_KEY", "ALCHEMY_API_KEY", "BITQUERY_API_KEY", "ARKHAM_API_KEY",
}
FUTURE = {"BITQUERY_API_KEY": "Bitquery", "ARKHAM_API_KEY": "Arkham"}
ORACLE = "0x40c57923924b5c5c5455c48d93317139addac8fb"
PUBLIC_ETH_RPC = "https://ethereum-rpc.publicnode.com"


@dataclass
class Result:
    service: str
    variable: str
    status: str  # "ok", "fail", "unset", "info"
    detail: str
    shown: str = ""  # masked value


def mask(value: str) -> str:
    return f"{value[:4]}..." if len(value) > 4 else "****"


def read_env(path: Path) -> tuple[dict[str, str], list[str]]:
    """Values from .env (quotes stripped) and warnings about likely mistakes."""
    values: dict[str, str] = {}
    warnings: list[str] = []
    if not path.exists():
        return values, [f"{path.name} not found next to the app (copy .env.example to .env)"]
    for n, raw in enumerate(path.read_text(encoding="utf-8-sig", errors="replace").splitlines(), start=1):
        line = raw.strip()
        if not line or "=" not in line:
            continue
        commented = line.startswith("#")
        key, _, value = line.lstrip("#").strip().partition("=")
        key, value = key.strip().upper(), value.strip()
        if " " in key or not key:
            continue  # an ordinary comment
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if commented:
            if value and key in KNOWN:
                warnings.append(f"line {n}: {key} has a value but the line starts with '#', so it is ignored")
            continue
        if key not in KNOWN:
            close = difflib.get_close_matches(key, KNOWN, n=1, cutoff=0.75)
            hint = f" - did you mean {close[0]}?" if close else ""
            warnings.append(f"line {n}: {key} is not a ChainTrace setting{hint}")
        secret = key.endswith(("_KEY", "_TOKEN", "_URL"))
        if secret and any(c.isspace() for c in value):
            warnings.append(f"line {n}: {key} contains spaces; API keys never do (copy it again)")
        values[key] = value
    return values, warnings


def _reason(exc: Exception) -> str:
    # Never str(exc): request errors include the URL, which can contain a token.
    if isinstance(exc, httpx.TimeoutException):
        return "no answer (timeout) - check internet / VPN"
    if isinstance(exc, httpx.ConnectError):
        return "cannot connect - blocked network, VPN or proxy?"
    if isinstance(exc, ValueError):
        return "unexpected answer (not JSON)"
    return f"request failed ({type(exc).__name__})"


def _http(resp: httpx.Response) -> str:
    if resp.status_code in (401, 403):
        return f"key rejected (HTTP {resp.status_code})"
    if resp.status_code == 429:
        return "rate limited right now (HTTP 429) - try again in a minute"
    return f"HTTP {resp.status_code}"


async def check_trongrid(client, env, urls) -> Result:
    key = env.get("TRONGRID_API_KEY", "")
    base = env.get("TRONGRID_BASE_URL") or urls["trongrid"]
    r = Result("TronGrid", "TRONGRID_API_KEY", "unset", "not set: Tron is limited to 1 request/s", mask(key) if key else "")
    if not key:
        return r
    try:
        resp = await client.get(f"{base}/v1/accounts/{USDT_TRON}", headers={"TRON-PRO-API-KEY": key})
        if resp.status_code == 200:
            return Result(r.service, r.variable, "ok", "key accepted (about 15 requests/s)", r.shown)
        return Result(r.service, r.variable, "fail", _http(resp), r.shown)
    except Exception as exc:  # noqa: BLE001
        return Result(r.service, r.variable, "fail", _reason(exc), r.shown)


async def check_tronscan(client, env, urls) -> Result:
    key = env.get("TRONSCAN_API_KEY", "")
    r = Result("TronScan", "TRONSCAN_API_KEY", "unset", "not set (TronScan verification and exchange tags in investigation reports)", mask(key) if key else "")
    if not key:
        return r
    try:
        resp = await client.get(
            f"{urls['tronscan']}/accountv2", params={"address": USDT_TRON}, headers={"TRON-PRO-API-KEY": key}
        )
        if resp.status_code == 200:
            return Result(r.service, r.variable, "ok", "key accepted", r.shown)
        return Result(r.service, r.variable, "fail", _http(resp), r.shown)
    except Exception as exc:  # noqa: BLE001
        return Result(r.service, r.variable, "fail", _reason(exc), r.shown)


async def check_etherscan(client, env, urls) -> Result:
    key = env.get("ETHERSCAN_API_KEY", "")
    base = env.get("ETHERSCAN_BASE_URL") or urls["etherscan"]
    r = Result("Etherscan", "ETHERSCAN_API_KEY", "unset", "not set: EVM chains use slower Blockscout, BSC/Avalanche off", mask(key) if key else "")
    if not key:
        return r
    try:
        resp = await client.get(
            base,
            params={"chainid": 1, "module": "account", "action": "balance", "address": ZERO_EVM, "apikey": key},
        )
        if resp.status_code != 200:
            return Result(r.service, r.variable, "fail", _http(resp), r.shown)
        data = resp.json()
        if str(data.get("status")) == "1":
            return Result(r.service, r.variable, "ok", "key accepted (all EVM chains on the free plan)", r.shown)
        message = str(data.get("result") or data.get("message") or "rejected")[:120]
        return Result(r.service, r.variable, "fail", f"Etherscan says: {message}", r.shown)
    except Exception as exc:  # noqa: BLE001
        return Result(r.service, r.variable, "fail", _reason(exc), r.shown)


async def check_solana(client, env, urls) -> Result:
    url = env.get("SOLANA_RPC_URL", "")
    if not url or url.rstrip("/") == PUBLIC_SOLANA:
        return Result("Solana RPC", "SOLANA_RPC_URL", "unset", "public RPC in use (slow) - a free Helius URL is faster")
    shown = url.split("?")[0][:40] + ("?api-key=" + mask(url.split("api-key=")[1]) if "api-key=" in url else "")
    if not url.startswith("https://"):
        return Result("Solana RPC", "SOLANA_RPC_URL", "fail", "must start with https://", shown)
    try:
        resp = await client.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "getHealth"})
        if resp.status_code == 200 and resp.json().get("result") == "ok":
            return Result("Solana RPC", "SOLANA_RPC_URL", "ok", "RPC answers", shown)
        return Result("Solana RPC", "SOLANA_RPC_URL", "fail", _http(resp) if resp.status_code != 200 else "RPC not healthy", shown)
    except Exception as exc:  # noqa: BLE001
        return Result("Solana RPC", "SOLANA_RPC_URL", "fail", _reason(exc), shown)


async def check_coingecko(client, env, urls) -> Result:
    key = env.get("COINGECKO_API_KEY", "")
    r = Result("CoinGecko", "COINGECKO_API_KEY", "unset", "not set (optional; prices may hit rate limits)", mask(key) if key else "")
    if not key:
        return r
    base = env.get("COINGECKO_BASE_URL") or urls["coingecko"]
    try:
        resp = await client.get(f"{base}/ping", headers={"x-cg-demo-api-key": key})
        if resp.status_code == 200:
            return Result(r.service, r.variable, "ok", "demo key accepted", r.shown)
        return Result(r.service, r.variable, "fail", _http(resp) + " (is it a Demo key, not a Pro key?)", r.shown)
    except Exception as exc:  # noqa: BLE001
        return Result(r.service, r.variable, "fail", _reason(exc), r.shown)


async def check_chainalysis(client, env, urls) -> Result:
    key = env.get("CHAINALYSIS_API_KEY", "")
    r = Result(
        "Chainalysis", "CHAINALYSIS_API_KEY", "unset",
        "not needed: the free Chainalysis sanctions oracle is used instead", mask(key) if key else "",
    )
    if not key:
        return r
    try:
        resp = await client.get(
            f"{urls['chainalysis']}/address/{ZERO_EVM}", headers={"X-API-Key": key, "Accept": "application/json"}
        )
        if resp.status_code == 200 and "identifications" in resp.json():
            return Result(r.service, r.variable, "ok", "key accepted", r.shown)
        return Result(r.service, r.variable, "fail", _http(resp), r.shown)
    except Exception as exc:  # noqa: BLE001
        return Result(r.service, r.variable, "fail", _reason(exc), r.shown)


async def check_alchemy(client, env, urls) -> Result:
    key = env.get("ALCHEMY_API_KEY", "")
    r = Result("Alchemy", "ALCHEMY_API_KEY", "unset", "not set (optional: public RPCs are used)", mask(key) if key else "")
    if not key:
        return r
    try:
        resp = await client.post(
            urls.get("alchemy", f"https://eth-mainnet.g.alchemy.com/v2/{key}"),
            json={"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []},
        )
        data = resp.json() if resp.status_code == 200 else {}
        if str(data.get("result", "")).startswith("0x"):
            return Result(r.service, r.variable, "ok", "key accepted", r.shown)
        if resp.status_code == 200:
            return Result(r.service, r.variable, "fail", "Alchemy says: " + str((data.get("error") or {}).get("message", "error"))[:100], r.shown)
        return Result(r.service, r.variable, "fail", _http(resp), r.shown)
    except Exception as exc:  # noqa: BLE001
        return Result(r.service, r.variable, "fail", _reason(exc), r.shown)


async def check_oracle(client, env, urls) -> Result:
    """The free Chainalysis sanctions oracle on Ethereum (no key needed)."""
    key = env.get("ALCHEMY_API_KEY", "")
    url = urls.get("oracle_rpc") or (f"https://eth-mainnet.g.alchemy.com/v2/{key}" if key else PUBLIC_ETH_RPC)
    data = "0xdf592f7d" + "0" * 64  # isSanctioned(0x000...0)
    try:
        resp = await client.post(
            url, json={"jsonrpc": "2.0", "id": 1, "method": "eth_call", "params": [{"to": ORACLE, "data": data}, "latest"]}
        )
        result = resp.json().get("result", "") if resp.status_code == 200 else ""
        if isinstance(result, str) and len(result) >= 66:
            return Result("Sanctions oracle", "(no key needed)", "ok", "Chainalysis oracle answers" + (" via Alchemy" if key else ""))
        return Result("Sanctions oracle", "(no key needed)", "fail", _http(resp) if resp.status_code != 200 else "unexpected answer")
    except Exception as exc:  # noqa: BLE001
        return Result("Sanctions oracle", "(no key needed)", "fail", _reason(exc))


async def check_telegram(client, env, urls) -> list[Result]:
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    users = env.get("TELEGRAM_ALLOWED_USERS", "")
    out = []
    if not token:
        out.append(Result("Telegram bot", "TELEGRAM_BOT_TOKEN", "unset", "not set (bot off)"))
    else:
        shown = mask(token)
        try:
            resp = await client.get(f"{urls['telegram']}/bot{token}/getMe")
            data = resp.json() if resp.status_code in (200, 401, 404) else {}
            if data.get("ok"):
                out.append(Result("Telegram bot", "TELEGRAM_BOT_TOKEN", "ok", f"bot @{data['result'].get('username')}", shown))
            else:
                detail = "token rejected - copy it again from @BotFather" if resp.status_code in (401, 404) else _http(resp)
                out.append(Result("Telegram bot", "TELEGRAM_BOT_TOKEN", "fail", detail, shown))
        except Exception as exc:  # noqa: BLE001
            out.append(Result("Telegram bot", "TELEGRAM_BOT_TOKEN", "fail", _reason(exc) + " (Telegram is filtered in Iran: VPN needed)", shown))
    ids = [u for u in users.replace(" ", "").split(",") if u]
    if token and not ids:
        out.append(Result("Telegram users", "TELEGRAM_ALLOWED_USERS", "fail", "empty: nobody may use the bot (send /start to it to see your id)"))
    elif ids and not all(u.isdigit() for u in ids):
        out.append(Result("Telegram users", "TELEGRAM_ALLOWED_USERS", "fail", "must be numeric ids separated by commas, not @usernames"))
    elif ids:
        out.append(Result("Telegram users", "TELEGRAM_ALLOWED_USERS", "ok", f"{len(ids)} allowed user id(s)"))
    return out


async def check_toman(client, env, urls) -> Result:
    manual = env.get("USD_TOMAN_RATE", "")
    if manual:
        return Result("Dollar rate", "USD_TOMAN_RATE", "info", f"manual rate {manual} toman per dollar")
    try:
        resp = await client.get(
            f"{env.get('NOBITEX_BASE_URL') or urls['nobitex']}/market/stats",
            params={"srcCurrency": "usdt", "dstCurrency": "rls"},
        )
        rate = float(resp.json()["stats"]["usdt-rls"]["latest"]) / 10
        return Result("Dollar rate", "(Nobitex)", "ok", f"{rate:,.0f} toman per dollar")
    except Exception:  # noqa: BLE001
        pass
    try:
        resp = await client.get(f"{env.get('WALLEX_BASE_URL') or urls['wallex']}/v1/markets")
        rate = float(resp.json()["result"]["symbols"]["USDTTMN"]["stats"]["lastPrice"])
        return Result("Dollar rate", "(Wallex)", "ok", f"{rate:,.0f} toman per dollar (Nobitex did not answer)")
    except Exception:  # noqa: BLE001
        return Result(
            "Dollar rate", "USD_TOMAN_RATE", "fail",
            "Nobitex and Wallex unreachable (VPN on?) - set a manual rate in Settings",
        )


async def run_checks(env: dict[str, str], client: httpx.AsyncClient, urls: dict[str, str] | None = None) -> list[Result]:
    urls = {**URLS, **(urls or {})}
    checks = [
        check_trongrid(client, env, urls),
        check_etherscan(client, env, urls),
        check_tronscan(client, env, urls),
        check_solana(client, env, urls),
        check_coingecko(client, env, urls),
        check_alchemy(client, env, urls),
        check_oracle(client, env, urls),
        check_chainalysis(client, env, urls),
        check_toman(client, env, urls),
    ]
    results = list(await asyncio.gather(*checks))
    results.extend(await check_telegram(client, env, urls))
    for var, name in FUTURE.items():
        value = env.get(var, "")
        results.append(
            Result(name, var, "info" if value else "unset", "saved for an upcoming feature" if value else "not set", mask(value) if value else "")
        )
    return results


MARK = {"ok": "[OK]  ", "fail": "[FAIL]", "unset": "[ -- ]", "info": "[INFO]"}


def report(results: list[Result], warnings: list[str]) -> str:
    lines = ["=== ChainTrace API key check ===", ""]
    width = max(len(r.service) for r in results)
    for r in results:
        shown = f" ({r.shown})" if r.shown else ""
        lines.append(f"{MARK[r.status]} {r.service.ljust(width)}  {r.variable}{shown}: {r.detail}")
    if warnings:
        lines += ["", "Problems in .env:"] + [f"  - {w}" for w in warnings]
    ok = sum(r.status == "ok" for r in results)
    failed = [r.service for r in results if r.status == "fail"]
    lines += ["", f"{ok} working, {len(failed)} failing" + (f": {', '.join(failed)}" if failed else "")]
    lines.append("Keys are shown as their first 4 characters only; this output is safe to share.")
    return "\n".join(lines)


async def main_async(env_path: Path, urls: dict[str, str] | None = None, timeout: float = 10.0) -> tuple[str, int]:
    env, warnings = read_env(env_path)
    async with httpx.AsyncClient(timeout=timeout) as client:
        results = await run_checks(env, client, urls)
    by_var = {r.variable: r.status for r in results}
    code = 0 if by_var.get("TRONGRID_API_KEY") == "ok" and by_var.get("ETHERSCAN_API_KEY") == "ok" else 1
    return report(results, warnings), code


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    env_path = Path(argv[0]) if argv else ROOT / ".env"
    text, code = asyncio.run(main_async(env_path))
    print(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
