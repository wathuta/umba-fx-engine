# FX Engine Spec

## 1. Goal
Build a FastAPI + Postgres FX engine for USD, EUR, KES, and NGN with customer balances, 60-second quotes, atomic execution, idempotent retries, rate refreshes, and observability.

## 2. Global Rules
- IDs: UUID.
- Timestamps: UTC ISO 8601 at the API boundary.
- Auth/authz: out of scope.
- API amounts/rates: decimal strings.
- Supported currencies are exactly USD, EUR, KES, and NGN.
- Money math: Python `Decimal` only; no binary floats.
- DB money: `NUMERIC(20,2)`.
- DB rates: `NUMERIC(20,10)` or higher.
- Spreads: integer basis points.
- Currency decimal places: USD/EUR/KES/NGN = 2.
- Rounding: `ROUND_HALF_EVEN`, set explicitly in code.
- Leg mid-rate/spread math uses `Decimal`; persisted leg executable rates are rounded to `NUMERIC(20,10)` before storage and compounding.
- Final destination amount is rounded once at the destination currency decimal places.

## 3. Core Tables
- `customers`: customer identity.
- `balances`: `customer_id`, `currency`, `balance`; unique `(customer_id, currency)`.
- `rate_refreshes`: provider refresh attempt with status, timestamps, counts, latency, and error fields.
- `rate_snapshots`: immutable provider rates with `rate_refresh_id`, canonical pair, `mid_rate`, provider timestamp, fetched time, raw payload hash; indexed by `(base_currency, quote_currency, fetched_at desc)`.
- `current_rates`: latest usable canonical pair with `mid_rate`, `buy_spread_bps`, `sell_spread_bps`, `rate_snapshot_id`, `last_updated_at`; unique `(base_currency, quote_currency)`.
- `quotes`: immutable terms with customer, currencies, source/destination amounts, executable rate, route, total spread bps, `created_at`, and `expires_at`. Provenance lives in `quote_legs`.
- `quote_legs`: per-leg pricing with `quote_id`, `position`, source/destination currencies, `mid_rate`, `executable_rate`, `spread_side`, `spread_bps`, and `rate_snapshot_id`; unique `(quote_id, position)`. Rows are never updated. Stores the math at the time the quote was made so we can rebuild a quote later even if `current_rates` has changed.
- `executions`: successful execution rows with debit leg, credit leg, customer, quote, and timestamp; unique `quote_id`.
- `idempotency_keys`: execute idempotency record with endpoint, key, request hash, response payload/status, and completion timestamp; unique `(endpoint, key)`.

## 4. API Contract
- `POST /customers`
  - Input: none.
  - Output: `customer_id`.
- `GET /customers/{customer_id}/balances`
  - Output: balances for USD, EUR, KES, NGN as decimal strings.
- `POST /customers/{customer_id}/balance-credits`
  - Input: `currency`, `amount`.
  - Output: updated balance.
  - Purpose: test/setup only.
- `POST /quotes` -> create quote; contract below.
- `POST /executions` -> execute quote; contract below.
- `POST /rate-refreshes`
  - Input: empty body for default refresh.
  - Output: `rate_refresh_id`, `status`, `fetched_at`, `pairs_updated`.
- `GET /healthz`
  - Output: app health, DB connectivity, and rate freshness.
- `GET /metrics`
  - Output: quote, execution, idempotency, stale-rate, and rate-refresh metrics.

## 5. Customer Balance Rules
- New customers start with zero balances for all supported currencies.
- Manual credits must reject unsupported currencies and non-positive amounts.
- Balance reads return all supported currencies even when balance is zero.
- `execute` is the only operation that debits balances.
- Balance updates must occur inside DB transactions.
- Missing balance rows are created at zero before credit/debit logic runs.

## 6. Quote Contract
- Input: `customer_id`, `source_currency`, `destination_currency`, `source_amount`.
- Output: `quote_id`, `executable_rate`, `destination_amount`, `route`, `expires_at`.
- `source_currency != destination_currency`.
- Both currencies must be supported.
- `source_amount > 0`.
- `created_at = now`.
- `expires_at = created_at + 60 seconds`.
- Quote records executable rate, route, additive spread-bps summary, source amount, destination amount, and expiry. Per-leg pricing detail (mid rate, executable rate, spread side/bps, snapshot id) is stored in `quote_legs`; rebuild pricing provenance from there.
- Quote and quote_leg rows are never updated after they are written.
- Quote creation never reads, reserves, debits, credits, or mutates balances.
- Execution uses stored quote terms and never recomputes rates.

## 7. Rate Refresh Contract
- Provider returns base currency, timestamp/date, and a rates map.
- Refresh inserts one `rate_refreshes` row.
- Default refresh fetches all supported canonical pairs.
- Refresh inserts immutable `rate_snapshots` rows for fetched canonical pairs.
- Refresh upserts `current_rates` rows for fetched canonical pairs.
- Each `current_rates.rate_snapshot_id` must point to the snapshot row used for that current rate.
- Refresh writes happen in one DB transaction.
- If refresh fails, the failed attempt is recorded in `rate_refreshes`.
- Failed refresh attempts do not update `current_rates`.
- Successful refresh updates `current_rates.last_updated_at`.
- Quotes read `current_rates`, verify freshness, and store each leg's `rate_snapshot_id` on the matching `quote_legs` row; normal quote creation does not read `rate_snapshots`.

## 8. Rate Direction And Spread Rules
Canonical pair:
- `base_currency/quote_currency`.
- `mid_rate` = quote currency units per 1 base currency.

Supported conversion pairs:
- USD/KES
- USD/NGN
- USD/EUR
- EUR/KES
- EUR/NGN
- EUR/USD
- KES/NGN
- NGN/KES
- The API supports these pairs plus all inverses.

Routing:
- Prefer direct pair when the requested pair or its canonical inverse exists in `current_rates`.
- Else route through USD.
- Else route through EUR.
- `current_rates` stores one canonical orientation per currency pair; inverses are derived in code.
- Cross routes compute an executable rate per leg.
- Cross-route executable rate = rounded product of stored leg executable rates.
- Cross-route spreads compound per leg.

Spread direction:
- If source is base and destination is quote, use `sell_spread_bps`.
- Formula: `executable_rate = mid_rate * (1 - sell_spread_bps / 10000)`.
- If source is quote and destination is base, use `buy_spread_bps`.
- Formula: `priced_rate = mid_rate * (1 + buy_spread_bps / 10000)` and `executable_rate = 1 / priced_rate`.
- Final destination amount = `source_amount * executable_rate`, rounded once at destination currency decimal places.

## 9. Rate Freshness Policy
- Default freshness window: 5 minutes.
- If `current_rates.last_updated_at` is fresh, quotes may proceed.
- If refresh fails but current rates are fresh, continue quoting and emit warning metric/log.
- If rates are stale, `POST /quotes` returns `503`.
- Invalid upstream response returns `502`.
- Upstream timeout returns `504`.
- Slow upstream response times out using the configured HTTP timeout.

## 10. Execution Contract
- Input: `quote_id`.
- Header: `Idempotency-Key` required.
- Output: `execution_id`, `quote_id`, debit leg, credit leg, final source/destination balances.
- Execution allowed only when quote exists, is unexecuted, has `now < expires_at`, and source balance is sufficient.
- A quote is unexecuted when no `executions` row exists for its `quote_id`.
- Debit, credit, execution row, and completed idempotency result happen in one DB transaction.
- If any step fails, rollback all balance, execution, and idempotency writes.
- Source balance cannot go negative.
- Destination credit can only increase destination balance.
- Executed credit amount must exactly equal quoted destination amount.
- Concurrent same-quote execution gives exactly one success.

Transaction order:
- Validate idempotency key.
- Lock or create idempotency record for this request.
- Lock quote row.
- Validate quote executable.
- Create missing zero balance rows before locking if needed.
- Lock source and destination balance rows in deterministic order.
- Debit source balance.
- Credit destination balance.
- Insert execution row.
- Store completed idempotency result.
- Commit.

## 11. Idempotency Rules
- Scope: `POST /executions`.
- Request hash = method + path + body hash.
- Idempotency key is required and must be non-empty.
- Same key + same request hash + completed result returns stored response.
- Same key + different request hash returns `409`.
- Stored idempotency response must match the original response body and status code.
- Validation failures are not cached.
- In-flight conflicts are not cached as completed results.
- Replays must not create a second execution or mutate balances again.

## 12. Atomicity Demonstration
- Use a test-only failure injection point after source debit and before destination credit.
- Injected failure must roll back the DB transaction, create no execution row, leave quote unexecuted, and leave balances unchanged.

## 13. Error Semantics
All errors return `application/problem+json` with `type`, `title`, `status`, `detail`, `instance`, `code`, and `retryable`.

Status mapping:
- `400`: malformed JSON.
- `415`: unsupported content type.
- `422`: invalid fields, currency, amount, or same source/destination currency.
- `404`: customer or quote not found.
- `409`: expired quote, executed quote, insufficient funds, idempotency conflict, concurrent execution conflict.
- `429`: rate limited.
- `502`: bad upstream response.
- `503`: stale rates.
- `504`: upstream timeout.
- `500`: internal error.

Required machine codes:
- `quote_expired`
- `quote_already_executed`
- `insufficient_funds`
- `idempotency_conflict`
- `rates_stale`
- `upstream_bad_response`
- `upstream_timeout`

## 14. Observability
- Every request has `request_id`.
- `request_id` may be accepted from `X-Request-ID`; otherwise generated by the app.
- Quote/execute logs include `request_id`, `customer_id`, `quote_id`, `execution_id`, `idempotency_key`, currencies, amounts, executable rate, and outcome.
- Rate refresh logs include `request_id`, `rate_refresh_id`, provider, status, pairs updated, latency, and freshness.
- Logs are structured JSON.
- Metrics cover quote creation/expiry, execution success/failure, idempotency replay/conflict, insufficient funds, stale rates, and rate refresh health/latency.

## 15. Test Requirements
- Decimal/property tests for random amounts, rates, and supported pairs.
- Quote lifecycle tests: valid before expiry, rejected at/after expiry, executed at most once.
- Rate tests: direct, inverse, cross-route, and multi-leg spread compounding.
- Execute concurrency test: N parallel executions of one quote gives exactly one success.
- Idempotency tests: same payload replays, different payload returns `409`, replay does not mutate balances.
- Atomicity tests: insufficient funds and injected mid-execute failure leave persisted state unchanged.
- Rate-source tests: fresh cached rates, stale rates, down provider, slow provider, bad provider response.
- Observability tests: `/healthz` reports DB/rate freshness and `/metrics` exposes required counters.

## 16. Out of Scope
- Auth/authz.
- Payment rails or external settlement.
- KYC/AML.
- Limits and fees beyond spread.
- Multi-tenant permissions.
- Distributed transactions.
