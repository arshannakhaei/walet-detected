from fastapi import APIRouter

from app.api import analysis, cases, insights, investigation, labels, links, monitor, prices, settings, wallet

router = APIRouter()
for module in (wallet, analysis, links, investigation, insights, labels, cases, monitor, prices, settings):
    router.include_router(module.router)
