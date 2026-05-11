from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.schemas.rates import RateRefreshResponse
from app.db.session import get_db
from app.services.rates import refresh_rates

router = APIRouter()


@router.post("/rate-refreshes", response_model=RateRefreshResponse)
def refresh_rates_endpoint(request: Request, session: Session = Depends(get_db)) -> RateRefreshResponse:
    """Audit a provider refresh and update current rates only on success."""
    refresh_id, status, fetched_at, pairs_updated = refresh_rates(session, request_id=request.state.request_id)
    return RateRefreshResponse(
        rate_refresh_id=refresh_id,
        status=status,
        fetched_at=fetched_at.isoformat(),
        pairs_updated=pairs_updated,
    )
