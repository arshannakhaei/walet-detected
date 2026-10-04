from fastapi import APIRouter

from app.api import analysis, cases, insights, labels, links, monitor, prices, settings, wallet

router = APIRouter()
for module in (wallet, analysis, links, insights, labels, cases, monitor, prices, settings):
    router.include_router(module.router)
