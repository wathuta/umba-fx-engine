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

- `fx_execution_failure_total` increments only on inside-try failures —
  after the idempotency-key row has been claimed. Covers
  `quote_not_found`, `quote_already_executed`, `quote_expired`,
  `insufficient_funds`, and any system fault.
- `fx_idempotency_conflict_total` counts pre-flight rejections that
  never reach the work block: hash mismatch on an existing key and the
  race-loser `IntegrityError` on a concurrent insert. These do not
  increment `fx_execution_failure_total`.
- `fx_quote_expired_total` and `fx_insufficient_funds_total` are
  per-reason subsets of `fx_execution_failure_total`, useful for
  splitting client friction from true system faults on dashboards.
- `fx_idempotency_replay_total` counts successful replays (cached
  responses returned) and is not a failure signal.

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
- **Ledger model:** money changes are recorded in an append-only ledger,
  and balances are kept as a cached view of that ledger. The tests check
  that the two stay in sync. History tables such as `ledger_entries`,
  `credit_adjustments`, `executions`, `quotes`, `quote_legs`, and
  `rate_snapshots` are protected from update/delete writes by triggers in
  `app/db/models.py`. Production should also remove update/delete access
  from the app role and run a scheduled reconciliation job.
- **Schema changes:** tables are created at startup; production should use
  Alembic migrations.
- **Rate refresh:** manual through `POST /rate-refreshes` and single-provider;
  production should add scheduling, retries, failover, and stale-rate alerts.
- **Metrics:** local `/metrics` only; not connected to dashboards or alerting.
- **Idempotency keys:** retained indefinitely. Production needs an
  `expires_at` column, a `created_at` index, and a periodic cleanup job
  (24–72h retention). Deferred to a follow-up — see
  `implementation_findings.md` A-001.

## With Another Day

- Add Alembic migrations.
- Add scheduled rate refresh using the existing refresh service, while keeping
  `POST /rate-refreshes` as an admin/debug endpoint.
- Add an executable demo script that creates a customer, credits balance, quotes, and executes.
- Add load-test output for quote/execution paths.
- Add stricter linting/type checks and enforce them in CI.
- Add tracing dashboards beyond structured logs and `/metrics`.
- Add idempotency-key retention (`expires_at` column + scheduled cleanup).
- Add richer OpenAPI examples.

## Time Tracking

I'm currently working a full-time role, so this was done across evenings and
breaks with quite a bit of context switching between tasks.

**Active-engagement time:** ~14 hours.
**Wall-clock span:** ~3 days of focused work between 07 May and 10 May 2026.

### 07 May 2026

- 11:25 AM – 12:10 PM — read the assignment and requirements
- *Break — unrelated tasks*
- 12:35 PM – 12:55 PM — finish reading requirements, take personal notes
- 1:00 PM – 1:22 PM — first pass on `SPEC.md`
- *Break — unrelated tasks*
- 3:19 PM – 4:20 PM — `SPEC.md` refinement
- 7:30 PM – 9:00 PM — `AGENTS.md` creation and initial code generation

### 08 May 2026

- 1:52 PM – 4:30 PM — initial implementation, linting, and code review
- 7:23 PM – 9:36 PM — test coverage and refactoring

### 09 May 2026

- 5:44 AM – 9:15 AM — code review, observability, and manual testing
- 10:11 PM – 11:37 PM — docs, healthz/readyz split, validation errors, and REVIEW.md

### 10 May 2026

- 6:30 PM – 7:00 PM — promote failure-path `log_event` calls to `ERROR` level

### 11 May 2026

- 11:00 PM – 11:30 PM — implementation-findings follow-ups (DB immutability triggers, schema-required `Idempotency-Key`, `execution.rejected` split, idempotency-key index) and final polish

## AI Tools Used

- **OpenAI Codex 5.3** — scaffolding, code generation, and test drafting.
- **Claude Code (claude-sonnet-4-6)** — code review passes.
