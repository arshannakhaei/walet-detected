from fastapi import APIRouter

from app.api import analysis, labels, wallet

router = APIRouter()
for module in (wallet, analysis, labels):
    router.include_router(module.router)
