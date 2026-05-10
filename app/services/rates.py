"""Rate refresh and freshness rules.

The engine keeps immutable snapshots for audit and current rates for fast quote
creation. Current rates store one direction per pair; inverse conversions are
calculated during quote pricing.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, DecimalException
from uuid import UUID

import logging

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.constants import DECIMAL_ONE, ERROR_RATES_STALE
from app.core.errors import ApiError, bad_gateway, gateway_timeout, service_unavailable
from app.core.money import Currency, round_rate
from app.core.observability import (
    log_event,
    rate_refresh_failure_total,
    rate_refresh_latency_ms,
    rate_refresh_success_total,
    stale_rates_total,
)
from app.db.models import UQ_CURRENT_RATES_PAIR, CurrentRate, RateRefresh, RateSnapshot

# Upstream failures from the provider are normalized to this client-facing code.
ERROR_UPSTREAM_BAD_RESPONSE = "upstream_bad_response"

# Provider name stored on refresh/snapshot rows.
PROVIDER_FXAPI_APP = "fxapi.app"

# Status values stored on refresh rows.
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"

# Stored rate directions; inverses are calculated in code.
CANONICAL_PAIRS: tuple[tuple[Currency, Currency], ...] = (
    (Currency.USD, Currency.KES),
    (Currency.USD, Currency.NGN),
    (Currency.USD, Currency.EUR),
    (Currency.EUR, Currency.KES),
    (Currency.EUR, Currency.NGN),
    (Currency.KES, Currency.NGN),
)


@dataclass(frozen=True)
class ProviderRates:
    provider: str
    base_currency: Currency
    rates: dict[Currency, Decimal]
    provider_timestamp: datetime | None
    raw_payload: dict


class RateProvider:
    """Fetch and validate provider payloads before rates reach persistence."""

    def fetch(self) -> ProviderRates:
        settings = get_settings()
        try:
            response = httpx.get(
                settings.rate_provider_url,
                timeout=settings.rate_provider_timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise gateway_timeout("Rate provider timed out.") from exc
        except httpx.HTTPError as exc:
            raise bad_gateway(ERROR_UPSTREAM_BAD_RESPONSE, "Rate provider request failed.") from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise bad_gateway(ERROR_UPSTREAM_BAD_RESPONSE, "Rate provider returned invalid JSON.") from exc
        if response.status_code >= 400:
            raise bad_gateway(ERROR_UPSTREAM_BAD_RESPONSE, "Rate provider returned an HTTP error.")
        if payload.get("success") is False or "rates" not in payload:
            raise bad_gateway(ERROR_UPSTREAM_BAD_RESPONSE, "Rate provider returned an unusable response.")
        try:
            base_currency = Currency(payload.get("base", Currency.USD.value))
            rates = {Currency(k): Decimal(str(v)) for k, v in payload["rates"].items() if k in Currency.__members__}
            provider_timestamp = _provider_timestamp(payload.get("timestamp"))
        except (ValueError, DecimalException, TypeError) as exc:
            raise bad_gateway(ERROR_UPSTREAM_BAD_RESPONSE, "Rate provider returned invalid rate data.") from exc
        missing = set(Currency) - {base_currency} - set(rates)
        if missing or any(rate <= 0 for rate in rates.values()):
            raise bad_gateway(
                ERROR_UPSTREAM_BAD_RESPONSE,
                "Rate provider returned incomplete or non-positive rates.",
            )
        return ProviderRates(PROVIDER_FXAPI_APP, base_currency, rates, provider_timestamp, payload)


def pair_mid_rate(provider_rates: ProviderRates, base: Currency, quote: Currency) -> Decimal:
    """Calculate a pair rate from the provider's base-rate map."""
    if base == provider_rates.base_currency:
        return DECIMAL_ONE if quote == base else provider_rates.rates[quote]
    if quote == provider_rates.base_currency:
        return DECIMAL_ONE / provider_rates.rates[base]
    return provider_rates.rates[quote] / provider_rates.rates[base]


def refresh_rates(
    session: Session,
    provider: RateProvider | None = None,
    request_id: str | None = None,
) -> tuple[UUID, str, datetime, int]:
    """Store one refresh attempt and update current rates only on success."""
    provider = provider or RateProvider()
    settings = get_settings()
    started = datetime.now(UTC)
    try:
        rates = provider.fetch()
        raw_hash = hashlib.sha256(json.dumps(rates.raw_payload, sort_keys=True, default=str).encode()).hexdigest()
        refresh = RateRefresh(
            provider=rates.provider,
            status=STATUS_COMPLETED,
            provider_base_currency=rates.base_currency.value,
            provider_timestamp=rates.provider_timestamp,
            fetched_at=started,
            pairs_updated=0,
            duration_ms=_duration_ms(started),
        )
        session.add(refresh)
        session.flush()
        updated = 0
        for base, quote in CANONICAL_PAIRS:
            mid_rate = round_rate(pair_mid_rate(rates, base, quote))
            snapshot = RateSnapshot(
                rate_refresh_id=refresh.id,
                base_currency=base.value,
                quote_currency=quote.value,
                mid_rate=mid_rate,
                provider=rates.provider,
                provider_timestamp=rates.provider_timestamp,
                fetched_at=started,
                raw_payload_hash=raw_hash,
            )
            session.add(snapshot)
            session.flush()
            session.execute(
                insert(CurrentRate)
                .values(
                    base_currency=base.value,
                    quote_currency=quote.value,
                    mid_rate=mid_rate,
                    buy_spread_bps=settings.default_buy_spread_bps,
                    sell_spread_bps=settings.default_sell_spread_bps,
                    rate_snapshot_id=snapshot.id,
                    last_updated_at=started,
                )
                .on_conflict_do_update(
                    constraint=UQ_CURRENT_RATES_PAIR,
                    set_={
                        "mid_rate": mid_rate,
                        "buy_spread_bps": settings.default_buy_spread_bps,
                        "sell_spread_bps": settings.default_sell_spread_bps,
                        "rate_snapshot_id": snapshot.id,
                        "last_updated_at": started,
                    },
                )
            )
            updated += 1
        refresh.pairs_updated = updated
        refresh.duration_ms = _duration_ms(started)
        # Single commit: quotes reading current_rates see either all-old or all-new rates, never a partial refresh.
        session.commit()
        rate_refresh_success_total.inc()
        rate_refresh_latency_ms.observe(refresh.duration_ms)
        log_event(
            "rate_refresh.completed",
            request_id=request_id,
            rate_refresh_id=refresh.id,
            provider=rates.provider,
            status=STATUS_COMPLETED,
            pairs_updated=updated,
            duration_ms=refresh.duration_ms,
        )
        return refresh.id, refresh.status, refresh.fetched_at, updated
    except ApiError as exc:
        _record_failed_refresh(session, started, exc, request_id)
        raise
    except (KeyError, DecimalException, ZeroDivisionError, ValueError) as exc:
        error = bad_gateway(ERROR_UPSTREAM_BAD_RESPONSE, "Rate provider returned unusable rates.")
        _record_failed_refresh(session, started, error, request_id)
        raise error from exc


def ensure_fresh_rates(session: Session) -> None:
    """Fail quotes when the latest current rate is outside the freshness window."""
    status = rates_freshness_status(session)
    if status == "fresh":
        return
    stale_rates_total.inc()
    detail = "Rates are unavailable." if status == "unavailable" else "Rates are stale."
    raise service_unavailable(ERROR_RATES_STALE, detail)


def rates_freshness_status(session: Session) -> str:
    """Return rate readiness without mutating quote-path metrics."""
    latest = session.execute(
        select(CurrentRate).order_by(CurrentRate.last_updated_at.desc()).limit(1)
    ).scalar_one_or_none()
    if latest is None:
        return "unavailable"
    cutoff = datetime.now(UTC) - timedelta(seconds=get_settings().rate_freshness_seconds)
    last_updated = latest.last_updated_at
    if last_updated.tzinfo is None:
        last_updated = last_updated.replace(tzinfo=UTC)
    if last_updated < cutoff:
        return "stale"
    return "fresh"


def _duration_ms(started: datetime) -> int:
    return int((datetime.now(UTC) - started).total_seconds() * 1000)


def _provider_timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=UTC)
    raise ValueError("unsupported provider timestamp")


def _record_failed_refresh(session: Session, started: datetime, error: ApiError, request_id: str | None) -> None:
    session.rollback()
    refresh = RateRefresh(
        provider=PROVIDER_FXAPI_APP,
        status=STATUS_FAILED,
        provider_base_currency=None,
        provider_timestamp=None,
        fetched_at=started,
        pairs_updated=0,
        error_code=error.code,
        error_message=error.detail,
        duration_ms=_duration_ms(started),
    )
    session.add(refresh)
    session.commit()
    rate_refresh_failure_total.inc()
    rate_refresh_latency_ms.observe(refresh.duration_ms)
    log_event(
        "rate_refresh.failed",
        level=logging.ERROR,
        request_id=request_id,
        rate_refresh_id=refresh.id,
        provider=refresh.provider,
        status=STATUS_FAILED,
        pairs_updated=0,
        duration_ms=refresh.duration_ms,
        error_code=error.code,
    )
