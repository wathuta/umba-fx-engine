from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.schemas import (
    BalanceCreditRequest,
    BalanceResponse,
    BalancesResponse,
    CustomerResponse,
    ExecutionRequest,
    ExecutionResponse,
    HealthResponse,
    QuoteRequest,
    QuoteResponse,
    RateRefreshResponse,
    ReadinessResponse,
)
from app.core.constants import EXECUTIONS_PATH, HTTP_POST
from app.core.errors import ApiError
from app.core.money import Currency
from app.core.observability import metrics_response
from app.db.session import get_db
from app.services.customers import create_customer, credit_balance, get_balances
from app.services.executions import execute_quote, request_hash
from app.services.quotes import create_quote
from app.services.rates import ensure_fresh_rates, refresh_rates

router = APIRouter()


@router.post("/customers", response_model=CustomerResponse, status_code=status.HTTP_201_CREATED)
def create_customer_endpoint(session: Session = Depends(get_db)) -> CustomerResponse:
    return CustomerResponse(customer_id=create_customer(session))


@router.get("/customers/{customer_id}/balances", response_model=BalancesResponse)
def get_balances_endpoint(customer_id: UUID, session: Session = Depends(get_db)) -> BalancesResponse:
    return BalancesResponse(customer_id=customer_id, balances=get_balances(session, customer_id))


@router.post("/customers/{customer_id}/balance-credits", response_model=BalanceResponse)
def credit_balance_endpoint(
    customer_id: UUID,
    payload: BalanceCreditRequest,
    session: Session = Depends(get_db),
) -> BalanceResponse:
    amount = credit_balance(session, customer_id, Currency(payload.currency), payload.amount)
    return BalanceResponse(currency=payload.currency, amount=amount)


@router.post("/quotes", response_model=QuoteResponse)
def create_quote_endpoint(
    payload: QuoteRequest,
    request: Request,
    session: Session = Depends(get_db),
) -> QuoteResponse:
    """Price an immutable quote without reading or mutating customer balances."""
    quote = create_quote(
        session,
        payload.customer_id,
        Currency(payload.source_currency),
        Currency(payload.destination_currency),
        payload.source_amount,
        request_id=request.state.request_id,
    )
    return QuoteResponse(
        quote_id=quote.id,
        source_currency=Currency(quote.source_currency),
        destination_currency=Currency(quote.destination_currency),
        source_amount=quote.source_amount,
        destination_amount=quote.destination_amount,
        executable_rate=quote.executable_rate,
        route=[Currency(value) for value in quote.route],
        expires_at=quote.expires_at.isoformat(),
    )


@router.post("/executions", response_model=ExecutionResponse)
def execute_quote_endpoint(
    payload: ExecutionRequest,
    request: Request,
    session: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict:
    """Execute stored quote terms atomically under the required idempotency key."""
    body = {"quote_id": str(payload.quote_id)}
    result, _ = execute_quote(
        session,
        payload.quote_id,
        idempotency_key or "",
        request_hash(HTTP_POST, EXECUTIONS_PATH, body),
        request_id=request.state.request_id,
    )
    return result


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


@router.get("/healthz", response_model=HealthResponse)
def healthz(session: Session = Depends(get_db)) -> HealthResponse:
    """Report process health while exposing DB and rate freshness details."""
    db_status = "ok"
    rates_status = "ok"
    try:
        session.execute(text("SELECT 1"))
    except Exception:
        db_status = "unhealthy"
    try:
        ensure_fresh_rates(session)
    except ApiError:
        rates_status = "stale"
    return HealthResponse(status=db_status, database=db_status, rates=rates_status)


@router.get("/readyz", response_model=ReadinessResponse)
def readyz(session: Session = Depends(get_db)) -> ReadinessResponse:
    """Report readiness for quote traffic; stale rates make the service not ready."""
    try:
        session.execute(text("SELECT 1"))
    except Exception:
        return ReadinessResponse(status="unhealthy", database="unhealthy", rates="unknown")
    try:
        ensure_fresh_rates(session)
    except ApiError:
        return ReadinessResponse(status="unhealthy", database="ok", rates="stale")
    return ReadinessResponse(status="ok", database="ok", rates="ok")


@router.get("/metrics")
def metrics():
    return metrics_response()
