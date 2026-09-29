"""Watchlist monitor, graph image and Telegram bot handlers (no network)."""

from decimal import Decimal
from types import SimpleNamespace

from app.bot import texts
from app.bot.telegram import TelegramBot, _chunks
from app.models import Chain
from app.services.graph import GraphBuilder, GraphParams
from app.services.graph_image import render_png
from app.services.monitor import MonitorService
from app.services.risk import RiskAnalyzer
from app.services.tracer import Tracer
from tests.test_tracing import EXCHANGE, M1, SCAMMER, SCENARIO, VICTIM, env, usdt  # noqa: F401


async def test_monitor_alerts_only_new_matching_transfers(env):
    wallets, labels, provider = await env()
    monitor = MonitorService(wallets._db, wallets)
    received = []

    async def notifier(alert):
        received.append(alert)

    monitor.add_notifier(notifier)
    watch = await monitor.add(Chain.TRON, SCAMMER, name="scammer", min_amount=Decimal(100))
    assert await monitor.check_all() == []  # existing history is not news

    provider.transfers = SCENARIO + [
        usdt("new1", SCAMMER, M1, 5000, 500),
        usdt("small", SCAMMER, M1, 10, 501),  # below min_amount
        usdt("new2", VICTIM, SCAMMER, 250, 502),
    ]
    alerts = await monitor.check_all()
    assert [(a.tx_hash, a.direction.value) for a in alerts] == [("new1", "out"), ("new2", "in")]
    assert [a.tx_hash for a in received] == ["new1", "new2"]
    assert await monitor.check_all() == []  # not reported twice

    stored = await monitor.alerts(unread_only=True)
    assert len(stored) == 2 and stored[0].watch_name == "scammer"
    await monitor.mark_read([stored[0].id])
    assert len(await monitor.alerts(unread_only=True)) == 1
    assert await monitor.remove(watch.id)
    assert await monitor.list_watches() == []


async def test_render_png(env):
    wallets, labels, _ = await env()
    graph = await GraphBuilder(wallets, labels, 1000).build(Chain.TRON, SCAMMER, GraphParams())
    png = render_png(graph)
    assert png.startswith(b"\x89PNG") and len(png) > 5000


def test_chunks_split_long_messages():
    text = "\n".join("x" * 100 for _ in range(100))
    parts = _chunks(text)
    assert len(parts) == 3 and all(len(p) <= 4096 for p in parts)


class FakeMessage:
    def __init__(self, user_id=1, text=""):
        self.from_user = SimpleNamespace(id=user_id)
        self.chat = SimpleNamespace(id=555)
        self.text = text
        self.sent: list[str] = []
        self.photos: list = []

    async def answer(self, text, **_):
        self.sent.append(text)

    async def answer_photo(self, photo, caption=None, **_):
        self.photos.append((photo, caption))


async def _bot(env):
    wallets, labels, provider = await env()
    graphs = GraphBuilder(wallets, labels, 1000)
    registry = wallets._providers
    services = SimpleNamespace(
        providers=registry,
        wallets=wallets,
        labels=labels,
        graphs=graphs,
        tracer=Tracer(wallets, labels, 1000),
        risk=RiskAnalyzer(wallets, labels, graphs),
        monitor=MonitorService(wallets._db, wallets),
    )
    return TelegramBot("123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi", {1}, services, "http://localhost:8000")


async def test_bot_wallet_command(env):
    bot = await _bot(env)
    msg = FakeMessage()
    await bot._guard(msg, lambda: bot.wallet(msg, [SCAMMER]))
    summary = msg.sent[-1]
    assert SCAMMER in summary and "بیشترین واریز از" in summary and VICTIM in summary
    assert "http://localhost:8000/wallet/tron/" in summary
    assert msg.photos and msg.photos[0][0].data.startswith(b"\x89PNG")


async def test_bot_trace_and_watch(env):
    bot = await _bot(env)
    msg = FakeMessage()
    await bot._guard(msg, lambda: bot.trace(msg, [VICTIM, "s1", "lifo"]))
    assert "Test Exchange" in msg.sent[-1]

    await bot._guard(msg, lambda: bot.watch(msg, [SCAMMER, "100"]))
    await bot._guard(msg, lambda: bot.watchlist(msg, []))
    assert SCAMMER in msg.sent[-1]
    await bot._guard(msg, lambda: bot.unwatch(msg, [SCAMMER]))
    assert msg.sent[-1] == "✅ حذف شد."


async def test_bot_rejects_strangers_and_bad_input(env):
    bot = await _bot(env)
    stranger = FakeMessage(user_id=99)
    await bot._guard(stranger, lambda: bot.wallet(stranger, [SCAMMER]))
    assert "<code>99</code>" in stranger.sent[0]

    msg = FakeMessage()
    await bot._guard(msg, lambda: bot.wallet(msg, ["not-an-address"]))
    assert msg.sent[-1].startswith("❌")
    await bot._guard(msg, lambda: bot.trace(msg, [VICTIM, "missing-tx"]))
    assert msg.sent[-1].startswith("❌")


def test_help_mentions_all_commands():
    for command in ("/wallet", "/graph", "/risk", "/trace", "/watch", "/unwatch", "/watchlist"):
        assert command in texts.HELP


def test_bot_and_dashboard_share_finding_titles():
    import re
    from pathlib import Path

    ts = (Path(__file__).resolve().parents[2] / "frontend/src/lib/findings.ts").read_text()
    assert dict(re.findall(r"  (\w+): '([^']+)'", ts)) == texts.FINDING_FA
