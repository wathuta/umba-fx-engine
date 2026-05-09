# FX Take-Home

Production-minded FX engine for USD, EUR, KES, and NGN with customer balances, 60-second quotes, atomic execution, idempotent retries, rate refresh handling, and observability.

## Requirements

- [Docker](https://docs.docker.com/get-docker/)
- [Docker Compose](https://docs.docker.com/compose/install/)
- Python 3.12+
- Postgres via Docker Compose

## Start The Application

1. Create a local env file:

```bash
cp .env.example .env
```

2. Start Postgres:

```bash
docker compose up -d postgres
```

Postgres is exposed on `localhost:55432`. The app builds its local database URL
from `POSTGRES_*` settings. `DATABASE_URL` is optional and only needed when
overriding that generated URL, for example in CI or with a managed database.

3. Create a virtual environment and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

4. Run the API:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

5. In a second terminal, check the service:

```bash
curl http://localhost:8000/healthz
curl http://localhost:8000/readyz
curl http://localhost:8000/metrics
```

6. Refresh rates before creating quotes:

```bash
curl -X POST http://localhost:8000/rate-refreshes
```

The default provider is `fxapi.app`, which returns live rates without an API
key. Override `RATE_PROVIDER_URL` only if you want to test another compatible
provider payload.

Rates are refreshed manually through `POST /rate-refreshes` for this take-home
to keep the service simple and easy to test. In production, the same refresh
logic would run from a scheduler, with `POST /rate-refreshes` kept as an
admin/debug endpoint.

7. Run tests when needed:

```bash
source .venv/bin/activate
pytest
```

8. Stop Postgres when done:

```bash
docker compose down
```

## API Overview

- `POST /customers`: create customer
- `GET /customers/{customer_id}/balances`: view balances
- `POST /customers/{customer_id}/balance-credits`: manually credit test balance
- `POST /quotes`: generate a 60-second FX quote
- `POST /executions`: execute quote with `Idempotency-Key`
- `POST /rate-refreshes`: refresh rates from `fxapi.app`
- `GET /healthz`: process liveness
- `GET /readyz`: readiness for quote traffic; stale or unavailable rates return `status: not_ready`
- `GET /metrics`: Prometheus metrics


## Observability

The service exposes `/healthz`, `/readyz`, `/metrics`, and structured JSON logs. The
`request_id` field is the correlation ID; quote and execution logs also share
`customer_id` and `quote_id`.

Example quote log:

```json
{
  "customer_id": "0196f20f-7f6a-7f40-9a0e-dc803f830001",
  "destination_amount": "12935.00",
  "destination_currency": "KES",
  "event": "quote.created",
  "executable_rate": "129.3500000000",
  "outcome": "success",
  "quote_id": "0196f20f-7f6a-7f40-9a0e-dc803f830002",
  "request_id": "0196f20f-7f6a-7f40-9a0e-dc803f830010",
  "source_amount": "100.00",
  "source_currency": "USD"
}
```

Example execution log:

```json
{
  "customer_id": "0196f20f-7f6a-7f40-9a0e-dc803f830001",
  "destination_amount": "12935.00",
  "destination_currency": "KES",
  "event": "execution.completed",
  "execution_id": "0196f20f-7f6a-7f40-9a0e-dc803f830003",
  "idempotency_key": "client-retry-key-1",
  "outcome": "success",
  "quote_id": "0196f20f-7f6a-7f40-9a0e-dc803f830002",
  "request_id": "0196f20f-7f6a-7f40-9a0e-dc803f830010",
  "source_amount": "100.00",
  "source_currency": "USD"
}
```

### Metrics counter semantics

`fx_execution_failure_total` increments on any non-success exit from the execution
path — including expired quotes, idempotency conflicts, and insufficient funds. Read
it alongside `fx_insufficient_funds_total`, `fx_idempotency_conflict_total`, and
`fx_quote_expired_total` to distinguish expected client-side rejections from true
system failures.

## Tests

Run all tests:

```bash
pytest
```

Run API-only integration tests:

```bash
pytest tests/integration
```

Run service-backed unit tests, split by behavior:

```bash
pytest tests/unit
```

Semantic test files:

- `tests/integration/test_api_contract.py`
- `tests/unit/test_api_errors.py`
- `tests/unit/test_executions.py`
- `tests/unit/test_money.py`
- `tests/unit/test_observability.py`
- `tests/unit/test_quotes.py`
- `tests/unit/test_rates_and_routing.py`

**Assignment evidence:** the required behaviors are verified through the test suite.
Load-test output is not included; the concurrency test proves correctness for
parallel execution attempts against the same quote, not throughput or tail latency.

The suite covers decimal/property behavior, customer balances, manual
credits, all supported currency pairs plus inverses, routing, quote expiry,
concurrency, idempotency, atomic rollback, rate-provider failures,
`/healthz`, `/readyz`, and `/metrics`.

Integration tests call only public HTTP APIs. Tests that require direct database
setup, controlled provider failures, stale rates, forced cross-route setup, or
failure injection live under `tests/unit`.

Run the command locally to see the current test count.

## Known Limitations

- **Balance credits:** test-only endpoint; production funding would need auth,
  rails, and audit workflow.
- **Ledger model:** balances are updated in place; production should use
  immutable ledger entries.
- **Schema changes:** tables are created at startup; production should use
  Alembic migrations.
- **Rate refresh:** manual through `POST /rate-refreshes` and single-provider;
  production should add scheduling, retries, failover, and stale-rate alerts.
- **Metrics:** local `/metrics` only; not connected to dashboards or alerting.

## With Another Day

- Add Alembic migrations.
- Add scheduled rate refresh using the existing refresh service, while keeping
  `POST /rate-refreshes` as an admin/debug endpoint.
- Add an executable demo script that creates a customer, credits balance, quotes, and executes.
- Add load-test output for quote/execution paths.
- Add stricter linting/type checks and enforce them in CI.
- Add tracing dashboards beyond structured logs and `/metrics`.
- Add richer OpenAPI examples.

## Time Tracking

