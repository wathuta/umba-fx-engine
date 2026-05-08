from app.core.money import Currency

# Integration tests intentionally use only public HTTP endpoints. Scenarios that
# need deterministic provider data or failure injection live in tests/unit until
# the app exposes test-only setup APIs.


def test_create_customer_zero_balances(client):
    customer_id = client.post("/customers").json()["customer_id"]

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


def test_malformed_json_problem(client):
    response = client.post("/quotes", content='{"bad"', headers={"Content-Type": "application/json"})

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "bad_request"


def test_non_json_body_problem(client):
    response = client.post("/quotes", content="not-json", headers={"Content-Type": "text/plain"})

    assert response.status_code == 415
    assert response.headers["content-type"].startswith("application/problem+json")


def test_health_ready_metrics_without_rates(client):
    health = client.get("/healthz")
    readiness = client.get("/readyz")
    metrics = client.get("/metrics")

    # Health is liveness-style; readiness is stricter because quotes require
    # fresh rates.
    assert health.status_code == 200
    assert health.json() == {"status": "ok", "database": "ok", "rates": "stale"}
    assert readiness.status_code == 200
    assert readiness.json() == {"status": "unhealthy", "database": "ok", "rates": "stale"}
    assert metrics.status_code == 200
    assert "fx_stale_rates_total" in metrics.text
