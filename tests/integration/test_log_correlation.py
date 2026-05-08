import json
import logging


def test_quote_and_execution_logs_share_correlation_fields(client, seeded_rates, caplog):
    request_id = "test-request-id-123"
    customer_id = client.post("/customers").json()["customer_id"]
    client.post(
        f"/customers/{customer_id}/balance-credits",
        json={"currency": "USD", "amount": "100.00"},
    )

    with caplog.at_level(logging.INFO, logger="fx"):
        quote_response = client.post(
            "/quotes",
            headers={"X-Request-ID": request_id},
            json={
                "customer_id": customer_id,
                "source_currency": "USD",
                "destination_currency": "KES",
                "source_amount": "100.00",
            },
        )
        quote_id = quote_response.json()["quote_id"]
        execution_response = client.post(
            "/executions",
            headers={
                "X-Request-ID": request_id,
                "Idempotency-Key": "log-correlation-key",
            },
            json={"quote_id": quote_id},
        )

    assert quote_response.status_code == 200
    assert execution_response.status_code == 200

    events = [json.loads(record.message) for record in caplog.records if record.name == "fx"]
    quote_log = _event(events, "quote.created")
    execution_log = _event(events, "execution.completed")

    assert quote_log["request_id"] == request_id
    assert execution_log["request_id"] == request_id
    assert quote_log["customer_id"] == customer_id
    assert execution_log["customer_id"] == customer_id
    assert quote_log["quote_id"] == quote_id
    assert execution_log["quote_id"] == quote_id
    assert execution_log["execution_id"] == execution_response.json()["execution_id"]

    for event in (quote_log, execution_log):
        assert event["source_currency"] == "USD"
        assert event["destination_currency"] == "KES"
        assert event["source_amount"] == "100.00"
        assert event["destination_amount"] == "12935.00"
        assert event["executable_rate"] == "129.3500000000"
        assert event["outcome"] == "success"


def _event(events, event_name):
    return next(event for event in events if event["event"] == event_name)
