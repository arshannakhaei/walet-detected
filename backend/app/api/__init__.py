from fastapi import APIRouter

from app.api import analysis, cases, insights, labels, wallet

router = APIRouter()
for module in (wallet, analysis, insights, labels, cases):
    router.include_router(module.router)
