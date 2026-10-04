"""Builds every service once, for the web app, the MCP server and scripts alike."""

from dataclasses import dataclass
from pathlib import Path

import httpx

from app.config import ENV_FILE, Settings
from app.db import Database
from app.providers import ProviderRegistry
from app.services.cases import CaseService
from app.services.fx import ValueService
from app.services.graph import GraphBuilder
from app.services.labels import LabelService
from app.services.links import LinkAnalyzer, LinkJobs
from app.services.monitor import MonitorService
from app.services.pricing import PriceService
from app.services.risk import RiskAnalyzer
from app.services.sanctions import SanctionsChecker, SanctionStatus
from app.services.tracer import Tracer
from app.services.wallet import WalletService


@dataclass
class Services:
    settings: Settings
    db: Database
    client: httpx.AsyncClient
    providers: ProviderRegistry
    prices: PriceService
    values: ValueService
    wallets: WalletService
    labels: LabelService
    graphs: GraphBuilder
    tracer: Tracer
    risk: RiskAnalyzer
    cases: CaseService
    monitor: MonitorService
    links: LinkJobs
    sanctions: SanctionsChecker
    env_file: Path = ENV_FILE

    async def close(self) -> None:
        await self.monitor.stop()
        await self.links.close()
        await self.wallets.close()
        await self.client.aclose()
        await self.db.close()


async def create_services(settings: Settings) -> Services:
    if settings.database_url.startswith("sqlite"):
        db_path = settings.database_url.split(":///", 1)[-1]
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    db = Database(settings.database_url)
    await db.init()
    client = httpx.AsyncClient(timeout=settings.http_timeout_seconds)
    providers = ProviderRegistry(settings, client)
    prices = PriceService(client, settings.coingecko_base_url, settings.coingecko_api_key)
    values = ValueService(
        client,
        db,
        prices,
        settings.usd_toman_rate,
        nobitex_url=settings.nobitex_base_url,
        wallex_url=settings.wallex_base_url,
        binance_url=settings.binance_base_url,
    )
    wallets = WalletService(db, providers, settings.max_transfers_per_address, settings.cache_ttl_seconds, prices)
    labels = LabelService(db)
    await labels.load()
    graphs = GraphBuilder(wallets, labels, settings.hub_threshold)
    sanctions = SanctionsChecker(
        client,
        settings.trongrid_base_url,
        settings.trongrid_api_key,
        settings.alchemy_api_key,
        offline=_demo_sanctions() if settings.demo_mode else None,
    )
    return Services(
        settings=settings,
        db=db,
        client=client,
        providers=providers,
        prices=prices,
        values=values,
        wallets=wallets,
        labels=labels,
        graphs=graphs,
        tracer=Tracer(wallets, labels, settings.hub_threshold),
        risk=RiskAnalyzer(wallets, labels, graphs, sanctions),
        cases=CaseService(db),
        monitor=MonitorService(db, wallets, poll_limit=settings.page_size),
        links=LinkJobs(LinkAnalyzer(wallets, labels, settings.hub_threshold, sanctions=sanctions)),
        sanctions=sanctions,
    )


def _demo_sanctions() -> dict:
    """Demo: Tether has frozen the mule that kept part of the money."""
    from app.models import Chain
    from app.providers import demo

    frozen = demo.MULES[3]
    return {(Chain.TRON, frozen): SanctionStatus(chain=Chain.TRON, address=frozen, usdt_frozen=True)}
