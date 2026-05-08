from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


def uuid_pk() -> Mapped[str]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)


class Customer(Base):
    __tablename__ = "customers"

    id = uuid_pk()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Balance(Base):
    __tablename__ = "balances"
    __table_args__ = (UniqueConstraint("customer_id", "currency", name="uq_balances_customer_currency"),)

    id = uuid_pk()
    customer_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey("customers.id"), nullable=False, index=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    balance: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False, default=Decimal("0.00"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class RateRefresh(Base):
    __tablename__ = "rate_refreshes"

    id = uuid_pk()
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_base_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    provider_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    pairs_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(512), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RateSnapshot(Base):
    __tablename__ = "rate_snapshots"

    id = uuid_pk()
    rate_refresh_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey("rate_refreshes.id"), nullable=False)
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False, index=True)
    quote_currency: Mapped[str] = mapped_column(String(3), nullable=False, index=True)
    mid_rate: Mapped[Decimal] = mapped_column(Numeric(20, 10), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    raw_payload_hash: Mapped[str] = mapped_column(String(128), nullable=False)
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
    __table_args__ = (UniqueConstraint("base_currency", "quote_currency", name="uq_current_rates_pair"),)

    id = uuid_pk()
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    quote_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    mid_rate: Mapped[Decimal] = mapped_column(Numeric(20, 10), nullable=False)
    buy_spread_bps: Mapped[int] = mapped_column(Integer, nullable=False)
    sell_spread_bps: Mapped[int] = mapped_column(Integer, nullable=False)
    rate_snapshot_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey("rate_snapshots.id"), nullable=False)
    last_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Quote(Base):
    __tablename__ = "quotes"

    id = uuid_pk()
    customer_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey("customers.id"), nullable=False, index=True)
    source_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    destination_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    source_amount: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    destination_amount: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    executable_rate: Mapped[Decimal] = mapped_column(Numeric(20, 10), nullable=False)
    route: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    spread_bps: Mapped[int] = mapped_column(Integer, nullable=False)
    rate_snapshot_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey("rate_snapshots.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Execution(Base):
    __tablename__ = "executions"
    __table_args__ = (UniqueConstraint("quote_id", name="uq_executions_quote_id"),)

    id = uuid_pk()
    quote_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey("quotes.id"), nullable=False)
    customer_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey("customers.id"), nullable=False)
    debit_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    debit_amount: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    credit_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    credit_amount: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (UniqueConstraint("endpoint", "key", name="uq_idempotency_endpoint_key"),)

    id = uuid_pk()
    endpoint: Mapped[str] = mapped_column(String(64), nullable=False)
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    response_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
