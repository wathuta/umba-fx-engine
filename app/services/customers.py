from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from app.configs.constants import LEDGER_DIRECTION_CREDIT, LEDGER_REF_CREDIT_ADJUSTMENT
from app.utils.errors import not_found
from app.utils.money import Currency, round_money
from app.db.models import CreditAdjustment, Customer
from app.repositories.balances import ensure_all_balances, get_balance, list_balances
from app.repositories.ledger import append_entry

# Default reason/source for the test-only credit endpoint. A production-grade
# credit flow would take these from an authenticated admin request payload.
_DEFAULT_CREDIT_REASON = "manual_credit"
_DEFAULT_CREDIT_SOURCE = "test_fixture"


def create_customer(session: Session) -> UUID:
    customer = Customer()
    session.add(customer)
    session.flush()
    ensure_all_balances(session, customer.id)
    session.commit()
    return customer.id


def assert_customer_exists(session: Session, customer_id: UUID) -> Customer:
    customer = session.get(Customer, customer_id)
    if customer is None:
        raise not_found("customer_not_found", f"Customer {customer_id} not found.")
    return customer


def get_balances(session: Session, customer_id: UUID) -> dict[Currency, Decimal]:
    assert_customer_exists(session, customer_id)
    return list_balances(session, customer_id)


def credit_balance(session: Session, customer_id: UUID, currency: Currency, amount: Decimal) -> Decimal:
    assert_customer_exists(session, customer_id)
    rounded = round_money(amount, currency)
    balance = get_balance(session, customer_id, currency, for_update=True)
    adjustment = CreditAdjustment(
        customer_id=customer_id,
        currency=currency.value,
        amount=rounded,
        reason=_DEFAULT_CREDIT_REASON,
        source=_DEFAULT_CREDIT_SOURCE,
    )
    session.add(adjustment)
    session.flush()
    append_entry(
        session,
        customer_id=customer_id,
        currency=currency,
        amount=rounded,
        direction=LEDGER_DIRECTION_CREDIT,
        reference_type=LEDGER_REF_CREDIT_ADJUSTMENT,
        reference_id=adjustment.id,
    )
    balance.balance = round_money(balance.balance + rounded, currency)
    session.commit()
    return balance.balance
