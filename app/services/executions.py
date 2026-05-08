"""Quote execution service.

Execution is the only money-moving operation. The service keeps idempotency,
balance mutation, and execution persistence in one transaction so retries and
failures cannot create partial FX transfers.
"""

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import conflict, not_found
from app.core.money import Currency, round_money
from app.core.observability import (
    execution_failure_total,
    execution_success_total,
    idempotency_conflict_total,
    idempotency_replay_total,
    insufficient_funds_total,
    log_event,
    quote_expired_total,
)
from app.db.models import Execution, IdempotencyKey, Quote
from app.repositories.balances import get_balance


def request_hash(method: str, path: str, body: dict) -> str:
    """Bind an idempotency key to the exact execution request payload."""
    payload = json.dumps({"method": method, "path": path, "body": body}, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def _execution_response(execution: Execution, balances: dict[Currency, Decimal]) -> dict:
    return {
        "execution_id": str(execution.id),
        "quote_id": str(execution.quote_id),
        "debit": {"currency": execution.debit_currency, "amount": str(execution.debit_amount)},
        "credit": {"currency": execution.credit_currency, "amount": str(execution.credit_amount)},
        "balances": {currency.value: str(amount) for currency, amount in balances.items()},
    }


def execute_quote(
    session: Session,
    quote_id: UUID,
    idempotency_key: str,
    req_hash: str,
    request_id: str | None = None,
    fail_after_debit: bool = False,
) -> tuple[dict, bool]:
    """Execute stored quote terms exactly once, or return an idempotent replay."""
    if not idempotency_key:
        raise conflict("idempotency_conflict", "Idempotency-Key is required.")
    existing_key = session.execute(
        select(IdempotencyKey).where(IdempotencyKey.endpoint == "POST /executions", IdempotencyKey.key == idempotency_key)
    ).scalar_one_or_none()
    if existing_key is not None:
        if existing_key.request_hash != req_hash:
            idempotency_conflict_total.inc()
            raise conflict("idempotency_conflict", "Idempotency-Key was already used with a different request.")
        if existing_key.completed_at and existing_key.response_payload:
            idempotency_replay_total.inc()
            return existing_key.response_payload, True
        idempotency_conflict_total.inc()
        raise conflict("idempotency_conflict", "Idempotency-Key is already in flight.")

    idem = IdempotencyKey(endpoint="POST /executions", key=idempotency_key, request_hash=req_hash)
    try:
        session.add(idem)
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        idempotency_conflict_total.inc()
        raise conflict("idempotency_conflict", "Idempotency-Key is already in use.") from exc

    try:
        quote = session.execute(select(Quote).where(Quote.id == quote_id).with_for_update()).scalar_one_or_none()
        if quote is None:
            session.rollback()
            raise not_found("quote_not_found", f"Quote {quote_id} not found.")
        existing_execution = session.execute(select(Execution).where(Execution.quote_id == quote_id)).scalar_one_or_none()
        if existing_execution is not None:
            session.rollback()
            raise conflict("quote_already_executed", "Quote has already been executed.")
        now = datetime.now(UTC)
        expires_at = quote.expires_at if quote.expires_at.tzinfo else quote.expires_at.replace(tzinfo=UTC)
        if now >= expires_at:
            session.rollback()
            quote_expired_total.inc()
            raise conflict("quote_expired", "Quote has expired.")

        source_currency = Currency(quote.source_currency)
        destination_currency = Currency(quote.destination_currency)
        currencies = sorted((source_currency, destination_currency), key=lambda c: c.value)
        # Create missing balance rows before row locks so lock order stays stable.
        for currency in currencies:
            get_balance(session, quote.customer_id, currency, for_update=False)
        locked = {currency: get_balance(session, quote.customer_id, currency, for_update=True) for currency in currencies}
        source_balance = locked[source_currency]
        destination_balance = locked[destination_currency]
        if source_balance.balance < quote.source_amount:
            session.rollback()
            insufficient_funds_total.inc()
            raise conflict("insufficient_funds", "Insufficient source balance.")
        source_balance.balance = round_money(source_balance.balance - quote.source_amount, source_currency)
        # Test hook proves the debit and credit are protected by the same DB transaction.
        if fail_after_debit:
            raise RuntimeError("injected failure after debit")
        destination_balance.balance = round_money(destination_balance.balance + quote.destination_amount, destination_currency)
        execution = Execution(
            quote_id=quote.id,
            customer_id=quote.customer_id,
            debit_currency=quote.source_currency,
            debit_amount=quote.source_amount,
            credit_currency=quote.destination_currency,
            credit_amount=quote.destination_amount,
        )
        session.add(execution)
        session.flush()
        balances = {
            source_currency: source_balance.balance,
            destination_currency: destination_balance.balance,
        }
        payload = _execution_response(execution, balances)
        idem.response_payload = payload
        idem.status_code = 200
        idem.completed_at = datetime.now(UTC)
        session.commit()
        execution_success_total.inc()
        log_event(
            "execution.completed",
            request_id=request_id,
            customer_id=quote.customer_id,
            quote_id=quote.id,
            execution_id=execution.id,
            idempotency_key=idempotency_key,
            source_currency=quote.source_currency,
            destination_currency=quote.destination_currency,
            source_amount=str(quote.source_amount),
            destination_amount=str(quote.destination_amount),
            executable_rate=str(quote.executable_rate),
            outcome="success",
        )
        return payload, False
    except Exception:
        session.rollback()
        execution_failure_total.inc()
        raise
