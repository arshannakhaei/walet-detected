from fastapi import APIRouter

from app.api import analysis, cases, insights, labels, monitor, settings, wallet

router = APIRouter()
for module in (wallet, analysis, insights, labels, cases, monitor, settings):
    router.include_router(module.router)
