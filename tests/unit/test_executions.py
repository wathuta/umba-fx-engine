from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import select

from app.configs.constants import LEDGER_REF_EXECUTION
from app.utils.money import Currency
from app.db.models import Balance, Execution, LedgerEntry, Quote
from app.services.executions import execute_quote, request_hash
from app.services.quotes import create_quote
from tests.unit.helpers import create_customer_with_usd


def test_execute_debits_and_credits_once(client, seeded_rates):
    customer_id = create_customer_with_usd(client)
    quote_id = _quote(client, customer_id)

    response = client.post("/executions", json={"quote_id": quote_id}, headers={"Idempotency-Key": "key-1"})

    assert response.status_code == 200
    body = response.json()
    assert body["debit"] == {"currency": "USD", "amount": "100.00"}
    assert body["credit"] == {"currency": "KES", "amount": "12935.00"}
    assert body["balances"]["USD"] == "900.00"
    assert body["balances"]["KES"] == "12935.00"


def test_execution_requires_idempotency_key(client, seeded_rates):
    customer_id = create_customer_with_usd(client)
    quote_id = _quote(client, customer_id)

    response = client.post("/executions", json={"quote_id": quote_id})

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


def test_quote_can_execute_only_once(client, seeded_rates):
    customer_id = create_customer_with_usd(client)
    quote_id = _quote(client, customer_id)

    first = client.post("/executions", json={"quote_id": quote_id}, headers={"Idempotency-Key": "once-1"})
    second = client.post("/executions", json={"quote_id": quote_id}, headers={"Idempotency-Key": "once-2"})

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["code"] == "quote_already_executed"


def test_idempotency_replay_and_conflict(client, seeded_rates):
    customer_id = create_customer_with_usd(client)
    quote_id = _quote(client, customer_id)

    first = client.post("/executions", json={"quote_id": quote_id}, headers={"Idempotency-Key": "same-key"})
    second = client.post("/executions", json={"quote_id": quote_id}, headers={"Idempotency-Key": "same-key"})
    conflict = client.post(
        "/executions",
        json={"quote_id": "0196f20f-7f6a-7f40-9a0e-dc803f830999"},
        headers={"Idempotency-Key": "same-key"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json() == first.json()
    assert conflict.status_code == 409
    assert client.get(f"/customers/{customer_id}/balances").json()["balances"]["USD"] == "900.00"


def test_expired_quote_rejected(client, db_session, seeded_rates):
    customer_id = create_customer_with_usd(client)
    now = datetime.now(UTC)
    quote = Quote(
        customer_id=UUID(customer_id),
        source_currency=Currency.USD.value,
        destination_currency=Currency.KES.value,
        source_amount=Decimal("100.00"),
        destination_amount=Decimal("12935.00"),
        executable_rate=Decimal("129.3500000000"),
        route=[Currency.USD.value, Currency.KES.value],
        spread_bps=50,
        created_at=now - timedelta(seconds=120),
        expires_at=now - timedelta(seconds=60),
    )
    db_session.add(quote)
    db_session.commit()

    response = client.post("/executions", json={"quote_id": str(quote.id)}, headers={"Idempotency-Key": "expired"})

    assert response.status_code == 409
    assert response.json()["code"] == "quote_expired"


def test_insufficient_funds_leaves_balances_unchanged(client, seeded_rates):
    customer_id = create_customer_with_usd(client, amount="50.00")
    quote_id = _quote(client, customer_id)

    response = client.post("/executions", json={"quote_id": quote_id}, headers={"Idempotency-Key": "poor"})

    assert response.status_code == 409
    balances = client.get(f"/customers/{customer_id}/balances").json()["balances"]
    assert balances["USD"] == "50.00"
    assert balances["KES"] == "0.00"


def test_injected_mid_execute_failure_rolls_back(db_session, seeded_rates, monkeypatch):
    customer_id = UUID(str(_create_customer_with_usd_in_session(db_session)))
    quote = create_quote(db_session, customer_id, Currency.USD, Currency.KES, Decimal("100.00"))
    before = _balances(db_session, customer_id)

    def boom() -> None:
        raise RuntimeError("injected failure after debit")

    monkeypatch.setattr("app.services.executions._after_debit_hook", boom)

    with pytest.raises(RuntimeError):
        execute_quote(
            db_session,
            quote.id,
            "fail-key",
            request_hash("POST", "/executions", {"quote_id": str(quote.id)}),
        )

    assert _balances(db_session, customer_id) == before
    assert db_session.execute(select(Execution).where(Execution.quote_id == quote.id)).scalar_one_or_none() is None
    # The debit ledger entry was written before the hook fired, so the rollback
    # must also reverse it — otherwise the ledger would record money that no
    # execution row backs.
    rolled_back_entries = db_session.execute(
        select(LedgerEntry).where(
            LedgerEntry.customer_id == customer_id,
            LedgerEntry.reference_type == LEDGER_REF_EXECUTION,
        )
    ).scalars().all()
    assert rolled_back_entries == []


def test_same_idempotency_key_across_customers_returns_conflict(client, seeded_rates):
    """A key already used by one customer cannot be reused by another customer."""
    first_customer = create_customer_with_usd(client)
    second_customer = create_customer_with_usd(client)
    first_quote = _quote(client, first_customer)
    second_quote = _quote(client, second_customer)
    shared_key = "cross-customer-key"

    first = client.post("/executions", json={"quote_id": first_quote}, headers={"Idempotency-Key": shared_key})
    second = client.post("/executions", json={"quote_id": second_quote}, headers={"Idempotency-Key": shared_key})

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["code"] == "idempotency_conflict"


def test_execution_rejected_log_includes_correlation_fields(client, seeded_rates, caplog):
    """Pre-flight rejections must emit a structured `execution.rejected` event."""
    import json
    import logging

    customer_id = create_customer_with_usd(client)
    quote_id = _quote(client, customer_id)
    first = client.post("/executions", json={"quote_id": quote_id}, headers={"Idempotency-Key": "log-key"})
    assert first.status_code == 200

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="fx"):
        conflict = client.post(
            "/executions",
            json={"quote_id": "0196f20f-7f6a-7f40-9a0e-dc803f830999"},
            headers={"Idempotency-Key": "log-key"},
        )
    assert conflict.status_code == 409

    rejected_events = [
        json.loads(record.message)
        for record in caplog.records
        if record.levelno == logging.WARNING and '"execution.rejected"' in record.message
    ]
    assert len(rejected_events) == 1
    event = rejected_events[0]
    assert event["event"] == "execution.rejected"
    assert event["error_code"] == "idempotency_conflict"
    assert event["idempotency_key"] == "log-key"
    assert event["outcome"] == "failure"
    assert "quote_id" in event
    assert "request_id" in event


def test_concurrent_same_quote_execution_has_one_success(client, seeded_rates):
    customer_id = create_customer_with_usd(client)
    quote_id = _quote(client, customer_id)

    def attempt(i):
        return client.post(
            "/executions",
            json={"quote_id": quote_id},
            headers={"Idempotency-Key": f"parallel-{i}"},
        ).status_code

    with ThreadPoolExecutor(max_workers=8) as executor:
        statuses = list(executor.map(attempt, range(8)))

    assert statuses.count(200) == 1
    assert statuses.count(409) == 7


def _quote(client, customer_id: str) -> str:
    return client.post(
        "/quotes",
        json={
            "customer_id": customer_id,
            "source_currency": "USD",
            "destination_currency": "KES",
            "source_amount": "100.00",
        },
    ).json()["quote_id"]


def _balances(session, customer_id):
    return {
        row.currency: row.balance
        for row in session.execute(select(Balance).where(Balance.customer_id == customer_id)).scalars()
    }


def _create_customer_with_usd_in_session(session) -> str:
    from app.services.customers import create_customer, credit_balance

    customer_id = create_customer(session)
    credit_balance(session, customer_id, Currency.USD, Decimal("1000.00"))
    return str(customer_id)
