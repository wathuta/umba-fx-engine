"""Ledger repository — the only writer to `ledger_entries`.

Callers should `append_entry` inside the same DB transaction that updates the
materialized `balances` row, so the ledger and the cache always commit together.
"""

from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.configs.constants import LEDGER_DIRECTION_CREDIT, LEDGER_DIRECTION_DEBIT, ZERO_MONEY
from app.db.models import LedgerEntry
from app.utils.money import Currency, round_money


def append_entry(
    session: Session,
    *,
    customer_id: UUID,
    currency: Currency,
    amount: Decimal,
    direction: str,
    reference_type: str,
    reference_id: UUID,
) -> LedgerEntry:
    """Insert one immutable ledger entry. Amount must be positive."""
    if direction not in (LEDGER_DIRECTION_DEBIT, LEDGER_DIRECTION_CREDIT):
        raise ValueError(f"invalid ledger direction: {direction}")
    if amount <= ZERO_MONEY:
        raise ValueError("ledger entry amount must be positive")
    entry = LedgerEntry(
        customer_id=customer_id,
        currency=currency.value,
        amount=amount,
        direction=direction,
        reference_type=reference_type,
        reference_id=reference_id,
    )
    session.add(entry)
    session.flush()
    return entry


def compute_balance(session: Session, customer_id: UUID, currency: Currency) -> Decimal:
    """Sum the ledger for one (customer, currency). Used by reconciliation."""
    credits = session.execute(
        select(func.coalesce(func.sum(LedgerEntry.amount), ZERO_MONEY)).where(
            LedgerEntry.customer_id == customer_id,
            LedgerEntry.currency == currency.value,
            LedgerEntry.direction == LEDGER_DIRECTION_CREDIT,
        )
    ).scalar_one()
    debits = session.execute(
        select(func.coalesce(func.sum(LedgerEntry.amount), ZERO_MONEY)).where(
            LedgerEntry.customer_id == customer_id,
            LedgerEntry.currency == currency.value,
            LedgerEntry.direction == LEDGER_DIRECTION_DEBIT,
        )
    ).scalar_one()
    return round_money(Decimal(credits) - Decimal(debits), currency)
