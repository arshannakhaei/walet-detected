"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import router
from app.config import Settings, get_settings
from app.db import Database
from app.providers import ProviderRegistry
from app.services.cases import CaseService
from app.services.graph import GraphBuilder
from app.services.labels import LabelService
from app.services.monitor import MonitorService
from app.services.pricing import PriceService
from app.services.risk import RiskAnalyzer
from app.services.tracer import Tracer
from app.services.wallet import WalletService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.database_url.startswith("sqlite"):
            db_path = settings.database_url.split(":///", 1)[-1]
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        db = Database(settings.database_url)
        await db.init()
        client = httpx.AsyncClient(timeout=settings.http_timeout_seconds)
        providers = ProviderRegistry(settings, client)
        app.state.db = db
        app.state.providers = providers
        prices = PriceService(client, settings.coingecko_base_url, settings.coingecko_api_key)
        wallets = WalletService(
            db, providers, settings.max_transfers_per_address, settings.cache_ttl_seconds, prices
        )
        labels = LabelService(db)
        await labels.load()
        app.state.wallet_service = wallets
        app.state.labels = labels
        app.state.graph_builder = GraphBuilder(wallets, labels, settings.hub_threshold)
        app.state.tracer = Tracer(wallets, labels, settings.hub_threshold)
        app.state.risk = RiskAnalyzer(wallets, labels, app.state.graph_builder)
        app.state.cases = CaseService(db)
        monitor = MonitorService(db, wallets, poll_limit=settings.page_size)
        app.state.monitor = monitor

        bot = None
        if settings.telegram_bot_token:
            from app.bot.telegram import TelegramBot  # optional dependency path

            services = SimpleNamespace(
                providers=providers,
                wallets=wallets,
                labels=labels,
                graphs=app.state.graph_builder,
                tracer=app.state.tracer,
                risk=app.state.risk,
                monitor=monitor,
            )
            bot = TelegramBot(settings.telegram_bot_token, settings.telegram_user_ids, services, settings.public_url)
            monitor.add_notifier(bot.notify)
            bot.start()
            logging.getLogger(__name__).info("Telegram bot started")
        if settings.monitor_interval_seconds > 0:
            monitor.start(settings.monitor_interval_seconds)

        yield

        await monitor.stop()
        if bot is not None:
            await bot.stop()
        await client.aclose()
        await db.close()

    app = FastAPI(title="ChainTrace", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    return app


app = create_app()
