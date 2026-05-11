import pytest
from fastapi.testclient import TestClient

import app.api.routes.rates as rates_route
from app.utils.errors import bad_gateway, gateway_timeout
from app.main import create_app


def test_money_inputs_must_be_decimal_strings(client, seeded_rates):
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


def test_validation_error_is_sanitized(client):
    customer_id = client.post("/customers").json()["customer_id"]

    response = client.post(
        f"/customers/{customer_id}/balance-credits",
        json={"currency": "GBP", "amount": "10.00"},
        headers={"X-Request-ID": "validation-sanitized-test"},
    )
    body = response.json()

    assert response.status_code == 422
    assert body["detail"] == "Request validation failed."
    assert body["instance"] == "urn:request:validation-sanitized-test"
    assert body["errors"] == [
        {
            "field": "currency",
            "message": "Input should be 'USD', 'EUR', 'KES' or 'NGN'",
        }
    ]
    assert "routes.py" not in body["detail"]
    assert "/home/" not in body["detail"]


def test_errors_use_problem_json(client):
    response = client.post("/quotes", content='{"bad"', headers={"Content-Type": "application/json"})

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "bad_request"


def test_unsupported_content_type_rejected(client):
    response = client.post("/quotes", content="not-json", headers={"Content-Type": "text/plain"})

    assert response.status_code == 415
    assert response.headers["content-type"].startswith("application/problem+json")


@pytest.mark.parametrize(
    ("provider_error", "expected_status", "expected_code"),
    [
        (bad_gateway("upstream_bad_response", "Bad provider payload."), 502, "upstream_bad_response"),
        (gateway_timeout("Rate provider timed out."), 504, "upstream_timeout"),
    ],
)
def test_rate_refresh_maps_provider_errors(
    client,
    monkeypatch,
    provider_error,
    expected_status,
    expected_code,
):
    def fail_refresh(*args, **kwargs):
        raise provider_error

    monkeypatch.setattr(rates_route, "refresh_rates", fail_refresh)

    response = client.post("/rate-refreshes")

    assert response.status_code == expected_status
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == expected_code


def test_unhandled_errors_return_problem_json():
    app = create_app()

    @app.get("/boom")
    def boom():
        raise RuntimeError("boom")

    with TestClient(app, raise_server_exceptions=False) as test_client:
        response = test_client.get("/boom", headers={"X-Request-ID": "unexpected-error-test"})

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json() == {
        "type": "about:blank",
        "title": "Internal server error",
        "status": 500,
        "detail": "An unexpected error occurred.",
        "instance": "urn:request:unexpected-error-test",
        "code": "internal_error",
        "retryable": False,
    }
