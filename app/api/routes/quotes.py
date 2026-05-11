from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.schemas.quotes import QuoteRequest, QuoteResponse
from app.utils.money import Currency
from app.db.session import get_db
from app.services.quotes import create_quote

router = APIRouter()


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
