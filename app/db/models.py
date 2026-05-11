from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import (
    DDL,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    event,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.configs.constants import ZERO_MONEY
from app.db.session import Base

# String lengths are storage constraints, not arbitrary formatting choices.
CURRENCY_CODE_LENGTH = 3
ERROR_CODE_MAX_LENGTH = 64
ERROR_MESSAGE_MAX_LENGTH = 512
IDEMPOTENCY_ENDPOINT_MAX_LENGTH = 64
IDEMPOTENCY_KEY_MAX_LENGTH = 255
PROVIDER_NAME_MAX_LENGTH = 64
RAW_PAYLOAD_HASH_LENGTH = 128
REFRESH_STATUS_MAX_LENGTH = 32
REQUEST_HASH_LENGTH = 128

# Numeric precision used for stored money and rates.
NUMERIC_PRECISION = 20
MONEY_SCALE = 2
RATE_SCALE = 10

# Constraint names referenced by both DDL (models) and DML (upsert/insert calls).
UQ_BALANCES_CUSTOMER_CURRENCY = "uq_balances_customer_currency"
UQ_CURRENT_RATES_PAIR = "uq_current_rates_pair"


def uuid_pk() -> Mapped[str]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)


class Customer(Base):
    __tablename__ = "customers"

    id = uuid_pk()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Balance(Base):
    __tablename__ = "balances"
    __table_args__ = (
        UniqueConstraint("customer_id", "currency", name=UQ_BALANCES_CUSTOMER_CURRENCY),
        CheckConstraint("balance >= 0", name="ck_balances_non_negative"),
    )

    id = uuid_pk()
    customer_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customers.id"),
        nullable=False,
        index=True,
    )
    currency: Mapped[str] = mapped_column(String(CURRENCY_CODE_LENGTH), nullable=False)
    balance: Mapped[Decimal] = mapped_column(
        Numeric(NUMERIC_PRECISION, MONEY_SCALE),
        nullable=False,
        default=ZERO_MONEY,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class RateRefresh(Base):
    __tablename__ = "rate_refreshes"

    id = uuid_pk()
    provider: Mapped[str] = mapped_column(String(PROVIDER_NAME_MAX_LENGTH), nullable=False)
    status: Mapped[str] = mapped_column(String(REFRESH_STATUS_MAX_LENGTH), nullable=False)
    provider_base_currency: Mapped[str | None] = mapped_column(String(CURRENCY_CODE_LENGTH), nullable=True)
    provider_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    pairs_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(ERROR_CODE_MAX_LENGTH), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(ERROR_MESSAGE_MAX_LENGTH), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RateSnapshot(Base):
    __tablename__ = "rate_snapshots"

    id = uuid_pk()
    rate_refresh_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey("rate_refreshes.id"), nullable=False)
    base_currency: Mapped[str] = mapped_column(String(CURRENCY_CODE_LENGTH), nullable=False, index=True)
    quote_currency: Mapped[str] = mapped_column(String(CURRENCY_CODE_LENGTH), nullable=False, index=True)
    mid_rate: Mapped[Decimal] = mapped_column(Numeric(NUMERIC_PRECISION, RATE_SCALE), nullable=False)
    provider: Mapped[str] = mapped_column(String(PROVIDER_NAME_MAX_LENGTH), nullable=False)
    provider_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    raw_payload_hash: Mapped[str] = mapped_column(String(RAW_PAYLOAD_HASH_LENGTH), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        Index(
            "ix_rate_snapshots_pair_fetched_at_desc",
            "base_currency",
            "quote_currency",
            fetched_at.desc(),
        ),
    )


class CurrentRate(Base):
    __tablename__ = "current_rates"
    __table_args__ = (UniqueConstraint("base_currency", "quote_currency", name=UQ_CURRENT_RATES_PAIR),)

    id = uuid_pk()
    base_currency: Mapped[str] = mapped_column(String(CURRENCY_CODE_LENGTH), nullable=False)
    quote_currency: Mapped[str] = mapped_column(String(CURRENCY_CODE_LENGTH), nullable=False)
    mid_rate: Mapped[Decimal] = mapped_column(Numeric(NUMERIC_PRECISION, RATE_SCALE), nullable=False)
    buy_spread_bps: Mapped[int] = mapped_column(Integer, nullable=False)
    sell_spread_bps: Mapped[int] = mapped_column(Integer, nullable=False)
    rate_snapshot_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey("rate_snapshots.id"), nullable=False)
    last_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Quote(Base):
    __tablename__ = "quotes"

    id = uuid_pk()
    customer_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customers.id"),
        nullable=False,
        index=True,
    )
    source_currency: Mapped[str] = mapped_column(String(CURRENCY_CODE_LENGTH), nullable=False)
    destination_currency: Mapped[str] = mapped_column(String(CURRENCY_CODE_LENGTH), nullable=False)
    source_amount: Mapped[Decimal] = mapped_column(Numeric(NUMERIC_PRECISION, MONEY_SCALE), nullable=False)
    destination_amount: Mapped[Decimal] = mapped_column(Numeric(NUMERIC_PRECISION, MONEY_SCALE), nullable=False)
    executable_rate: Mapped[Decimal] = mapped_column(Numeric(NUMERIC_PRECISION, RATE_SCALE), nullable=False)
    route: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    spread_bps: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class QuoteLeg(Base):
    """Per-leg pricing for quote audit. Rows are never updated.

    One row per leg in route order, saved in the same transaction as the
    parent `Quote`. Stores the math at the time the quote was made so we can
    rebuild a quote later even if `current_rates` has changed.
    """

    __tablename__ = "quote_legs"
    __table_args__ = (
        UniqueConstraint("quote_id", "position", name="uq_quote_legs_quote_position"),
    )

    id = uuid_pk()
    quote_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("quotes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    source_currency: Mapped[str] = mapped_column(String(CURRENCY_CODE_LENGTH), nullable=False)
    destination_currency: Mapped[str] = mapped_column(String(CURRENCY_CODE_LENGTH), nullable=False)
    mid_rate: Mapped[Decimal] = mapped_column(Numeric(NUMERIC_PRECISION, RATE_SCALE), nullable=False)
    executable_rate: Mapped[Decimal] = mapped_column(Numeric(NUMERIC_PRECISION, RATE_SCALE), nullable=False)
    spread_side: Mapped[str] = mapped_column(String(8), nullable=False)
    spread_bps: Mapped[int] = mapped_column(Integer, nullable=False)
    rate_snapshot_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey("rate_snapshots.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Execution(Base):
    """Identity and linkage record. The actual money movement is the two paired
    `ledger_entries` rows referencing this execution's id — that is the single
    source of truth for what changed hands.
    """

    __tablename__ = "executions"
    __table_args__ = (UniqueConstraint("quote_id", name="uq_executions_quote_id"),)

    id = uuid_pk()
    quote_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey("quotes.id"), nullable=False)
    customer_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey("customers.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# Reason and source strings are short codes consumers can grep against.
CREDIT_REASON_MAX_LENGTH = 64
CREDIT_SOURCE_MAX_LENGTH = 32


class CreditAdjustment(Base):
    """Manual balance credit record. Resolves the `reference_id` on credit
    ledger entries so every ledger row points at a real audit record.
    """

    __tablename__ = "credit_adjustments"

    id = uuid_pk()
    customer_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customers.id"),
        nullable=False,
        index=True,
    )
    currency: Mapped[str] = mapped_column(String(CURRENCY_CODE_LENGTH), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(NUMERIC_PRECISION, MONEY_SCALE), nullable=False)
    reason: Mapped[str] = mapped_column(String(CREDIT_REASON_MAX_LENGTH), nullable=False)
    source: Mapped[str] = mapped_column(String(CREDIT_SOURCE_MAX_LENGTH), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LedgerEntry(Base):
    """Append-only money-movement record.

    Source of truth for balances — every credit_balance and execute_quote
    inserts one or more entries here. `balances` is a materialized cache and
    must always equal `SUM(credit) - SUM(debit)` per (customer, currency).
    Rows are never updated or deleted; corrections insert a reversing entry.
    """

    __tablename__ = "ledger_entries"
    __table_args__ = (
        UniqueConstraint(
            "reference_type",
            "reference_id",
            "direction",
            name="uq_ledger_entries_reference_direction",
        ),
        Index("ix_ledger_entries_customer_currency", "customer_id", "currency"),
        CheckConstraint("amount > 0", name="ck_ledger_entries_positive_amount"),
        CheckConstraint("direction IN ('debit', 'credit')", name="ck_ledger_entries_direction"),
    )

    id = uuid_pk()
    customer_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customers.id"),
        nullable=False,
        index=True,
    )
    currency: Mapped[str] = mapped_column(String(CURRENCY_CODE_LENGTH), nullable=False)
    amount: Mapped[Decimal] = mapped_column(
        Numeric(NUMERIC_PRECISION, MONEY_SCALE),
        nullable=False,
    )
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    reference_type: Mapped[str] = mapped_column(String(32), nullable=False)
    reference_id: Mapped[str] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint("endpoint", "key", name="uq_idempotency_endpoint_key"),
        Index("ix_idempotency_keys_created_at", "created_at"),
    )

    id = uuid_pk()
    endpoint: Mapped[str] = mapped_column(String(IDEMPOTENCY_ENDPOINT_MAX_LENGTH), nullable=False)
    key: Mapped[str] = mapped_column(String(IDEMPOTENCY_KEY_MAX_LENGTH), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(REQUEST_HASH_LENGTH), nullable=False)
    response_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# DB-level append-only enforcement.
#
# These tables capture audit history that must never be UPDATEd or DELETEd
# once committed. The Postgres trigger below raises so a stray query, ad-hoc
# fix, or future migration cannot silently corrupt the audit trail. Tables
# that are intentionally mutable (`balances`, `idempotency_keys`,
# `rate_refreshes`, `current_rates`) are not in this list.
_IMMUTABLE_TABLES = (LedgerEntry, CreditAdjustment, Execution, Quote, QuoteLeg, RateSnapshot)

_REJECT_MODIFICATION_FN = DDL(
    """
    CREATE OR REPLACE FUNCTION reject_modification() RETURNS trigger AS $$
    BEGIN
        RAISE EXCEPTION '%% rows are immutable', TG_TABLE_NAME;
    END;
    $$ LANGUAGE plpgsql;
    """
)
event.listen(Base.metadata, "before_create", _REJECT_MODIFICATION_FN)

for _immutable_table_class in _IMMUTABLE_TABLES:
    _table_name = _immutable_table_class.__tablename__
    _trigger_ddl = DDL(
        f"CREATE TRIGGER {_table_name}_immutable "
        f"BEFORE UPDATE OR DELETE ON {_table_name} "
        f"FOR EACH ROW EXECUTE FUNCTION reject_modification()"
    )
    event.listen(_immutable_table_class.__table__, "after_create", _trigger_ddl)
