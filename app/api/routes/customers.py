from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.schemas.customers import BalanceCreditRequest, BalanceResponse, BalancesResponse, CustomerResponse
from app.utils.money import Currency
from app.db.session import get_db
from app.services.customers import create_customer, credit_balance, get_balances

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
