# Planted-Bug Review

I went through `planted_bugs/` the way I'd review a teammate's PR — skim, then read carefully, then run the tests. The 9 tests pass. They also don't exercise any of the actual problems, which is its own finding. Findings below are ordered by what I'd block the merge on first.

The short version: this implementation never moves money, lets the same quote execute twice, recomputes rates at settlement, and prices in the wrong direction. The pricing direction alone would cost the desk on every direct trade. None of these are subtle once you sit with the code.

I read `fx.py`, `app.py`, `db.py`, `rates.py`, and `tests/test_fx.py`, and ran `pytest` once inside `planted_bugs/` to confirm the suite is green. No load test, no multi-process run — the concurrency evidence in finding 4 is structural, not observed.

## Findings at a glance

| # | Severity | Area                | Title                                                                       |
|---|----------|---------------------|-----------------------------------------------------------------------------|
| 1 | blocker  | execution / ledger  | Execute never debits or credits a customer balance                          |
| 2 | blocker  | rates / pricing     | Spread is applied in the wrong direction; the customer wins, the bank loses |
| 3 | blocker  | execution / quote   | Execute recomputes the rate at settlement, ignoring the stored quote        |
| 4 | blocker  | concurrency         | Same quote can execute twice under concurrent requests                      |
| 5 | blocker  | idempotency         | Idempotency key isn't bound to the request payload                          |
| 6 | major    | decimal             | Money math goes through `float` before quantizing to Decimal                |
| 7 | major    | rates / pricing     | Inverse and cross-route pricing ignore directional spread                   |
| 8 | major    | rates / freshness   | No real provider, no freshness check, no upstream-failure handling          |
| 9 | major    | observability       | No `/healthz`, no `/metrics`, no structured logs, no quote→execute linkage  |
| 10 | minor   | errors              | Error responses are inconsistent and not `application/problem+json`         |
| 11 | minor   | tests               | Test suite covers only happy paths and would not catch any blocker above    |

## 1. Execute never moves money — blocker

* Schema has `quotes`, `transactions`, `idempotency` but no `customers` or `balances` (`db.py:22-56`).
* `FXEngine.execute_quote` writes a `transactions` row and flips `quotes.executed = 1` (`fx.py:134-159`) — nothing to debit or credit because the tables don't exist.
* **Production impact:** every trade returns `200 OK` with a transaction ID and no money has moved. Books and response stream disagree on every trade.
* **Fix:** add `customers` and `balances(customer_id, currency, balance)` with `UNIQUE(customer_id, currency)`. In execute: lock both balance rows, debit source, credit destination, insert the execution row — all in one transaction.
* **Spec:** ASSIGNMENT §1 ("atomic: both legs succeed or neither"); `SPEC.md` §10.

## 2. Spread runs the wrong way — blocker

* `rates.py:22-26`: `sell = mid * (1 + SPREAD_BPS)`, `buy = mid * (1 - SPREAD_BPS)`.
* `_effective_rate` returns `direct["sell"]` for the direct route (`fx.py:185`).
* USD→KES at mid 129.50 with 50 bps → customer gets 130.1475 KES per USD, *more* than mid. The bank is paying the customer to trade.
* **Production impact:** every direct trade loses 50 bps versus mid, structurally. At any volume that's the P&L.
* **Fix:** swap `buy`/`sell` in `rates.py`, or — cleaner — drop the pre-computed sides and apply the spread per leg from direction + spread side.
* **Spec:** `SPEC.md` §8 — for `source=base, destination=quote`, executable = `mid * (1 - sell_spread/10000)`; inverse direction prices with the buy spread and takes the reciprocal.

## 3. Execute recomputes the rate at settlement — blocker

* `fx.py:126-132` calls `_effective_rate(...)` *again* inside `execute_quote` and recomputes `final` from the new rate.
* The stored `rate` and `final_amount` from the original quote (`fx.py:81-95`) are written but never read on the execute path.
* The response (`fx.py:161-170`) returns `current_rate`, not the stored value.
* **Production impact:** customer accepts at one rate, settles at another the moment `RateProvider` ticks. Audit story is broken — can't reproduce the trade from the stored quote row because the rate that applied was never the quote's rate.
* **Fix:** read `row["rate"]`, `row["amount"]`, `row["final_amount"]` and use those; never call `_effective_rate` from execute.
* **Spec:** `SPEC.md` §6 ("quote terms are immutable"); §10 ("execution uses stored quote terms and never recomputes rates").

## 4. Same quote can execute twice — blocker

* `executed` check at `fx.py:123` happens *before* `_execute_lock` is acquired at `fx.py:134` — classic check-then-act; two threads can both pass the check and queue at the lock.
* `_execute_lock` is a module-scope `threading.Lock` (`fx.py:21`); it does nothing across multiple worker processes — the deployment shape any real Python web app ends up in.
* `transactions` has no `UNIQUE(quote_id)` (`db.py:39-48`), so the duplicate INSERT doesn't fail at the DB layer either.
* **Production impact:** under a normal retry storm, two transaction rows for one quote. Once balances exist (finding 1), the customer is double-debited.
* **Fix:** add `UNIQUE(quote_id)` on `transactions` and switch to `SELECT … FOR UPDATE` on the quote row. The application check becomes optional once the unique constraint is the source of truth.
* **Spec:** ASSIGNMENT §1 ("a test that fires N parallel executions of the same quote ID and asserts exactly one succeeds"); `SPEC.md` §10.

## 5. Idempotency key isn't bound to the payload — blocker

* `idempotency` table stores `key, response, created_at` (`db.py:50-54`) — no request hash, no `quote_id`, no method/path.
* `execute_quote` looks up by key alone and returns the cached response (`fx.py:102-110`).
* **Production impact:** replay-with-same-key looks fine. The dangerous case is a client reusing key `K` for a *different* quote — they get back the previous quote's response with a "successful" execution ID, and no execution happened against the new quote. Worse than double-execution: a silent integrity failure that only surfaces at reconciliation.
* **Fix:** hash method + path + body at the API boundary. Store `(endpoint, key, request_hash, response_payload, status_code, completed_at)`.
  * Same key + same hash → return stored response.
  * Same key + different hash → `409 idempotency_conflict`.
  * Same key + still in-flight → also `409`.
* **Spec:** ASSIGNMENT §1; `SPEC.md` §11.

## 6. `float` in the middle of a money path — major

* `fx.py:60-63`: `final = float(amount) * float(rate); final_decimal = Decimal(str(final)).quantize(QUANTUM, ROUND_HALF_UP)`.
* `Decimal(str(float(x)))` doesn't round-trip cleanly — `float` introduces FP error first; `str` captures the error; `Decimal` then sees the error.
* **Production impact:** seeded mids show small visible damage; awkward inputs (large amounts, repeating-decimal rates) are reproducible and customer-visible.
* **Fix:** `final_decimal = (amount * rate).quantize(QUANTUM, rounding=ROUND_HALF_EVEN)`. Stay in `Decimal`. `ROUND_HALF_EVEN` for repeated rounding to avoid bias is a separate decision worth making while the line is open.
* **Spec:** ASSIGNMENT §1 ("Decimal precision throughout"); `SPEC.md` §2 ("no binary floats").

## 7. Inverse and cross-route ignore direction — major

* Inverse (`fx.py:187-190`): `mid = (inverse["buy"] + inverse["sell"]) / 2; return 1 / mid`. Averaging buy and sell collapses the spread to zero — KES→USD ships at mid; the bank earns nothing.
* Cross-route (`fx.py:192-200`): returns `leg1["sell"] * leg2["sell"]` regardless of the customer's actual direction on each leg. Fallback at `fx.py:193-198` picks whichever direction it finds first, then multiplies "sell" anyway.
* Combined with finding 2, cross routes can be wildly wrong in either direction.
* **Fix:**
  * Inverse: `executable = 1 / (mid * (1 + buy_spread/10000))`.
  * Cross: decide the customer's direction on each leg (`source=base` or `source=quote`), apply the directional spread per leg, multiply the executable leg rates. Reproducing the parent rate from the leg rates should be exact.
* **Spec:** `SPEC.md` §8.

## 8. No real rate provider — major

* `RateProvider` is an in-memory dict (`rates.py:10-17`); `refresh()` re-applies the same seed (`rates.py:30-40`).
* Timestamp ticks every refresh because the seed call always succeeds — a stuck provider would look healthy forever.
* No freshness check before `generate_quote` (`fx.py:51-97`); no upstream call, no timeout, no failure path.
* **Production impact:** down/slow/stale provider modes go undetected and unhandled.
* **Fix:** wire `refresh()` to a real upstream (e.g. `fxapi.app`) with a configurable timeout. Persist refresh attempts (provider, timestamp, latency, error). Reject `POST /quotes` with `503 rates_stale` when `last_updated` is outside the freshness window; map upstream timeouts to `504` and bad responses to `502`.
* **Spec:** ASSIGNMENT §1; `SPEC.md` §§7, 9.

## 9. Observability gap — major

* `app.py` propagates `X-Request-Id` (`app.py:27-30`) but logs are unstructured plain text (`app.py:57-60`, `app.py:74`).
* No `/healthz`, no `/metrics`, no counters/histograms, no log fields tying a `quote_id` from creation through to execution.
* **Production impact:** can't tell if the service is alive, if rates are fresh, if execution failure rates are spiking, or trace a customer-reported bad trade through the logs.
* **Fix:**
  * `/healthz` (DB + rate freshness).
  * `/metrics` — Prometheus counters and histograms for quote/execution success/failure, idempotency replay/conflict, stale rates, refresh latency.
  * JSON logs carrying `request_id`, `customer_id`, `quote_id`, `execution_id`, `idempotency_key`, currencies, amounts, outcome.
* **Spec:** ASSIGNMENT §1 ("show example log output in the README"); `SPEC.md` §14.

## 10. Error shapes drift — minor

* Three different error shapes across three error paths in `app.py`:
  * `app.py:50` — `{"error": "invalid request: …"}`
  * `app.py:55` — `{"error": str(e)}`
  * `app.py:39` — `{"error": "internal_error", "correlation_id": cid}`
* None use `application/problem+json`; no machine-readable `code` or `retryable` field.
* **Production impact:** clients special-case each shape. Not a money bug, but real interop friction.
* **Fix:** adopt `{type, title, status, detail, instance, code, retryable}` everywhere, served with `application/problem+json`.
* **Spec:** `SPEC.md` §13.

## 11. Tests cover happy paths only — minor

* `tests/test_fx.py` has 9 tests — happy paths plus a few cheap rejections (zero amount, same currency, unknown id).
* Missing: concurrent-execute, idempotency-with-different-payload, insufficient-funds (impossible to write — no balances), atomic-rollback, stale-rate, upstream-failure tests.
* `test_execute_with_idempotency_key_returns_cached` (`tests/test_fx.py:61-66`) only checks that the same key returns the same `transaction_id`; it can't check that no money moved on replay because there's no money.
* **Production impact:** CI is actively misleading — 9/9 green, every blocker above passes through.
* **Fix:** after fixing findings 1–8, add parallel execute (N threads, exactly one success), idempotency replay vs. conflict, insufficient funds + balance unchanged, mid-execute injected failure rolls back, stale/down/slow/bad provider, decimal/property tests over random amounts.
* **Spec:** ASSIGNMENT §1 ("evidence that they actually work"); `SPEC.md` §15.

## What I didn't flag

* **SQLite as the persistence layer.** ASSIGNMENT explicitly allows it. Concurrency evidence is weaker on SQLite than Postgres, but that's a documented assignment choice, not a bug.
* **`SPREAD_BPS = Decimal("0.005")` is a misleading name.** It's a fractional spread (0.5%), not basis points. Cosmetic; the math is internally consistent.
* **`_execute_lock` serializes across all quote IDs.** Performance smell, not correctness — and once a per-quote DB lock replaces it (finding 4 fix), it's gone.
* **`/rates` exposes the full buy/sell book without auth.** Authn/authz is explicitly out of scope per ASSIGNMENT "Constraints."
* **`request.get_json()` accepts any body.** Pydantic isn't required by the assignment; tighter validation is a quality improvement, not a planted bug.

## How I'd sequence the fixes

1. Finding 1 — customers + balances + execute that actually moves money. Nothing else matters until this is real.
2. Findings 5, 4 — bind idempotency to the request hash; add `UNIQUE(quote_id)` and a per-quote DB lock.
3. Finding 3 — read stored quote terms in execute; never recompute.
4. Findings 2, 7 — fix spread direction and inverse/cross logic.
5. Finding 6 — drop floats; stay in Decimal end to end.
6. Finding 8 — real provider + freshness window + upstream failure handling.
7. Finding 9 — `/healthz`, `/metrics`, JSON logs.
8. Findings 10, 11 — problem+json errors and a real test plan.

## Caveats

* Didn't run a load test or a multi-process concurrency test against this. The evidence in finding 4 is structural — process-local lock + missing unique constraint — not observed.
* Read code; didn't drive HTTP traffic.
* Didn't look at security beyond pricing and audit safety, since auth/authz is explicitly out of scope.
