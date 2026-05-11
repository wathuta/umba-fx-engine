"""Ledger is the source of truth; balances is its materialized cache.

These tests prove the invariant: for every (customer, currency), the sum of
ledger entries equals the stored balance after any operation that moves money.
"""

from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.exc import DBAPIError

from app.configs.constants import (
    LEDGER_DIRECTION_CREDIT,
    LEDGER_DIRECTION_DEBIT,
    LEDGER_REF_CREDIT_ADJUSTMENT,
    LEDGER_REF_EXECUTION,
)
from app.db.models import Balance, CreditAdjustment, LedgerEntry
from app.repositories.ledger import compute_balance
from app.utils.money import Currency
from tests.unit.helpers import create_customer_with_usd


def _balance(db_session, customer_id, currency):
    row = db_session.execute(
        select(Balance).where(Balance.customer_id == UUID(customer_id), Balance.currency == currency.value)
    ).scalar_one()
    return row.balance


def _assert_balance_matches_ledger(db_session, customer_id):
    for currency in Currency:
        ledger_sum = compute_balance(db_session, UUID(customer_id), currency)
        stored = _balance(db_session, customer_id, currency)
        assert stored == ledger_sum, f"{currency.value}: stored={stored}, ledger={ledger_sum}"


def test_credit_balance_writes_ledger_entry(client, db_session):
    customer_id = create_customer_with_usd(client, amount="500.00")
    entries = db_session.execute(
        select(LedgerEntry).where(LedgerEntry.customer_id == UUID(customer_id))
    ).scalars().all()
    assert len(entries) == 1
    entry = entries[0]
    assert entry.direction == LEDGER_DIRECTION_CREDIT
    assert entry.amount == Decimal("500.00")
    assert entry.currency == Currency.USD.value
    assert entry.reference_type == LEDGER_REF_CREDIT_ADJUSTMENT
    # The ledger reference must resolve to a real CreditAdjustment row — no dead pointers.
    adjustment = db_session.execute(
        select(CreditAdjustment).where(CreditAdjustment.id == entry.reference_id)
    ).scalar_one()
    assert adjustment.customer_id == UUID(customer_id)
    assert adjustment.currency == Currency.USD.value
    assert adjustment.amount == Decimal("500.00")
    _assert_balance_matches_ledger(db_session, customer_id)


def test_execute_writes_paired_debit_and_credit(client, db_session, seeded_rates):
    customer_id = create_customer_with_usd(client, amount="1000.00")
    quote_id = client.post(
        "/quotes",
        json={
            "customer_id": customer_id,
            "source_currency": "USD",
            "destination_currency": "KES",
            "source_amount": "100.00",
        },
    ).json()["quote_id"]
    client.post("/executions", json={"quote_id": quote_id}, headers={"Idempotency-Key": "key-1"})

    entries = db_session.execute(
        select(LedgerEntry)
        .where(LedgerEntry.customer_id == UUID(customer_id), LedgerEntry.reference_type == LEDGER_REF_EXECUTION)
        .order_by(LedgerEntry.direction)
    ).scalars().all()
    assert len(entries) == 2
    credit, debit = entries
    assert credit.direction == LEDGER_DIRECTION_CREDIT
    assert credit.currency == Currency.KES.value
    assert credit.amount == Decimal("12935.00")
    assert debit.direction == LEDGER_DIRECTION_DEBIT
    assert debit.currency == Currency.USD.value
    assert debit.amount == Decimal("100.00")
    assert credit.reference_id == debit.reference_id
    _assert_balance_matches_ledger(db_session, customer_id)


def test_ledger_entries_cannot_be_updated(client, db_session):
    customer_id = create_customer_with_usd(client, amount="100.00")
    entry = db_session.execute(
        select(LedgerEntry).where(LedgerEntry.customer_id == UUID(customer_id))
    ).scalar_one()

    with pytest.raises(DBAPIError, match="immutable"):
        db_session.execute(
            update(LedgerEntry).where(LedgerEntry.id == entry.id).values(amount=Decimal("999.99"))
        )
        db_session.flush()


def test_ledger_entries_cannot_be_deleted(client, db_session):
    customer_id = create_customer_with_usd(client, amount="100.00")
    entry = db_session.execute(
        select(LedgerEntry).where(LedgerEntry.customer_id == UUID(customer_id))
    ).scalar_one()

    with pytest.raises(DBAPIError, match="immutable"):
        db_session.execute(delete(LedgerEntry).where(LedgerEntry.id == entry.id))
        db_session.flush()
