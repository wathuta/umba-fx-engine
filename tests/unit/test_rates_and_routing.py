from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, select

from app.core.errors import ApiError, bad_gateway, gateway_timeout
from app.core.money import Currency, round_money, round_rate
from app.db.models import CurrentRate, Quote, RateRefresh
from app.services.rates import CANONICAL_PAIRS, ProviderRates, RateProvider, refresh_rates
from tests.conftest import seed_rates
from tests.unit.helpers import create_customer_with_usd


class GoodProvider:
    def fetch(self) -> ProviderRates:
        return ProviderRates(
            provider="fake",
            base_currency=Currency.USD,
            rates={
                Currency.USD: Decimal("1"),
                Currency.EUR: Decimal("0.8000000000"),
                Currency.KES: Decimal("130.0000000000"),
                Currency.NGN: Decimal("1500.0000000000"),
            },
            provider_timestamp=datetime.now(UTC),
            raw_payload={"base": "USD", "rates": {"EUR": "0.8", "KES": "130", "NGN": "1500"}},
        )


class FailingProvider:
    def __init__(self, error: ApiError) -> None:
        self.error = error

    def fetch(self) -> ProviderRates:
        raise self.error


class IncompleteProvider:
    def fetch(self) -> ProviderRates:
        return ProviderRates(
            provider="fake",
            base_currency=Currency.USD,
            rates={Currency.KES: Decimal("130.0000000000")},
            provider_timestamp=datetime.now(UTC),
            raw_payload={"base": "USD", "rates": {"KES": "130"}},
        )


def test_fxapi_provider_payload_is_parsed(monkeypatch):
    class Response:
        status_code = 200

        def json(self):
            return {
                "base": "USD",
                "timestamp": "2026-05-08T17:05:11.556Z",
                "rates": {
                    "EUR": 0.849449,
                    "KES": 129.149265,
                    "NGN": 1363.267265,
                },
            }

    monkeypatch.setattr("app.services.rates.httpx.get", lambda *args, **kwargs: Response())

    rates = RateProvider().fetch()

    assert rates.provider == "fxapi.app"
    assert rates.base_currency == Currency.USD
    assert rates.rates[Currency.KES] == Decimal("129.149265")
    assert rates.provider_timestamp is not None


def test_refresh_rates_writes_one_canonical_orientation_per_pair(db_session):
    _, status, _, pairs_updated = refresh_rates(db_session, provider=GoodProvider())

    pair_count = db_session.execute(select(func.count()).select_from(CurrentRate)).scalar_one()
    inverse_usd_eur = db_session.execute(
        select(CurrentRate).where(CurrentRate.base_currency == "EUR", CurrentRate.quote_currency == "USD")
    ).scalar_one_or_none()

    assert status == "completed"
    assert pairs_updated == len(CANONICAL_PAIRS)
    assert pair_count == len(CANONICAL_PAIRS)
    assert inverse_usd_eur is None


def test_inverse_route_uses_buy_spread(client, db_session, seeded_rates):
    customer_id = create_customer_with_usd(client)
    response = client.post(
        "/quotes",
        json={
            "customer_id": customer_id,
            "source_currency": "EUR",
            "destination_currency": "USD",
            "source_amount": "100.00",
        },
    )

    mid = Decimal("0.8000000000")
    expected_rate = round_rate(Decimal("1") / (mid * Decimal("1.005")))
    expected_destination = round_money(Decimal("100.00") * expected_rate, Currency.USD)

    assert response.status_code == 200
    body = response.json()
    assert body["route"] == ["EUR", "USD"]
    assert body["executable_rate"] == str(expected_rate)
    assert body["destination_amount"] == str(expected_destination)


def test_cross_route_compounds_leg_spreads(client, db_session, seeded_rates):
    db_session.execute(
        delete(CurrentRate).where(CurrentRate.base_currency == "KES", CurrentRate.quote_currency == "NGN")
    )
    db_session.commit()
    customer_id = create_customer_with_usd(client)

    response = client.post(
        "/quotes",
        json={
            "customer_id": customer_id,
            "source_currency": "KES",
            "destination_currency": "NGN",
            "source_amount": "100.00",
        },
    )

    leg_one = round_rate(Decimal("1") / (Decimal("130.0000000000") * Decimal("1.005")))
    leg_two = round_rate(Decimal("1500.0000000000") * Decimal("0.995"))
    expected_rate = round_rate(leg_one * leg_two)
    expected_destination = round_money(Decimal("100.00") * expected_rate, Currency.NGN)
    quote = db_session.execute(select(Quote)).scalar_one()

    assert response.status_code == 200
    assert response.json()["route"] == ["KES", "USD", "NGN"]
    assert response.json()["executable_rate"] == str(expected_rate)
    assert response.json()["destination_amount"] == str(expected_destination)
    assert quote.spread_bps == 100


@pytest.mark.parametrize(
    "error",
    [
        bad_gateway("upstream_down", "Rate provider is unavailable."),
        gateway_timeout("Rate provider timed out."),
        bad_gateway("upstream_bad_response", "Rate provider returned an unusable response."),
    ],
)
def test_failed_rate_refresh_is_audited_without_replacing_current_rates(db_session, error):
    seed_rates(db_session)
    before = db_session.execute(select(func.count()).select_from(CurrentRate)).scalar_one()

    with pytest.raises(ApiError):
        refresh_rates(db_session, provider=FailingProvider(error))

    after = db_session.execute(select(func.count()).select_from(CurrentRate)).scalar_one()
    failed_refresh = db_session.execute(select(RateRefresh).where(RateRefresh.status == "failed")).scalar_one()

    assert after == before
    assert failed_refresh.error_code == error.code


def test_bad_provider_rates_are_audited_as_failed_refresh(db_session):
    with pytest.raises(ApiError) as exc:
        refresh_rates(db_session, provider=IncompleteProvider())

    failed_refresh = db_session.execute(select(RateRefresh).where(RateRefresh.status == "failed")).scalar_one()

    assert exc.value.code == "upstream_bad_response"
    assert failed_refresh.error_code == "upstream_bad_response"


def test_fresh_cached_rates_still_quote_after_refresh_failure(client, db_session):
    seed_rates(db_session)
    with pytest.raises(ApiError):
        refresh_rates(db_session, provider=FailingProvider(bad_gateway("upstream_down", "Provider down.")))

    customer_id = create_customer_with_usd(client)
    response = client.post(
        "/quotes",
        json={
            "customer_id": customer_id,
            "source_currency": "USD",
            "destination_currency": "KES",
            "source_amount": "100.00",
        },
    )

    assert response.status_code == 200
