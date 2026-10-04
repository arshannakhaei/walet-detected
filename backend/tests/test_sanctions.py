"""Live sanctions/freeze checks: Chainalysis oracle (EVM) and Tether's USDT freeze list."""

import asyncio
import json

import httpx
import pytest
import respx

from app.models import Chain
from app.services.risk import RiskAnalyzer
from app.services.sanctions import ORACLE, USDT_ETHEREUM, USDT_TRON, SanctionsChecker, SanctionStatus
from tests.test_tracing import M1, M2, SCAMMER, env  # noqa: F401

TRUE = "0x" + "0" * 63 + "1"
FALSE = "0x" + "0" * 64
EVM = "0x7f367cc41522ce07553e823bf3be79a889debe1b"
TRONGRID = "https://api.trongrid.io"


async def checker(client, **kw):
    return SanctionsChecker(client, TRONGRID, "tg-key", **kw)


async def test_tron_usdt_frozen_call():
    async with httpx.AsyncClient() as client:
        svc = await checker(client)
        with respx.mock() as r:
            route = r.post(f"{TRONGRID}/wallet/triggerconstantcontract").respond(json={"constant_result": ["0" * 63 + "1"]})
            status = await svc.check(Chain.TRON, SCAMMER)
            await svc.check(Chain.TRON, SCAMMER)  # cached
    assert status.usdt_frozen is True and status.sanctioned is None and status.flagged
    assert route.call_count == 1
    body = json.loads(route.calls[0].request.content)
    assert body["contract_address"] == USDT_TRON and body["function_selector"] == "isBlackListed(address)"
    assert len(body["parameter"]) == 64 and body["parameter"].startswith("0" * 24)
    assert route.calls[0].request.headers["TRON-PRO-API-KEY"] == "tg-key"


async def test_ethereum_checks_oracle_and_usdt_with_alchemy():
    async with httpx.AsyncClient() as client:
        svc = await checker(client, alchemy_key="alc")
        with respx.mock() as r:

            def answer(request):
                call = json.loads(request.content)["params"][0]
                return httpx.Response(200, json={"result": TRUE if call["to"] == ORACLE else FALSE})

            route = r.post("https://eth-mainnet.g.alchemy.com/v2/alc").mock(side_effect=answer)
            status = await svc.check(Chain.ETHEREUM, EVM)
    assert status.sanctioned is True and status.usdt_frozen is False
    calls = [json.loads(c.request.content)["params"][0] for c in route.calls]
    assert {c["to"] for c in calls} == {ORACLE, USDT_ETHEREUM}
    oracle_call = next(c for c in calls if c["to"] == ORACLE)
    assert oracle_call["data"] == "0xdf592f7d" + "0" * 24 + EVM[2:]


async def test_public_rpc_without_alchemy_and_oracle_only_off_ethereum():
    async with httpx.AsyncClient() as client:
        svc = await checker(client)
        with respx.mock() as r:
            route = r.post("https://polygon-bor-rpc.publicnode.com").respond(json={"result": FALSE})
            status = await svc.check(Chain.POLYGON, EVM)
    assert status.sanctioned is False and status.usdt_frozen is None and route.call_count == 1


async def test_unreachable_or_missing_contract_is_unknown_not_clean():
    async with httpx.AsyncClient() as client:
        svc = await checker(client)
        with respx.mock() as r:
            r.post(f"{TRONGRID}/wallet/triggerconstantcontract").mock(side_effect=httpx.ConnectError("x"))
            r.post("https://base-rpc.publicnode.com").respond(json={"result": "0x"})  # no contract there
            tron = await svc.check(Chain.TRON, SCAMMER)
            base = await svc.check(Chain.BASE, EVM)
            r.post(f"{TRONGRID}/wallet/triggerconstantcontract").respond(json={"constant_result": ["0" * 64]})
            again = await svc.check(Chain.TRON, SCAMMER)  # unknown answers are not cached
    assert tron.usdt_frozen is None and base.sanctioned is None and not tron.flagged
    assert again.usdt_frozen is False
    assert not svc.supports(Chain.BITCOIN) and not svc.supports(Chain.SOLANA)


class FakeSanctions:
    def __init__(self, frozen=(), sanctioned=()):
        self.frozen, self.sanctioned, self.asked = set(frozen), set(sanctioned), []

    def supports(self, chain):
        return True

    async def check(self, chain, address):
        self.asked.append(address)
        return SanctionStatus(
            chain=chain, address=address, usdt_frozen=address in self.frozen, sanctioned=address in self.sanctioned
        )

    async def check_many(self, chain, addresses):
        return [await self.check(chain, a) for a in addresses]


@pytest.mark.parametrize("deep", [False, True])
async def test_risk_findings(env, deep):
    wallets, labels, _ = await env()
    from app.services.graph import GraphBuilder

    sanctions = FakeSanctions(frozen={SCAMMER, M1}, sanctioned={M2})
    risk = RiskAnalyzer(wallets, labels, GraphBuilder(wallets, labels, 1000), sanctions)
    report = await risk.analyze(Chain.TRON, SCAMMER, deep=deep)
    codes = {f.code for f in report.findings}
    assert "usdt_frozen" in codes and report.score == 100 and report.level == "critical"
    if deep:
        assert {"frozen_counterparty", "sanctioned_counterparty"} <= codes
        frozen = next(f for f in report.findings if f.code == "frozen_counterparty")
        assert frozen.evidence == [M1]
    else:
        assert sanctions.asked == [SCAMMER]  # only the wallet itself without deep


def test_demo_shows_a_frozen_mule(tmp_path):
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app
    from app.providers import demo

    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'd.db'}", demo_mode=True, monitor_interval_seconds=0, _env_file=None
    )
    with TestClient(create_app(settings)) as c:
        assert c.get(f"/api/wallet/{demo.MULES[3]}/sanctions").json()["usdt_frozen"] is True
        assert c.get(f"/api/wallet/{demo.MULES[0]}/sanctions").json()["usdt_frozen"] is None
        risk = c.get(f"/api/wallet/{demo.MULES[3]}/risk").json()
        assert risk["findings"][0]["code"] == "usdt_frozen"
        job = c.post("/api/links", json={"addresses": [demo.SCAMMER, demo.MULES[3]]}).json()
        for _ in range(100):
            result = c.get(f"/api/links/{job['id']}").json()
            if result["state"] != "running":
                break
            asyncio.run(asyncio.sleep(0.02))
        members = {m["address"]: m for m in result["result"]["members"]}
        assert members[demo.MULES[3]]["usdt_frozen"] is True
