from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.schemas.health import HealthResponse, ReadinessResponse
from app.utils.observability import metrics_response
from app.db.session import get_db
from app.services.rates import rates_freshness_status

router = APIRouter()


@router.get("/healthz", response_model=HealthResponse)
def healthz() -> HealthResponse:
    """Report process liveness only."""
    return HealthResponse(status="ok")


@router.get("/readyz", response_model=ReadinessResponse)
def readyz(session: Session = Depends(get_db)) -> ReadinessResponse:
    """Report readiness for quote traffic; stale rates make the service not ready."""
    try:
        session.execute(text("SELECT 1"))
    except Exception:
        return ReadinessResponse(status="not_ready", database="unhealthy", rates="unknown")
    rates_status = rates_freshness_status(session)
    if rates_status != "fresh":
        return ReadinessResponse(status="not_ready", database="ok", rates=rates_status)
    return ReadinessResponse(status="ready", database="ok", rates="fresh")


@router.get("/metrics")
def metrics():
    return metrics_response()
