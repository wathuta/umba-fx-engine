"""Quote execution service.

Execution is the only money-moving operation. The service keeps idempotency,
balance mutation, and execution persistence in one transaction so retries and
failures cannot create partial FX transfers.
"""

import hashlib
import json
import logging
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.schemas.executions import ExecutionResponse, LegResponse
from app.configs.constants import (
    EXECUTIONS_PATH,
    HTTP_POST,
    LEDGER_DIRECTION_CREDIT,
    LEDGER_DIRECTION_DEBIT,
    LEDGER_REF_EXECUTION,
    OUTCOME_FAILURE,
    OUTCOME_SUCCESS,
)
from app.db.models import Execution, IdempotencyKey, LedgerEntry, Quote
from app.repositories.balances import ensure_balance, get_balance_for_update
from app.repositories.ledger import append_entry
from app.utils.errors import ApiError, conflict, not_found, validation_error
from app.utils.money import Currency, round_money
from app.utils.observability import (
    execution_failure_total,
    execution_latency_ms,
    execution_success_total,
    idempotency_conflict_total,
    idempotency_replay_total,
    insufficient_funds_total,
    log_event,
    quote_expired_total,
)

# Error codes — referenced at raise sites and in EXECUTION_REJECTION_CODES below,
# so they live as named constants to keep the two sites in sync.
ERROR_IDEMPOTENCY_CONFLICT = "idempotency_conflict"
ERROR_IDEMPOTENCY_KEY_MISSING = "idempotency_key_missing"
ERROR_QUOTE_NOT_FOUND = "quote_not_found"
ERROR_QUOTE_ALREADY_EXECUTED = "quote_already_executed"
ERROR_QUOTE_EXPIRED = "quote_expired"
ERROR_INSUFFICIENT_FUNDS = "insufficient_funds"

# Client-caused failures inside the main try block. They share the failure
# counter but log at WARNING as `execution.rejected`, leaving `execution.failed`
# at ERROR for true system faults.
EXECUTION_REJECTION_CODES = frozenset(
    {ERROR_QUOTE_NOT_FOUND, ERROR_QUOTE_ALREADY_EXECUTED, ERROR_QUOTE_EXPIRED, ERROR_INSUFFICIENT_FUNDS}
)

# Endpoint identifier stored on every idempotency row; must match routes.py.
_EXECUTIONS_ENDPOINT = f"{HTTP_POST} {EXECUTIONS_PATH}"


def _after_debit_hook() -> None:
    """No-op in production. Tests override via monkeypatch to prove rollback."""
    return None


def _log_rejected(
    error_code: str,
    *,
    quote_id: UUID,
    idempotency_key: str,
    request_id: str | None,
) -> None:
    """Emit a structured log for pre-flight rejections so oncall can grep them."""
    log_event(
        "execution.rejected",
        level=logging.WARNING,
        request_id=request_id,
        quote_id=quote_id,
        idempotency_key=idempotency_key,
        error_code=error_code,
        outcome=OUTCOME_FAILURE,
    )


def request_hash(method: str, path: str, body: dict) -> str:
    """Bind an idempotency key to the exact execution request payload."""
    payload = json.dumps({"method": method, "path": path, "body": body}, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def _execution_response(
    execution: Execution,
    debit_entry: LedgerEntry,
    credit_entry: LedgerEntry,
    balances: dict[Currency, Decimal],
) -> dict:
    # Build the Pydantic model so DecimalStringModel's serializer drives the
    # Decimal → string conversion; dump in JSON mode for the JSONB column.
    return ExecutionResponse(
        execution_id=execution.id,
        quote_id=execution.quote_id,
        debit=LegResponse(currency=Currency(debit_entry.currency), amount=debit_entry.amount),
        credit=LegResponse(currency=Currency(credit_entry.currency), amount=credit_entry.amount),
        balances=balances,
    ).model_dump(mode="json")


@execution_latency_ms.time()
def execute_quote(
    session: Session,
    quote_id: UUID,
    idempotency_key: str,
    req_hash: str,
    request_id: str | None = None,
) -> tuple[dict, bool]:
    """Execute stored quote terms exactly once, or return an idempotent replay."""
    if not idempotency_key:
        _log_rejected(
            ERROR_IDEMPOTENCY_KEY_MISSING,
            quote_id=quote_id,
            idempotency_key=idempotency_key,
            request_id=request_id,
        )
        raise validation_error("Idempotency-Key is required.")
    existing_key = session.execute(
        select(IdempotencyKey).where(
            IdempotencyKey.endpoint == _EXECUTIONS_ENDPOINT,
            IdempotencyKey.key == idempotency_key,
        )
    ).scalar_one_or_none()
    if existing_key is not None:
        if existing_key.request_hash != req_hash:
            idempotency_conflict_total.inc()
            _log_rejected(
                ERROR_IDEMPOTENCY_CONFLICT,
                quote_id=quote_id,
                idempotency_key=idempotency_key,
                request_id=request_id,
            )
            raise conflict(
                ERROR_IDEMPOTENCY_CONFLICT,
                "Idempotency-Key was already used with a different request.",
            )
        if existing_key.completed_at and existing_key.response_payload:
            idempotency_replay_total.inc()
            return existing_key.response_payload, True

    idem = IdempotencyKey(endpoint=_EXECUTIONS_ENDPOINT, key=idempotency_key, request_hash=req_hash)
    try:
        session.add(idem)
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        idempotency_conflict_total.inc()
        _log_rejected(
            ERROR_IDEMPOTENCY_CONFLICT,
            quote_id=quote_id,
            idempotency_key=idempotency_key,
            request_id=request_id,
        )
        raise conflict(ERROR_IDEMPOTENCY_CONFLICT, "Idempotency-Key is already in use.") from exc

    quote: Quote | None = None
    try:
        quote = session.execute(select(Quote).where(Quote.id == quote_id).with_for_update()).scalar_one_or_none()
        if quote is None:
            session.rollback()
            raise not_found(ERROR_QUOTE_NOT_FOUND, f"Quote {quote_id} not found.")
        existing_execution = session.execute(
            select(Execution).where(Execution.quote_id == quote_id)
        ).scalar_one_or_none()
        if existing_execution is not None:
            session.rollback()
            raise conflict(ERROR_QUOTE_ALREADY_EXECUTED, "Quote has already been executed.")
        now = datetime.now(UTC)
        expires_at = quote.expires_at if quote.expires_at.tzinfo else quote.expires_at.replace(tzinfo=UTC)
        if now >= expires_at:
            session.rollback()
            quote_expired_total.inc()
            raise conflict(ERROR_QUOTE_EXPIRED, "Quote has expired.")

        source_currency = Currency(quote.source_currency)
        destination_currency = Currency(quote.destination_currency)
        currencies = sorted((source_currency, destination_currency), key=lambda c: c.value)
        # Create missing balance rows before row locks so lock order is predictable.
        for currency in currencies:
            ensure_balance(session, quote.customer_id, currency)
        locked = {
            currency: get_balance_for_update(session, quote.customer_id, currency) for currency in currencies
        }
        source_balance = locked[source_currency]
        destination_balance = locked[destination_currency]
        if source_balance.balance < quote.source_amount:
            session.rollback()
            insufficient_funds_total.inc()
            raise conflict(ERROR_INSUFFICIENT_FUNDS, "Insufficient source balance.")
        execution = Execution(quote_id=quote.id, customer_id=quote.customer_id)
        session.add(execution)
        session.flush()
        # Ledger entries are the source of truth; balance updates below are the materialized cache.
        debit_entry = append_entry(
            session,
            customer_id=quote.customer_id,
            currency=source_currency,
            amount=quote.source_amount,
            direction=LEDGER_DIRECTION_DEBIT,
            reference_type=LEDGER_REF_EXECUTION,
            reference_id=execution.id,
        )
        source_balance.balance = round_money(source_balance.balance - quote.source_amount, source_currency)
        # Test hook proves the debit and credit are protected by the same DB transaction.
        _after_debit_hook()
        credit_entry = append_entry(
            session,
            customer_id=quote.customer_id,
            currency=destination_currency,
            amount=quote.destination_amount,
            direction=LEDGER_DIRECTION_CREDIT,
            reference_type=LEDGER_REF_EXECUTION,
            reference_id=execution.id,
        )
        destination_balance.balance = round_money(
            destination_balance.balance + quote.destination_amount,
            destination_currency,
        )
        balances = {
            source_currency: source_balance.balance,
            destination_currency: destination_balance.balance,
        }
        payload = _execution_response(execution, debit_entry, credit_entry, balances)
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
            outcome=OUTCOME_SUCCESS,
        )
        return payload, False
    except Exception as exc:
        session.rollback()
        execution_failure_total.inc()
        error_code = exc.code if isinstance(exc, ApiError) else "internal_error"
        is_rejection = error_code in EXECUTION_REJECTION_CODES
        log_event(
            "execution.rejected" if is_rejection else "execution.failed",
            level=logging.WARNING if is_rejection else logging.ERROR,
            request_id=request_id,
            quote_id=quote_id,
            customer_id=quote.customer_id if quote is not None else None,
            idempotency_key=idempotency_key,
            error_code=error_code,
            outcome=OUTCOME_FAILURE,
        )
        raise
