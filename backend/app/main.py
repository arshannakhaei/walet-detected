"""FastAPI application entry point: REST API plus the built dashboard."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import router
from app.config import PROJECT_ROOT, Settings, get_settings
from app.container import create_services

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        services = await create_services(settings)
        s = app.state
        s.services = services
        s.db = services.db
        s.providers = services.providers
        s.wallet_service = services.wallets
        s.labels = services.labels
        s.graph_builder = services.graphs
        s.tracer = services.tracer
        s.risk = services.risk
        s.cases = services.cases
        s.monitor = services.monitor

        bot = None
        if settings.telegram_bot_token:
            from app.bot.telegram import TelegramBot

            bot = TelegramBot(settings.telegram_bot_token, settings.telegram_user_ids, services, settings.public_url)
            services.monitor.add_notifier(bot.notify)
            bot.start()
            log.info("Telegram bot started")
        if settings.monitor_interval_seconds > 0:
            services.monitor.start(settings.monitor_interval_seconds)

        yield

        if bot is not None:
            await bot.stop()
        await services.close()

    app = FastAPI(title="ChainTrace", version="1.0.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)

    if FRONTEND_DIST.is_dir():
        app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            # Client-side routes (e.g. /wallet/tron/T...) all load index.html.
            if path.startswith("api/"):
                raise HTTPException(404)
            file = (FRONTEND_DIST / path).resolve()
            if path and file.is_file() and FRONTEND_DIST.resolve() in file.parents:
                return FileResponse(file)
            return FileResponse(FRONTEND_DIST / "index.html")
    else:
        log.info("dashboard not built (frontend/dist missing); API only")

    return app


app = create_app()
