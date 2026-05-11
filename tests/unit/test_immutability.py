"""Database-level immutability triggers on append-only tables."""

from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db.models import Execution, LedgerEntry, RateRefresh, RateSnapshot
from app.db.session import IMMUTABLE_TABLES
from app.services.executions import execute_quote, request_hash
from app.services.quotes import create_quote
from app.utils.money import Currency
from tests.unit.helpers import create_customer_with_usd


def test_immutable_tables_list_matches_expected():
    assert set(IMMUTABLE_TABLES) == {
        "quotes",
        "quote_legs",
        "executions",
        "ledger_entries",
        "rate_snapshots",
    }


def _execute_one(client, db_session) -> Execution:
    customer_id = create_customer_with_usd(client)
    quote = create_quote(db_session, UUID(customer_id), Currency.USD, Currency.KES, Decimal("100.00"))
    payload, _ = execute_quote(
        db_session,
        quote.id,
        idempotency_key="immutability-key",
        req_hash=request_hash("POST", "/executions", {"quote_id": str(quote.id)}),
    )
    return db_session.get(Execution, UUID(payload["execution_id"]))


def test_quotes_update_is_rejected(client, db_session, seeded_rates):
    customer_id = create_customer_with_usd(client)
    quote = create_quote(db_session, UUID(customer_id), Currency.USD, Currency.KES, Decimal("100.00"))

    with pytest.raises(IntegrityError, match="quotes is append-only"):
        db_session.execute(
            text("UPDATE quotes SET destination_amount = :new WHERE id = :id"),
            {"new": Decimal("0.00"), "id": quote.id},
        )
        db_session.commit()
    db_session.rollback()


def test_quotes_delete_is_rejected(client, db_session, seeded_rates):
    customer_id = create_customer_with_usd(client)
    quote = create_quote(db_session, UUID(customer_id), Currency.USD, Currency.KES, Decimal("100.00"))

    with pytest.raises(IntegrityError, match="quote_legs is append-only|quotes is append-only"):
        db_session.execute(text("DELETE FROM quotes WHERE id = :id"), {"id": quote.id})
        db_session.commit()
    db_session.rollback()


def test_quote_legs_update_is_rejected(client, db_session, seeded_rates):
    customer_id = create_customer_with_usd(client)
    quote = create_quote(db_session, UUID(customer_id), Currency.USD, Currency.KES, Decimal("100.00"))

    with pytest.raises(IntegrityError, match="quote_legs is append-only"):
        db_session.execute(
            text("UPDATE quote_legs SET executable_rate = :rate WHERE quote_id = :id"),
            {"rate": Decimal("0"), "id": quote.id},
        )
        db_session.commit()
    db_session.rollback()


def test_executions_update_is_rejected(client, db_session, seeded_rates):
    execution = _execute_one(client, db_session)

    with pytest.raises(IntegrityError, match="executions is append-only"):
        db_session.execute(
            text("UPDATE executions SET customer_id = customer_id WHERE id = :id"),
            {"id": execution.id},
        )
        db_session.commit()
    db_session.rollback()


def test_ledger_entries_update_is_rejected(client, db_session, seeded_rates):
    customer_id = create_customer_with_usd(client)
    entry = db_session.query(LedgerEntry).filter_by(customer_id=UUID(customer_id)).first()
    assert entry is not None

    with pytest.raises(IntegrityError, match="ledger_entries is append-only"):
        db_session.execute(
            text("UPDATE ledger_entries SET amount = :amt WHERE id = :id"),
            {"amt": Decimal("0.00"), "id": entry.id},
        )
        db_session.commit()
    db_session.rollback()


def test_ledger_entries_delete_is_rejected(client, db_session, seeded_rates):
    customer_id = create_customer_with_usd(client)
    entry = db_session.query(LedgerEntry).filter_by(customer_id=UUID(customer_id)).first()
    assert entry is not None

    with pytest.raises(IntegrityError, match="ledger_entries is append-only"):
        db_session.execute(text("DELETE FROM ledger_entries WHERE id = :id"), {"id": entry.id})
        db_session.commit()
    db_session.rollback()


def test_rate_snapshots_update_is_rejected(db_session, seeded_rates):
    snapshot = db_session.query(RateSnapshot).first()
    assert snapshot is not None

    with pytest.raises(IntegrityError, match="rate_snapshots is append-only"):
        db_session.execute(
            text("UPDATE rate_snapshots SET mid_rate = :rate WHERE id = :id"),
            {"rate": Decimal("0"), "id": snapshot.id},
        )
        db_session.commit()
    db_session.rollback()


def test_mutable_tables_are_not_affected(db_session, seeded_rates):
    """Sanity check: tables outside the immutable set still accept UPDATE."""
    refresh = db_session.query(RateRefresh).first()
    assert refresh is not None

    db_session.execute(
        text("UPDATE rate_refreshes SET pairs_updated = :p WHERE id = :id"),
        {"p": 99, "id": refresh.id},
    )
    db_session.commit()
    db_session.refresh(refresh)
    assert refresh.pairs_updated == 99
