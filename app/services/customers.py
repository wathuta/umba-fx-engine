from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from app.configs.constants import LEDGER_DIRECTION_CREDIT, LEDGER_REF_CREDIT_ADJUSTMENT
from app.db.models import CreditAdjustment, Customer
from app.repositories.balances import ensure_all_balances, ensure_balance, get_balance_for_update, list_balances
from app.repositories.ledger import append_entry
from app.utils.errors import not_found
from app.utils.money import Currency, round_money

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

    ensure_balance(session, customer_id, currency)
    balance = get_balance_for_update(session, customer_id, currency)
    adjustment = CreditAdjustment(
        customer_id=customer_id,
        currency=currency.value,
        amount=rounded,
        reason=_DEFAULT_CREDIT_REASON,
        source=_DEFAULT_CREDIT_SOURCE,
    )
    session.add(adjustment)
    session.flush()
    # Write to the ledger — the source of truth for every money movement.
    # Without this, the balance cache below would have no matching audit row
    # and the invariant balance == SUM(credits) - SUM(debits) would break.
    # reference_id ties this ledger entry back to the CreditAdjustment record.
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
