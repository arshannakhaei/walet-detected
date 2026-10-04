"""scripts/check_keys.py: every key tried against (mocked) services, never printed in full."""

import asyncio
import importlib.util
import sys
from pathlib import Path

import httpx
import respx

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("check_keys", ROOT / "scripts" / "check_keys.py")
ck = importlib.util.module_from_spec(spec)
sys.modules["check_keys"] = ck  # dataclasses look the module up
spec.loader.exec_module(ck)

KEYS = {
    "TRONGRID_API_KEY": "tg-1234567890abcdef",
    "ETHERSCAN_API_KEY": "ES1234567890ABCDEF",
    "TRONSCAN_API_KEY": "ts-abcdef1234567890",
    "COINGECKO_API_KEY": "CG-abcdef1234567890",
    "CHAINALYSIS_API_KEY": "ch-abcdef1234567890",
    "TELEGRAM_BOT_TOKEN": "123456:ABCDEFabcdefSECRET",
    "SOLANA_RPC_URL": "https://mainnet.helius-rpc.com/?api-key=hel-secret-123456",
}


def write_env(tmp_path, text):
    path = tmp_path / ".env"
    path.write_text(text, encoding="utf-8")
    return path


def mock_all_ok(r):
    r.get(f"{ck.URLS['trongrid']}/v1/accounts/{ck.USDT_TRON}").respond(json={"data": []})
    r.get(ck.URLS["etherscan"]).respond(json={"status": "1", "result": "0"})
    r.get(f"{ck.URLS['tronscan']}/accountv2").respond(json={"balance": 0})
    r.post("https://mainnet.helius-rpc.com/").respond(json={"result": "ok"})
    r.get(f"{ck.URLS['coingecko']}/ping").respond(json={"gecko_says": "ok"})
    r.get(f"{ck.URLS['chainalysis']}/address/{ck.ZERO_EVM}").respond(json={"identifications": []})
    r.get(url__regex=r"https://api\.telegram\.org/bot.*/getMe").respond(json={"ok": True, "result": {"username": "ct_bot"}})
    r.get(f"{ck.URLS['nobitex']}/market/stats").respond(json={"stats": {"usdt-rls": {"latest": "1000000"}}})


def run(path):
    return asyncio.run(ck.main_async(path))


def test_all_keys_ok_and_never_printed(tmp_path):
    env = "\n".join(f"{k}={v}" for k, v in KEYS.items()) + "\nTELEGRAM_ALLOWED_USERS=111, 222\n"
    with respx.mock() as r:
        mock_all_ok(r)
        text, code = run(write_env(tmp_path, env))
    assert code == 0
    assert text.count("[OK]") == 9, text
    assert "Problems in .env" not in text
    assert "bot @ct_bot" in text and "100,000 toman per dollar" in text and "2 allowed user id(s)" in text
    for value in KEYS.values():
        secret = value.split("api-key=")[-1]
        assert secret not in text
    assert "tg-1..." in text


def test_rejected_keys_explain_why(tmp_path):
    env = "TRONGRID_API_KEY=bad\nETHERSCAN_API_KEY=bad\nCOINGECKO_API_KEY=bad\nTELEGRAM_BOT_TOKEN=1:bad\nTELEGRAM_ALLOWED_USERS=@me\n"
    with respx.mock(assert_all_called=False) as r:
        r.get(f"{ck.URLS['trongrid']}/v1/accounts/{ck.USDT_TRON}").respond(401)
        r.get(ck.URLS["etherscan"]).respond(json={"status": "0", "message": "NOTOK", "result": "Invalid API Key"})
        r.get(f"{ck.URLS['coingecko']}/ping").respond(400)
        r.get(url__regex=r"https://api\.telegram\.org/.*").respond(401, json={"ok": False})
        r.get(f"{ck.URLS['nobitex']}/market/stats").mock(side_effect=httpx.ConnectError("x"))
        r.get(f"{ck.URLS['wallex']}/v1/markets").respond(json={"result": {"symbols": {"USDTTMN": {"stats": {"lastPrice": "95000"}}}}})
        text, code = run(write_env(tmp_path, env))
    assert code == 1
    assert "key rejected (HTTP 401)" in text
    assert "Etherscan says: Invalid API Key" in text
    assert "token rejected" in text and "not @usernames" in text
    assert "95,000 toman per dollar (Nobitex did not answer)" in text
    assert "1:bad" not in text


def test_network_errors_do_not_leak_tokens(tmp_path):
    env = "TELEGRAM_BOT_TOKEN=999:TOPSECRET\nUSD_TOMAN_RATE=60000\n"
    with respx.mock(assert_all_called=False) as r:
        r.get(url__regex=r"https://api\.telegram\.org/.*").mock(side_effect=httpx.ConnectError("https://api.telegram.org/bot999:TOPSECRET/getMe"))
        text, _ = run(write_env(tmp_path, env))
    assert "TOPSECRET" not in text
    assert "cannot connect" in text and "VPN needed" in text
    assert "manual rate 60000" in text
    assert "public RPC in use" in text  # no Solana URL set


def test_env_mistakes_are_reported(tmp_path):
    env = (
        "# a comment\n"
        "TRONGRID_KEY=abc\n"
        "# ETHERSCAN_API_KEY=realkey\n"
        "COINGECKO_API_KEY=\"CG-abc def\"\n"
        "ALCHEMY_API_KEY='alc-123456'\n"
    )
    values, warnings = ck.read_env(write_env(tmp_path, env))
    assert values["ALCHEMY_API_KEY"] == "alc-123456"
    text = "\n".join(warnings)
    assert "TRONGRID_KEY is not a ChainTrace setting - did you mean TRONGRID_API_KEY?" in text
    assert "ETHERSCAN_API_KEY has a value but the line starts with '#'" in text
    assert "COINGECKO_API_KEY contains spaces" in text
    assert ck.read_env(tmp_path / "missing.env")[1][0].startswith("missing.env not found")


def test_knows_every_setting_of_the_app():
    from app.config import Settings

    assert {name.upper() for name in Settings.model_fields} <= ck.KNOWN
