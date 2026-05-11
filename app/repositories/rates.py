"""Persistence helpers for rate refresh reads and writes."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import CurrentRate, RateRefresh, RateSnapshot, UQ_CURRENT_RATES_PAIR
from app.utils.money import Currency


@dataclass(frozen=True)
class RateRow:
    base_currency: str
    quote_currency: str
    mid_rate: Decimal


@dataclass(frozen=True)
class CurrentRateBook:
    """Snapshot of all current rates loaded in one read."""

    rates: dict[tuple[Currency, Currency], CurrentRate]
    last_updated_at: datetime | None


def create_refresh_success(
    session: Session,
    *,
    provider: str,
    provider_base_currency: str,
    provider_timestamp: datetime | None,
    fetched_at: datetime,
    raw_payload_hash: str,
    buy_spread_bps: int,
    sell_spread_bps: int,
    pairs_updated: int,
    duration_ms: int,
    rate_rows: Iterable[RateRow],
) -> RateRefresh:
    """Persist a successful refresh and the immutable snapshot history."""
    refresh = RateRefresh(
        provider=provider,
        status="completed",
        provider_base_currency=provider_base_currency,
        provider_timestamp=provider_timestamp,
        fetched_at=fetched_at,
        pairs_updated=pairs_updated,
        duration_ms=duration_ms,
    )
    session.add(refresh)
    session.flush()
    _store_pair_rows(
        session,
        refresh_id=refresh.id,
        provider=provider,
        provider_timestamp=provider_timestamp,
        fetched_at=fetched_at,
        raw_payload_hash=raw_payload_hash,
        buy_spread_bps=buy_spread_bps,
        sell_spread_bps=sell_spread_bps,
        rate_rows=rate_rows,
    )
    return refresh


def create_refresh_failure(
    session: Session,
    *,
    provider: str,
    fetched_at: datetime,
    error_code: str,
    error_message: str,
    duration_ms: int,
) -> RateRefresh:
    """Persist a failed refresh attempt for audit and troubleshooting."""
    refresh = RateRefresh(
        provider=provider,
        status="failed",
        provider_base_currency=None,
        provider_timestamp=None,
        fetched_at=fetched_at,
        pairs_updated=0,
        error_code=error_code,
        error_message=error_message,
        duration_ms=duration_ms,
    )
    session.add(refresh)
    session.flush()
    return refresh


def get_latest_current_rate(session: Session) -> CurrentRate | None:
    """Return the newest current rate row, or `None` when rates are empty."""
    return session.execute(
        select(CurrentRate).order_by(CurrentRate.last_updated_at.desc()).limit(1)
    ).scalar_one_or_none()


def load_current_rate_book(session: Session) -> CurrentRateBook:
    """Load all current rates in one read so quote pricing sees one snapshot."""
    rows = session.execute(select(CurrentRate)).scalars().all()
    rates = {(Currency(row.base_currency), Currency(row.quote_currency)): row for row in rows}
    latest = max((row.last_updated_at for row in rows), default=None)
    return CurrentRateBook(rates=rates, last_updated_at=latest)


def _store_pair_rows(
    session: Session,
    *,
    refresh_id,
    provider: str,
    provider_timestamp: datetime | None,
    fetched_at: datetime,
    raw_payload_hash: str,
    buy_spread_bps: int,
    sell_spread_bps: int,
    rate_rows: Iterable[RateRow],
) -> None:
    for row in rate_rows:
        snapshot = RateSnapshot(
            rate_refresh_id=refresh_id,
            base_currency=row.base_currency,
            quote_currency=row.quote_currency,
            mid_rate=row.mid_rate,
            provider=provider,
            provider_timestamp=provider_timestamp,
            fetched_at=fetched_at,
            raw_payload_hash=raw_payload_hash,
        )
        session.add(snapshot)
        session.flush()
        session.execute(
            insert(CurrentRate)
            .values(
                base_currency=row.base_currency,
                quote_currency=row.quote_currency,
                mid_rate=row.mid_rate,
                buy_spread_bps=buy_spread_bps,
                sell_spread_bps=sell_spread_bps,
                rate_snapshot_id=snapshot.id,
                last_updated_at=fetched_at,
            )
            .on_conflict_do_update(
                constraint=UQ_CURRENT_RATES_PAIR,
                set_={
                    "mid_rate": row.mid_rate,
                    "buy_spread_bps": buy_spread_bps,
                    "sell_spread_bps": sell_spread_bps,
                    "rate_snapshot_id": snapshot.id,
                    "last_updated_at": fetched_at,
                },
            )
        )
