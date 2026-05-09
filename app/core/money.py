"""Central money/rate rules.

Keeping Decimal conversion, currency metadata, and rounding here prevents
different services from silently applying different financial rules.
"""

from decimal import ROUND_HALF_EVEN, Decimal
from enum import StrEnum

from app.core.constants import DECIMAL_ONE

# Supported fiat currencies all use two stored/display decimal places.
STANDARD_MINOR_UNIT_PLACES = 2

# Executable rates are stored and returned at ten decimal places.
RATE_QUANT = Decimal("0.0000000001")


class Currency(StrEnum):
    USD = "USD"
    EUR = "EUR"
    KES = "KES"
    NGN = "NGN"


_MONEY_QUANT = DECIMAL_ONE.scaleb(-STANDARD_MINOR_UNIT_PLACES)


def round_money(amount: Decimal, currency: Currency) -> Decimal:
    """Round settled money to the currency's storage/display precision."""
    return amount.quantize(_MONEY_QUANT, rounding=ROUND_HALF_EVEN)


def round_rate(rate: Decimal) -> Decimal:
    """Round executable rates at the single precision used for quotes."""
    return rate.quantize(RATE_QUANT, rounding=ROUND_HALF_EVEN)


def decimal_to_str(value: Decimal) -> str:
    return format(value, "f")
