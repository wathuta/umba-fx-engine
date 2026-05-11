from fastapi import APIRouter

from app.api.routes.customers import router as customers_router
from app.api.routes.executions import router as executions_router
from app.api.routes.health import router as health_router
from app.api.routes.quotes import router as quotes_router
from app.api.routes.rates import router as rates_router

router = APIRouter()
router.include_router(customers_router)
router.include_router(quotes_router)
router.include_router(executions_router)
router.include_router(rates_router)
router.include_router(health_router)
