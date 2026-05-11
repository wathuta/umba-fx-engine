from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.configs.constants import ZERO_MONEY
from app.utils.money import Currency, round_money
from app.db.models import UQ_BALANCES_CUSTOMER_CURRENCY, Balance


def ensure_balance(session: Session, customer_id: UUID, currency: Currency) -> Balance:
    stmt = (
        insert(Balance)
        .values(customer_id=customer_id, currency=currency.value, balance=ZERO_MONEY)
        .on_conflict_do_nothing(constraint=UQ_BALANCES_CUSTOMER_CURRENCY)
    )
    session.execute(stmt)
    session.flush()
    return get_balance(session, customer_id, currency, for_update=False)


def ensure_all_balances(session: Session, customer_id: UUID) -> None:
    for currency in Currency:
        ensure_balance(session, customer_id, currency)


def get_balance(session: Session, customer_id: UUID, currency: Currency, for_update: bool = False) -> Balance:
    stmt = select(Balance).where(Balance.customer_id == customer_id, Balance.currency == currency.value)
    if for_update:
        stmt = stmt.with_for_update()
    result = session.execute(stmt).scalar_one_or_none()
    if result is None:
        result = ensure_balance(session, customer_id, currency)
        if for_update:
            result = session.execute(stmt.with_for_update()).scalar_one()
    return result


def list_balances(session: Session, customer_id: UUID) -> dict[Currency, Decimal]:
    ensure_all_balances(session, customer_id)
    rows = session.execute(select(Balance).where(Balance.customer_id == customer_id)).scalars().all()
    values = {Currency(row.currency): row.balance for row in rows}
    return {currency: round_money(values.get(currency, ZERO_MONEY), currency) for currency in Currency}
