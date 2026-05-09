"""Quote pricing service.

Quotes capture executable terms at creation time. Execution later uses these
stored terms rather than repricing, so later rate changes do not alter the
accepted quote.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.constants import DECIMAL_ONE, ERROR_RATES_STALE, OUTCOME_SUCCESS
from app.core.errors import service_unavailable, validation_error
from app.core.money import Currency, round_money, round_rate
from app.core.observability import log_event, quote_created_total
from app.db.models import CurrentRate, Quote, QuoteLeg
from app.services.customers import assert_customer_exists
from app.services.rates import ensure_fresh_rates

# Spread math uses basis points, where 10,000 bps equals 100%.
BASIS_POINT_DENOMINATOR = Decimal("10000")

# Quote validity window.
QUOTE_TTL_SECONDS = 60

# Spread side values stored on each QuoteLeg row.
SPREAD_SIDE_BUY = "buy"
SPREAD_SIDE_SELL = "sell"


@dataclass(frozen=True)
class LegPricing:
    """`mid_rate` keeps all decimals; `executable_rate` is rounded per leg so stored quote amounts don't change."""

    source: Currency
    destination: Currency
    mid_rate: Decimal
    executable_rate: Decimal
    spread_bps: int
    spread_side: str
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
    """Choose the route in this order: direct, USD, then EUR."""
    if find_current_rate(session, source, destination):
        return [source, destination]
    for pivot in (Currency.USD, Currency.EUR):
        source_to_pivot = find_current_rate(session, source, pivot)
        pivot_to_destination = find_current_rate(session, pivot, destination)
        if pivot not in (source, destination) and source_to_pivot and pivot_to_destination:
            return [source, pivot, destination]
    raise service_unavailable(ERROR_RATES_STALE, f"No route available for {source.value}/{destination.value}.")


def price_leg(rate: CurrentRate, source: Currency, destination: Currency) -> LegPricing:
    """Apply spread after direction is known so direct and inverse legs price correctly."""
    base = Currency(rate.base_currency)
    quote = Currency(rate.quote_currency)
    mid = Decimal(rate.mid_rate)
    if source == base and destination == quote:
        spread = rate.sell_spread_bps
        executable = round_rate(mid * (DECIMAL_ONE - (Decimal(spread) / BASIS_POINT_DENOMINATOR)))
        return LegPricing(source, destination, mid, executable, spread, SPREAD_SIDE_SELL, rate.rate_snapshot_id)
    if source == quote and destination == base:
        spread = rate.buy_spread_bps
        priced_rate = mid * (DECIMAL_ONE + (Decimal(spread) / BASIS_POINT_DENOMINATOR))
        executable = round_rate(DECIMAL_ONE / priced_rate)
        return LegPricing(source, destination, mid, executable, spread, SPREAD_SIDE_BUY, rate.rate_snapshot_id)
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
    legs: list[LegPricing] = []
    for source, destination in zip(route, route[1:], strict=False):
        current_rate = find_current_rate(session, source, destination)
        if current_rate is None:
            raise service_unavailable(ERROR_RATES_STALE, f"Rate missing for {source.value}/{destination.value}.")
        legs.append(price_leg(current_rate, source, destination))
    executable_rate = DECIMAL_ONE
    for leg in legs:
        executable_rate *= leg.executable_rate
    executable_rate = round_rate(executable_rate)
    spread_bps = sum(leg.spread_bps for leg in legs)
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
        created_at=created_at,
        expires_at=created_at + timedelta(seconds=QUOTE_TTL_SECONDS),
    )
    session.add(quote)
    session.flush()
    session.add_all(
        QuoteLeg(
            quote_id=quote.id,
            position=position,
            source_currency=leg.source.value,
            destination_currency=leg.destination.value,
            mid_rate=round_rate(leg.mid_rate),
            executable_rate=leg.executable_rate,
            spread_side=leg.spread_side,
            spread_bps=leg.spread_bps,
            rate_snapshot_id=leg.rate_snapshot_id,
        )
        for position, leg in enumerate(legs)
    )
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
        outcome=OUTCOME_SUCCESS,
    )
    return quote
