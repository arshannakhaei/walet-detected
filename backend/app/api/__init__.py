from fastapi import APIRouter

from app.api import analysis, cases, insights, labels, monitor, wallet

router = APIRouter()
for module in (wallet, analysis, insights, labels, cases, monitor):
    router.include_router(module.router)
