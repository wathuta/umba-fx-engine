# Planted-Bug Review

I went through `planted_bugs/` the way I'd review a teammate's PR — skim, then read carefully, then run the tests. The 8 tests pass. Findings below are ordered by what I'd block the merge on first.

The short version: this implementation never moves money, lets the same quote execute twice, recomputes rates at settlement, and prices in the wrong direction. The pricing direction alone would cost the desk on every direct trade. None of these are subtle once you sit with the code.

I read `fx.py`, `app.py`, `db.py`, `rates.py`, and `tests/test_fx.py`, and ran `pytest` once inside `planted_bugs/` to confirm the suite is green. No load test, no multi-process run — the concurrency evidence in finding 4 is structural, not observed from tests.

## Findings at a glance

| #  | Severity | Area               | Title                                                                       |
|----|----------|--------------------|-----------------------------------------------------------------------------|
| 1  | blocker  | execution          | Execute never debits or credits a customer balance                          |
| 2  | blocker  | rates              | Spread is applied in the wrong direction; the customer wins, the bank loses |
| 3  | blocker  | execution / quote  | Execute recomputes the rate at settlement, ignoring the stored quote        |
| 4  | blocker  | concurrency        | Same quote can execute twice under concurrent requests                      |
| 5  | blocker  | idempotency        | Idempotency key isn't bound to the request payload                          |
| 6  | major    | decimal            | Money math goes through `float` before quantizing to Decimal                |
| 7  | major    | rates / pricing    | Inverse and cross-route pricing ignore directional spread                   |
| 8  | major    | rates / freshness  | No real provider, no freshness check, no upstream-failure handling          |
| 9  | major    | observability      | No `/healthz`, no `/metrics`, no structured logs, no quote→execute linkage  |
| 10 | minor    | errors             | Error responses are inconsistent and not `application/problem+json`         |
| 11 | minor    | tests              | Test suite cannot catch any of the blockers above                           |

## 1. Execute never moves money — blocker

* Schema has `quotes`, `transactions`, `idempotency` but no `customers` or `balances` tables.
* `execute_quote` writes a `transactions` row and flips `quotes.executed = 1` — nothing moves because the balance tables don't exist.
* **Production impact:** every trade returns `200 OK` with a transaction ID and no money has moved. The books and the response stream disagree on every trade.
* **Fix:** add `customers` and `balances(customer_id, currency, balance)` with `UNIQUE(customer_id, currency)`. In execute: lock both balance rows, debit source, credit destination, insert the execution row — all in one transaction.
* **Spec:** violates atomicity — both legs must succeed or neither; a schema with no balance tables cannot satisfy this.

## 2. Spread runs the wrong way — blocker

* `rates.py:22-26`: `sell = mid * (1 + SPREAD_BPS)`, `buy = mid * (1 - SPREAD_BPS)`.
* `_effective_rate` returns `direct["sell"]` for the direct route (`fx.py:185`).
* USD→KES at mid 129.50 with 50 bps → customer gets 130.1475 KES per USD, more than mid. The bank is paying the customer to trade.
* **Production impact:** every direct trade loses 50 bps versus mid, structurally. At any volume that is the entire P&L.
* **Fix:** swap `buy`/`sell` in `rates.py`, or — cleaner — drop the pre-computed sides and apply the spread per leg from direction and spread side.
* **Spec:** violates the spread direction rule — for `source=base, destination=quote`, executable rate = `mid * (1 - sell_spread/10000)`; the inverse direction prices with the buy spread and takes the reciprocal.

## 3. Execute recomputes the rate at settlement — blocker

* `fx.py` calls `_effective_rate(...)` again inside `execute_quote` and recomputes `final` from the new rate.
* The stored `rate` and `final_amount` written at quote creation are never read on the execute path.
* The response returns `current_rate`, not the stored value.
* **Production impact:** the customer accepts terms at one rate and settles at whatever the rate happens to be at execution time. The quote row becomes an unreliable audit record — you cannot reproduce the trade from it.
* **Fix:** read `row["rate"]` and `row["final_amount"]` directly; never call `_effective_rate` from execute.
* **Spec:** violates quote immutability — once a quote is created, its rate and amounts must not change at settlement.

## 4. Same quote can execute twice — blocker

* The executed check (`fx.py:123`) happens before the lock is acquired (`fx.py:134`). Two concurrent requests both pass the check, then both execute.
* The lock is process-local (`fx.py:21`) — useless once the app runs more than one worker process.
* `transactions` has no `UNIQUE(quote_id)` (`db.py:39-48`), so the database won't catch the duplicate either.
* **Production impact:** a client retry triggers two executions for one quote. This has an impact on the balances.
* **Fix:** add `UNIQUE(quote_id)` on `transactions` and use `SELECT … FOR UPDATE` on the quote row. The DB constraint becomes the source of truth to cater for multiprocess deployments.
* **Spec:** violates the concurrency requirement — exactly one execution must succeed for a given quote.

## 5. Idempotency key isn't bound to the payload — blocker

* `idempotency` table stores `key, response, created_at` (`db.py:50-54`) — no request hash, no `quote_id`, no method or path.
* `execute_quote` looks up by the key alone and returns the cached response.
* **Production impact:** a client reusing key `K` for a different quote gets back the previous quote's response with a "successful" execution ID, and no execution happens against the new quote. This is a silent integrity failure that only surfaces at reconciliation.
* **Fix:** hash method + path + body at the API boundary. Store `(endpoint, key, request_hash, response_payload, status_code, completed_at)`.
  * Same key + same hash → return stored response.
  * Same key + different hash → `409 idempotency_conflict`.
  * Same key + still in-flight → `409`.
* **Spec:** violates the idempotency rule — the key must be bound to the request payload; a same-key/different-payload request must conflict, not silently replay.

## 6. `float` in the middle of a money path — major

* `fx.py`: `final = float(amount) * float(rate); final_decimal = Decimal(str(final)).quantize(QUANTUM, ROUND_HALF_UP)`.
* `Decimal(str(float(x)))` does not round-trip cleanly — `float` introduces FP error first; `str` captures the error; `Decimal` then inherits it.
* **Production impact:** on most inputs the error is sub-cent, but on large amounts or repeating-decimal rates it becomes customer-visible. `float(10_000) * float(0.07)` gives `700.0000000000001` instead of `700.00`.
* **Fix:** stay in `Decimal` throughout: `final_decimal = (amount * rate).quantize(QUANTUM, rounding=ROUND_HALF_EVEN)`. Switching to `ROUND_HALF_EVEN` while the line is open also removes rounding bias on repeated operations.
* **Spec:** violates the no-binary-floats rule — all money calculations must use exact decimal arithmetic.

## 7. Inverse and cross-route pricing ignore directional spread — major

* Inverse (`fx.py:187-190`): `mid = (inverse["buy"] + inverse["sell"]) / 2; return 1 / mid`. Averaging buy and sell collapses the spread to zero algebraically — KES→USD executes at mid and the bank earns nothing.
* Cross-route (`fx.py:192-200`): returns `leg1["sell"] * leg2["sell"]` regardless of the customer's actual direction on each leg. The fallback picks whichever direction it finds first, then multiplies `"sell"` regardless.
* Combined with finding 2, cross routes can be wrong in either direction.
* **Fix:**
  * Inverse: `executable = 1 / (mid * (1 + buy_spread / 10000))`.
  * Cross: determine the customer's direction on each leg (`source=base` or `source=quote`), apply the directional spread per leg, then multiply the executable leg rates.
* **Spec:** violates the spread direction rule — inverse and cross routes must apply directional spreads per leg, not average them away.

## 8. No real rate provider — major

* `RateProvider` is an in-memory dict (`rates.py:10-17`); `refresh()` re-applies the same hardcoded seed (`rates.py:30-40`).
* `self._last_updated` ticks on every `refresh()` because the seed call always succeeds — a stuck or failing provider would appear permanently healthy.
* There is no freshness check before `generate_quote`; stale or missing rates are never detected.
* **Production impact:** rates are frozen at startup values. Market moves are invisible. A down provider shows as healthy. Quotes are priced on arbitrarily stale data with no signal to the operator.
* **Fix:** wire `refresh()` to a real upstream (e.g. `fxapi.app`) with a configurable timeout. Persist refresh attempts (provider, timestamp, latency, error). Reject `POST /quotes` with `503 rates_stale` when `last_updated` is outside the freshness window; map upstream timeouts to `504` and bad payloads to `502`.
* **Spec:** violates the freshness and failure-handling rules — quotes must be rejected when rates are stale, and upstream failures must map to distinct error codes.

## 9. Observability gap — major

* `app.py` propagates `X-Request-Id` (`app.py:27-30`) but logs are unstructured plain text (`app.py:57-60`, `app.py:74`).
* No `/healthz`, no `/metrics`, no counters or histograms, no log fields linking a `quote_id` from creation through to execution.
* **Production impact:** no way to tell if the service is alive, whether rates are fresh, whether execution failure rates are spiking, or trace a customer-reported bad trade through the logs.
* **Fix:**
  * `/healthz` — DB connectivity and rate freshness.
  * `/metrics` — Prometheus counters and histograms for quote/execution success/failure, idempotency replay/conflict, stale rates, and refresh latency.
  * Structured JSON logs carrying `request_id`, `customer_id`, `quote_id`, `execution_id`, `idempotency_key`, currencies, amounts, and outcome on every event.
* **Spec:** violates the observability requirements — the service must expose health endpoints, structured logs, and metrics.

## 10. Error responses are inconsistent — minor

* Three different error shapes across three handlers in `app.py`:
  * `app.py:50` — `{"error": "invalid request: …"}`
  * `app.py:55` — `{"error": str(e)}`
  * `app.py:39` — `{"error": "internal_error", "correlation_id": cid}`
* None use `application/problem+json`; no machine-readable `code` or `retryable` field.
* **Production impact:** clients must special-case each shape. Not a money bug, but real interop friction that compounds as the API grows.
* **Fix:** adopt a single error shape `{type, title, status, detail, instance, code, retryable}` served with `Content-Type: application/problem+json` from every handler.
* **Spec:** violates the error contract — all responses must use a single consistent shape with machine-readable codes.

## 11. Test suite cannot catch any of the blockers above — minor

* `tests/test_fx.py` has 8 tests — happy paths plus a few cheap rejections (zero amount, same currency, unknown ID).
* Missing coverage: concurrent execute, idempotency with a different payload, insufficient funds (impossible to write — no balances exist), atomic rollback, stale rates, and upstream-failure handling.
* `test_execute_with_idempotency_key_returns_cached` only checks that the same key returns the same `transaction_id`; it cannot verify that no money moved on replay because money never moves at all.
* **Production impact:** CI is actively misleading — 9/9 green while every blocker above passes through undetected.
* **Fix:** after resolving findings 1–8, add parallel execute (N threads, exactly one success), idempotency replay versus conflict, insufficient funds with balance unchanged, mid-execute injected failure that rolls back, stale/down/slow/bad-payload provider, and decimal/property tests over random amounts and pairs.
* **Spec:** violates the evidence requirement — the assignment requires demonstrable proof that behaviors work, not assertions.

## What I didn't flag

* **SQLite as the persistence layer.** The assignment explicitly allows it. Concurrency evidence is weaker on SQLite than Postgres, but that is a documented assignment choice, not a bug.
* **`SPREAD_BPS = Decimal("0.005")` is a misleading name.** It is a fractional spread (0.5%), not a basis-point value. Cosmetic; the math is internally consistent.
* **`_execute_lock` serializes across all quote IDs.** Performance smell, not a correctness issue — and once a per-quote DB lock replaces it (finding 4 fix), it goes away.
* **`/rates` exposes the full buy/sell book without auth.** Auth/authz is explicitly out of scope per the assignment constraints.
* **`request.get_json()` accepts any body.** Pydantic is not required by the assignment; tighter validation is a quality improvement, not a planted bug.
* **`/quotes/<quote_id>/execute` puts a verb in the URL.** REST convention models the execution as a resource (`POST /executions`), which is the shape my own implementation uses. Worth raising in a real PR, but the endpoint works and the assignment does not prescribe an API shape.

## How I'd sequence the fixes

1. Finding 1 — customers + balances + execute that actually moves money. Nothing else matters until this is real.
2. Findings 5, 4 — bind idempotency to the request hash; add `UNIQUE(quote_id)` and a per-quote DB lock.
3. Findings 2, 7 — fix spread direction and inverse/cross logic.
4. Finding 3 — read stored quote terms in execute; never recompute.
5. Finding 6 — drop floats; stay in Decimal end to end.
6. Finding 8 — real provider + freshness window + upstream failure handling.
7. Finding 9 — `/healthz`, `/metrics`, structured JSON logs.
8. Findings 10, 11 — consistent error shape and a real test plan.

## Caveats

* Did not run a load test or a multi-process concurrency test. The evidence in finding 4 is structural — process-local lock plus missing unique constraint — not observed.
* Read code only; did not drive HTTP traffic given the state of the implementation.
* Did not review for security beyond pricing and audit safety, since auth/authz is explicitly out of scope.
* Used Claude Code as a second pass on the code; all severity rankings and production framing are mine. Markdown formatting and final polishing were also done with Claude Code.
