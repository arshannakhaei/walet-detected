"""Links between a set of wallets: direct transfers, pass-through paths, shared counterparties."""

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.models import Chain, Transfer
from app.providers import ProviderError
from app.services.labels import Label, LabelCategory
from app.services.links import LinkAnalyzer, LinkParams, SharedRole, match_hops
from tests.conftest import USDT, make_address

T0 = datetime(2025, 1, 1, tzinfo=timezone.utc)
X, Y, Z, W = (make_address(n) for n in (101, 102, 103, 104))
C, D, E = (make_address(n) for n in (201, 202, 203))
EXCHANGE = make_address(300)
HUB = make_address(301)


def tr(n: int, frm: str, to: str, amount, minutes: float, symbol="USDT", contract=USDT) -> Transfer:
    return Transfer(
        chain=Chain.TRON,
        transfer_id=f"t{n}",
        tx_hash=f"h{n}",
        timestamp=T0 + timedelta(minutes=minutes),
        from_address=frm,
        to_address=to,
        amount=Decimal(str(amount)),
        token_symbol=symbol,
        token_contract=contract,
        token_decimals=6,
    )


class FakeWallets:
    def __init__(self, transfers: list[Transfer], failing=(), busy=()):
        self.transfers = transfers
        self.failing = set(failing)
        self.busy = set(busy)
        self.loaded: list[str] = []

    async def load_transfers(self, chain, address, refresh=False, limit=None, quick=False):
        self.loaded.append(address)
        if address in self.failing:
            raise ProviderError("tron: rate limit")
        own = [t for t in self.transfers if address in (t.from_address, t.to_address)]
        return sorted(own, key=lambda t: t.timestamp, reverse=True), address in self.busy


class FakeLabels:
    def __init__(self, labels: dict[str, LabelCategory] | None = None):
        self.labels = labels or {}

    def get(self, chain, address):
        cat = self.labels.get(address)
        return None if cat is None else Label(chain=chain, address=address, name=cat.value, category=cat, source="builtin")


STORY = [
    tr(1, X, Z, 3999, 0),  # direct X -> Z
    tr(2, X, Z, "0.5", 5),
    tr(3, X, C, 5000, 10),  # X -> C -> Y, same money a few minutes later
    tr(4, C, Y, 4990, 25),
    tr(5, C, Y, 777, 26),  # unrelated amount
    tr(6, E, X, 100, -100),  # E funds X and W: common source
    tr(7, E, W, 200, -90),
    tr(8, X, EXCHANGE, 50, 100),  # both deposit to the same exchange
    tr(9, W, EXCHANGE, 60, 110),
    tr(10, EXCHANGE, Y, 40, 120),  # exchange withdrawal: weak path
    tr(11, X, Z, 10, 1, symbol="TRX", contract=None),  # other token, filtered out
    tr(12, make_address(400), X, 0, 2, contract="TXLAQ63Xg1NAzckPwKHvzw7CSEmLMEqcdj"),  # fake USDT spam
]


def analyze(transfers, members, labels=None, **kw):
    wallets = FakeWallets(transfers, **{k: kw.pop(k) for k in ("failing", "busy") if k in kw})
    analyzer = LinkAnalyzer(wallets, FakeLabels(labels), hub_threshold=500)
    report = asyncio.run(analyzer.analyze(Chain.TRON, members, LinkParams(**kw)))
    return report, wallets


def test_direct_links_between_members():
    report, _ = analyze(STORY, [X, Y, Z, W])
    assert len(report.direct) == 1
    link = report.direct[0]
    assert (link.from_address, link.to_address) == (X, Z)
    assert link.total == Decimal("3999")  # the 0.5 USDT transfer is below min_amount
    assert link.transfers[0].amount == Decimal("3999") and link.transfers[0].tx_hash == "h1"


def test_min_amount_zero_keeps_small_transfers_but_not_spam():
    report, _ = analyze(STORY, [X, Z], min_amount=Decimal(0))
    assert report.direct[0].count == 2
    assert report.direct[0].total == Decimal("3999.5")


def test_pass_through_path_with_matched_amount():
    report, _ = analyze(STORY, [X, Y, Z, W], labels={EXCHANGE: LabelCategory.EXCHANGE})
    path = next(p for p in report.paths if p.via == [C])
    assert (path.from_address, path.to_address) == (X, Y)
    assert path.amount_in == Decimal("5000") and path.amount_out == Decimal("5767")
    assert len(path.matched) == 1
    hop = path.matched[0]
    assert hop.incoming.amount == Decimal("5000") and hop.outgoing.amount == Decimal("4990")
    assert hop.delay_minutes == 15
    assert not path.through_service
    # Paths through an exchange are kept but flagged, and sorted last.
    weak = [p for p in report.paths if p.via == [EXCHANGE]]
    assert weak and all(p.through_service and not p.matched for p in weak)
    assert report.paths[-1].through_service


def test_groups_follow_money_not_exchanges():
    report, _ = analyze(STORY, [X, Y, Z, W], labels={EXCHANGE: LabelCategory.EXCHANGE})
    assert report.groups == [[X, Y, Z]] or sorted(report.groups[0]) == sorted([X, Y, Z])
    by = {m.address: m for m in report.members}
    assert by[W].group is None
    assert by[X].linked_members == 2 and by[Z].linked_members == 1
    assert by[X].sent_to_members == Decimal("3999") and by[Z].received_from_members == Decimal("3999")
    assert [m.index for m in report.members] == [1, 2, 3, 4]


def test_shared_counterparties():
    report, _ = analyze(STORY, [X, Y, Z, W], labels={EXCHANGE: LabelCategory.EXCHANGE})
    roles = {(s.address, s.role) for s in report.shared}
    assert (E, SharedRole.COMMON_SOURCE) in roles
    assert (EXCHANGE, SharedRole.COMMON_DESTINATION) in roles
    ex = next(s for s in report.shared if s.address == EXCHANGE)
    assert ex.label.category == LabelCategory.EXCHANGE and ex.total == Decimal("110")


def test_token_filter_all_tokens():
    report, _ = analyze(STORY, [X, Z], token=None)
    assert {link.token_symbol for link in report.direct} == {"USDT", "TRX"}


def test_money_must_flow_forward_in_time():
    # C paid Y before X paid C: not a path.
    report, _ = analyze([tr(1, C, Y, 100, 0), tr(2, X, C, 100, 60)], [X, Y])
    assert report.paths == [] and report.groups == []


def test_failed_member_is_reported_and_rest_still_analyzed():
    report, _ = analyze(STORY, [X, Y, Z], failing=[Y])
    by = {m.address: m for m in report.members}
    assert "rate limit" in by[Y].error
    assert report.direct and not report.complete


def test_duplicates_are_removed():
    report, _ = analyze(STORY, [X, Z, X])
    assert [m.address for m in report.members] == [X, Z]


def test_deep_finds_three_hop_paths_and_skips_hubs():
    transfers = [
        tr(1, X, C, 1000, 0),
        tr(2, C, D, 995, 10),
        tr(3, D, Y, 990, 20),
        tr(4, X, HUB, 500, 0),
        tr(5, HUB, D, 500, 5),
    ]
    shallow, _ = analyze(transfers, [X, Y])
    assert shallow.paths == []
    deep, wallets = analyze(transfers, [X, Y], deep=True, busy=[HUB])
    assert deep.intermediaries_checked == 2
    assert {C, HUB} <= set(wallets.loaded)
    paths = [p for p in deep.paths if p.via == [C, D]]
    assert len(paths) == 1 and paths[0].amount_in == Decimal("1000") and paths[0].amount_out == Decimal("990")
    assert not any(p.via[0] == HUB for p in deep.paths)
    assert deep.groups and sorted(deep.groups[0]) == sorted([X, Y])


def test_match_hops_uses_each_outgoing_once_and_respects_window():
    ins = [tr(1, X, C, 100, 0), tr(2, X, C, 100, 1)]
    outs = [tr(3, C, Y, 99, 2), tr(4, C, Y, 100, 60 * 24 * 10)]
    pairs = match_hops(ins, outs, timedelta(hours=72), Decimal("0.03"))
    assert len(pairs) == 1 and pairs[0].outgoing.tx_hash == "h3"


@pytest.fixture
def demo_client(tmp_path):
    from app.config import Settings
    from app.main import create_app

    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'demo.db'}",
        demo_mode=True,
        monitor_interval_seconds=0,
        telegram_bot_token="",
    )
    with TestClient(create_app(settings)) as c:
        yield c


def poll(client, job_id):
    for _ in range(200):
        job = client.get(f"/api/links/{job_id}").json()
        if job["state"] != "running":
            return job
        asyncio.run(asyncio.sleep(0.02))
    raise AssertionError("analysis did not finish")


def test_links_api_on_demo(demo_client):
    from app.providers import demo

    members = [demo.SCAMMER, *demo.MULES, demo.VICTIMS[0]]
    resp = demo_client.post("/api/links", json={"addresses": "\n".join(members)})
    assert resp.status_code == 200, resp.text
    job = resp.json()
    assert job["state"] == "running" and "result" not in job
    job = poll(demo_client, job["id"])
    assert job["state"] == "done", job
    report = job["result"]
    assert report["chain"] == "tron" and report["token"] == "USDT"
    pairs = {(d["from_address"], d["to_address"]) for d in report["direct"]}
    assert (demo.SCAMMER, demo.MULES[0]) in pairs and (demo.VICTIMS[0], demo.SCAMMER) in pairs
    assert len(report["groups"]) == 1 and len(report["groups"][0]) == len(members)
    assert report["complete"]


def test_links_api_finds_pass_through_on_demo(demo_client):
    from app.providers import demo

    # Victim -> scammer -> mule, with the scammer left out of the list.
    resp = demo_client.post("/api/links", json={"addresses": [demo.VICTIMS[0], demo.MULES[0]], "deep": True})
    report = poll(demo_client, resp.json()["id"])["result"]
    assert report["direct"] == []
    assert any(p["via"] == [demo.SCAMMER] for p in report["paths"])
    assert report["groups"]


def test_links_api_validation(demo_client):
    from app.providers import demo

    assert demo_client.post("/api/links", json={"addresses": demo.SCAMMER}).status_code == 400
    bad = demo_client.post("/api/links", json={"addresses": [demo.SCAMMER, "Tnotanaddress"]})
    assert bad.status_code == 400 and "Tnotanaddress" in bad.json()["detail"]
    assert demo_client.get("/api/links/nope").status_code == 404


def test_exchange_like_member_does_not_join_groups():
    # #1 behaves like an exchange hot wallet: it paid hundreds of addresses.
    hot = X
    payouts = [tr(1000 + i, hot, make_address(5000 + i), 10, i) for i in range(520)]
    transfers = payouts + [tr(1, hot, Y, 500, 1), tr(2, hot, Z, 700, 2), tr(3, Y, W, 300, 10)]
    report, _ = analyze(transfers, [hot, Y, Z, W])
    by = {m.address: m for m in report.members}
    assert by[hot].likely_service and by[hot].counterparty_count >= 500
    assert len(report.direct) == 3  # still listed...
    assert [sorted(g) for g in report.groups] == [sorted([Y, W])]  # ...but only Y-W is a real link
    assert by[hot].group is None and by[Z].group is None
