"""Quote pricing service.

Quotes capture executable terms at creation time. Execution later uses these
stored terms rather than repricing, which preserves the customer's accepted FX
contract across rate refreshes.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.errors import service_unavailable, validation_error
from app.core.money import Currency, round_money, round_rate
from app.core.observability import log_event, quote_created_total
from app.db.models import CurrentRate, Quote
from app.services.customers import assert_customer_exists
from app.services.rates import ensure_fresh_rates


@dataclass(frozen=True)
class Leg:
    base: Currency
    quote: Currency
    source: Currency
    destination: Currency
    mid_rate: Decimal
    buy_spread_bps: int
    sell_spread_bps: int
    rate_snapshot_id: UUID


def find_current_rate(session: Session, source: Currency, destination: Currency) -> CurrentRate | None:
    return session.execute(
        select(CurrentRate).where(
            or_(
                (CurrentRate.base_currency == source.value) & (CurrentRate.quote_currency == destination.value),
                (CurrentRate.base_currency == destination.value) & (CurrentRate.quote_currency == source.value),
            )
        )
    ).scalar_one_or_none()


def build_route(session: Session, source: Currency, destination: Currency) -> list[Currency]:
    """Choose the deterministic route defined by the spec: direct, USD, then EUR."""
    if find_current_rate(session, source, destination):
        return [source, destination]
    for pivot in (Currency.USD, Currency.EUR):
        source_to_pivot = find_current_rate(session, source, pivot)
        pivot_to_destination = find_current_rate(session, pivot, destination)
        if pivot not in (source, destination) and source_to_pivot and pivot_to_destination:
            return [source, pivot, destination]
    raise service_unavailable("rates_stale", f"No route available for {source.value}/{destination.value}.")


def executable_leg_rate(rate: CurrentRate, source: Currency, destination: Currency) -> tuple[Decimal, int]:
    """Apply spread after direction is known so direct and inverse legs price correctly."""
    base = Currency(rate.base_currency)
    quote = Currency(rate.quote_currency)
    mid = Decimal(rate.mid_rate)
    if source == base and destination == quote:
        spread = rate.sell_spread_bps
        return round_rate(mid * (Decimal("1") - (Decimal(spread) / Decimal("10000")))), spread
    if source == quote and destination == base:
        spread = rate.buy_spread_bps
        priced_rate = mid * (Decimal("1") + (Decimal(spread) / Decimal("10000")))
        return round_rate(Decimal("1") / priced_rate), spread
    raise validation_error("Rate direction does not match requested leg.")


def create_quote(
    session: Session,
    customer_id: UUID,
    source_currency: Currency,
    destination_currency: Currency,
    source_amount: Decimal,
    request_id: str | None = None,
) -> Quote:
    """Create an immutable quote without reading or mutating customer balances."""
    if source_currency == destination_currency:
        raise validation_error("source_currency and destination_currency must differ.")
    assert_customer_exists(session, customer_id)
    ensure_fresh_rates(session)
    route = build_route(session, source_currency, destination_currency)
    executable_rate = Decimal("1")
    spread_bps = 0
    rate_snapshot_id: UUID | None = None
    for source, destination in zip(route, route[1:], strict=False):
        current_rate = find_current_rate(session, source, destination)
        if current_rate is None:
            raise service_unavailable("rates_stale", f"Rate missing for {source.value}/{destination.value}.")
        leg_rate, leg_spread = executable_leg_rate(current_rate, source, destination)
        executable_rate *= leg_rate
        spread_bps += leg_spread
        rate_snapshot_id = current_rate.rate_snapshot_id
    executable_rate = round_rate(executable_rate)
    source_amount = round_money(source_amount, source_currency)
    destination_amount = round_money(source_amount * executable_rate, destination_currency)
    created_at = datetime.now(UTC)
    quote = Quote(
        customer_id=customer_id,
        source_currency=source_currency.value,
        destination_currency=destination_currency.value,
        source_amount=source_amount,
        destination_amount=destination_amount,
        executable_rate=executable_rate,
        route=[currency.value for currency in route],
        spread_bps=spread_bps,
        rate_snapshot_id=rate_snapshot_id,
        created_at=created_at,
        expires_at=created_at + timedelta(seconds=60),
    )
    session.add(quote)
    session.commit()
    quote_created_total.inc()
    log_event(
        "quote.created",
        request_id=request_id,
        customer_id=customer_id,
        quote_id=quote.id,
        source_currency=source_currency.value,
        destination_currency=destination_currency.value,
        source_amount=str(source_amount),
        destination_amount=str(destination_amount),
        executable_rate=str(executable_rate),
        outcome="success",
    )
    return quote
