"""API-only integration tests for public HTTP behavior.

These tests intentionally avoid DB sessions, models, service calls,
monkeypatching, and direct setup internals.
"""

import uuid

import pytest

from app.core.money import Currency

# Integration tests intentionally use only public HTTP endpoints. Scenarios that
# need deterministic provider data or failure injection live in tests/unit until
# the app exposes test-only setup APIs.


def test_create_customer_zero_balances(client):
    create_response = client.post("/customers")
    customer_id = create_response.json()["customer_id"]

    assert create_response.status_code == 201
    uuid.UUID(customer_id)
    response = client.get(f"/customers/{customer_id}/balances")
    assert response.status_code == 200
    assert response.json()["balances"] == {currency.value: "0.00" for currency in Currency}


def test_credit_balance_persists(client):
    customer_id = client.post("/customers").json()["customer_id"]

    credit = client.post(f"/customers/{customer_id}/balance-credits", json={"currency": "EUR", "amount": "12.34"})
    balances = client.get(f"/customers/{customer_id}/balances").json()["balances"]

    assert credit.status_code == 200
    assert credit.json() == {"currency": "EUR", "amount": "12.34"}
    assert balances["EUR"] == "12.34"


def test_get_unknown_customer_returns_404(client):
    response = client.get(f"/customers/{uuid.uuid4()}/balances")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "customer_not_found"


def test_credit_unknown_customer_returns_404(client):
    response = client.post(
        f"/customers/{uuid.uuid4()}/balance-credits",
        json={"currency": "USD", "amount": "10.00"},
    )

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "customer_not_found"


def test_credit_rejects_unsupported_currency(client):
    customer_id = client.post("/customers").json()["customer_id"]

    response = client.post(
        f"/customers/{customer_id}/balance-credits",
        json={"currency": "GBP", "amount": "10.00"},
    )

    assert response.status_code == 422


@pytest.mark.parametrize("amount", ["0.00", "-1.00"])
def test_credit_rejects_non_positive_amount(client, amount):
    customer_id = client.post("/customers").json()["customer_id"]

    response = client.post(
        f"/customers/{customer_id}/balance-credits",
        json={"currency": "USD", "amount": amount},
    )

    assert response.status_code == 422


def test_quote_unknown_customer_returns_404(client):
    response = client.post(
        "/quotes",
        json={
            "customer_id": str(uuid.uuid4()),
            "source_currency": "USD",
            "destination_currency": "KES",
            "source_amount": "100.00",
        },
    )

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "customer_not_found"


def test_quote_rejects_same_currency(client):
    customer_id = client.post("/customers").json()["customer_id"]

    response = client.post(
        "/quotes",
        json={
            "customer_id": customer_id,
            "source_currency": "USD",
            "destination_currency": "USD",
            "source_amount": "100.00",
        },
    )

    assert response.status_code == 422


def test_quote_rejects_unsupported_currency(client):
    customer_id = client.post("/customers").json()["customer_id"]

    response = client.post(
        "/quotes",
        json={
            "customer_id": customer_id,
            "source_currency": "USD",
            "destination_currency": "GBP",
            "source_amount": "100.00",
        },
    )

    assert response.status_code == 422


def test_quote_without_rates_fails_safely(client):
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

    # Without using DB fixtures or mocked providers, the public API should fail
    # safely instead of inventing rates or touching balances.
    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "rates_stale"


def test_execute_requires_idempotency_key(client):
    response = client.post("/executions", json={"quote_id": str(uuid.uuid4())})

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "validation_error"


def test_execute_unknown_quote_returns_404_with_key(client):
    response = client.post(
        "/executions",
        json={"quote_id": str(uuid.uuid4())},
        headers={"Idempotency-Key": "unknown-quote"},
    )

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "quote_not_found"


def test_money_amounts_must_be_strings(client):
    customer_id = client.post("/customers").json()["customer_id"]

    credit = client.post(f"/customers/{customer_id}/balance-credits", json={"currency": "USD", "amount": 100})
    quote = client.post(
        "/quotes",
        json={
            "customer_id": customer_id,
            "source_currency": "USD",
            "destination_currency": "KES",
            "source_amount": 100,
        },
    )

    assert credit.status_code == 422
    assert quote.status_code == 422


def test_request_id_is_echoed(client):
    response = client.get("/healthz", headers={"X-Request-ID": "integration-request-id"})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "integration-request-id"


def test_problem_response_contains_request_instance(client):
    response = client.post(
        "/quotes",
        content='{"bad"',
        headers={"Content-Type": "application/json", "X-Request-ID": "problem-instance-test"},
    )

    assert response.status_code == 400
    assert response.json()["instance"] == "urn:request:problem-instance-test"


def test_malformed_json_problem(client):
    response = client.post("/quotes", content='{"bad"', headers={"Content-Type": "application/json"})

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "bad_request"


def test_non_json_body_problem(client):
    response = client.post("/quotes", content="not-json", headers={"Content-Type": "text/plain"})

    assert response.status_code == 415
    assert response.headers["content-type"].startswith("application/problem+json")


def test_health_and_ready_show_stale_rates(client):
    health = client.get("/healthz")
    readiness = client.get("/readyz")

    # Health is liveness-style; readiness is stricter because quotes require
    # fresh rates.
    assert health.status_code == 200
    assert health.json() == {"status": "ok", "database": "ok", "rates": "stale"}
    assert readiness.status_code == 200
    assert readiness.json() == {"status": "unhealthy", "database": "ok", "rates": "stale"}


def test_metrics_endpoint_exposes_prometheus_metrics(client):
    metrics = client.get("/metrics")

    assert metrics.status_code == 200
    assert "fx_stale_rates_total" in metrics.text


def test_openapi_contains_required_paths(client):
    response = client.get("/openapi.json")
    paths = response.json()["paths"]

    assert response.status_code == 200
    for path in (
        "/customers",
        "/customers/{customer_id}/balances",
        "/customers/{customer_id}/balance-credits",
        "/quotes",
        "/executions",
        "/rate-refreshes",
        "/healthz",
        "/readyz",
        "/metrics",
    ):
        assert path in paths
