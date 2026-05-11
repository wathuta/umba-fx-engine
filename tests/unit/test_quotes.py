from datetime import datetime
from itertools import permutations
from uuid import UUID

import pytest

from app.utils.money import Currency
from tests.unit.helpers import create_customer_with_usd


def test_quote_creation_is_pricing_only(client, seeded_rates):
    customer_id = create_customer_with_usd(client)
    before = client.get(f"/customers/{customer_id}/balances").json()["balances"]

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
    body = response.json()
    assert UUID(body["quote_id"])
    assert body["destination_amount"] == "12935.00"
    assert body["route"] == ["USD", "KES"]
    assert client.get(f"/customers/{customer_id}/balances").json()["balances"] == before


def test_quote_expiry_is_sixty_seconds_after_creation(client, seeded_rates):
    customer_id = client.post("/customers").json()["customer_id"]

    response = client.post(
        "/quotes",
        json={
            "customer_id": customer_id,
            "source_currency": "USD",
            "destination_currency": "KES",
            "source_amount": "100.00",
        },
    )

    quote = response.json()
    expires_at = datetime.fromisoformat(quote["expires_at"])

    assert response.status_code == 200
    assert UUID(quote["quote_id"])
    assert 59 <= (expires_at - datetime.now(expires_at.tzinfo)).total_seconds() <= 60


@pytest.mark.parametrize("source_currency,destination_currency", list(permutations(Currency, 2)))
def test_all_supported_currency_pairs_can_quote(client, seeded_rates, source_currency, destination_currency):
    customer_id = client.post("/customers").json()["customer_id"]

    response = client.post(
        "/quotes",
        json={
            "customer_id": customer_id,
            "source_currency": source_currency.value,
            "destination_currency": destination_currency.value,
            "source_amount": "10.00",
        },
    )

    assert response.status_code == 200
    assert response.json()["source_currency"] == source_currency.value
    assert response.json()["destination_currency"] == destination_currency.value
