from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.errors import not_found
from app.core.money import Currency, round_money
from app.db.models import Customer
from app.repositories.balances import ensure_all_balances, get_balance, list_balances


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
    with session.begin_nested():
        balance = get_balance(session, customer_id, currency, for_update=True)
        balance.balance = round_money(balance.balance + rounded, currency)
    session.commit()
    return balance.balance
