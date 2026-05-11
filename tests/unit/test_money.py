from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from app.utils.money import Currency, round_money, round_rate


@given(
    amount=st.decimals(min_value="0.0000", max_value="1000000", places=4, allow_nan=False, allow_infinity=False),
    currency=st.sampled_from(list(Currency)),
)
@settings(max_examples=50)
def test_money_rounds_to_currency_decimal_places(amount: Decimal, currency: Currency):
    rounded = round_money(amount, currency)

    assert rounded == rounded.quantize(Decimal("0.01"))


@given(rate=st.decimals(min_value="0.000001", max_value="100000", places=12, allow_nan=False, allow_infinity=False))
@settings(max_examples=50)
def test_rates_round_to_configured_precision(rate: Decimal):
    rounded = round_rate(rate)

    assert rounded == rounded.quantize(Decimal("0.0000000001"))
