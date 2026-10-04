from fastapi import APIRouter

from app.api import analysis, cases, insights, labels, links, monitor, settings, wallet

router = APIRouter()
for module in (wallet, analysis, links, insights, labels, cases, monitor, settings):
    router.include_router(module.router)
