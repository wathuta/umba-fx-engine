from datetime import UTC, datetime, timedelta

from tests.conftest import seed_rates


def test_healthz_and_metrics(client, seeded_rates):
    health = client.get("/healthz")
    readiness = client.get("/readyz")
    metrics = client.get("/metrics")

    assert health.status_code == 200
    assert health.json()["database"] == "ok"
    assert readiness.status_code == 200
    assert readiness.json() == {"status": "ok", "database": "ok", "rates": "ok"}
    assert metrics.status_code == 200
    for counter in (
        "fx_quote_created_total",
        "fx_execution_success_total",
        "fx_idempotency_conflict_total",
        "fx_stale_rates_total",
        "fx_rate_refresh_success_total",
    ):
        assert counter in metrics.text


def test_readyz_reports_unhealthy_when_rates_are_stale(client, db_session):
    seed_rates(db_session, fetched_at=datetime.now(UTC) - timedelta(minutes=10))

    response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "unhealthy", "database": "ok", "rates": "stale"}
