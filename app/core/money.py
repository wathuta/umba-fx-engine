"""Central money/rate rules.

Keeping Decimal conversion, currency metadata, and rounding here prevents
different services from silently applying different financial rules.
"""

from decimal import Decimal, ROUND_HALF_EVEN, localcontext
from enum import StrEnum


class Currency(StrEnum):
    USD = "USD"
    EUR = "EUR"
    KES = "KES"
    NGN = "NGN"


CURRENCY_DECIMAL_PLACES: dict[Currency, int] = {
    Currency.USD: 2,
    Currency.EUR: 2,
    Currency.KES: 2,
    Currency.NGN: 2,
}

SUPPORTED_CURRENCIES = tuple(Currency)
RATE_QUANT = Decimal("0.0000000001")


def parse_decimal(value: Decimal | str) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def money_quant(currency: Currency) -> Decimal:
    places = CURRENCY_DECIMAL_PLACES[currency]
    return Decimal("1").scaleb(-places)


def round_money(amount: Decimal, currency: Currency) -> Decimal:
    """Round settled money to the currency's storage/display precision."""
    with localcontext() as ctx:
        ctx.rounding = ROUND_HALF_EVEN
        return amount.quantize(money_quant(currency), rounding=ROUND_HALF_EVEN)


def round_rate(rate: Decimal) -> Decimal:
    """Round executable rates at the single precision used for quotes."""
    with localcontext() as ctx:
        ctx.rounding = ROUND_HALF_EVEN
        return rate.quantize(RATE_QUANT, rounding=ROUND_HALF_EVEN)


def decimal_to_str(value: Decimal) -> str:
    return format(value, "f")
