# Decisions

## Approach

* I treated this as a small production system, not just an endpoint exercise.
* Money correctness came first: exact decimal math, atomic execution, idempotent retries, observable failures.
* Scope stayed narrow on purpose — proving core behavior with tests mattered more than extra features.
* Decisions are visible: every non-obvious choice is either documented here or in `SPEC.md`, with cross-references to the spec for storage and protocol-level rules.

## Main Tradeoffs

### 1. FastAPI and Postgres

* FastAPI keeps the HTTP layer thin and easy to inspect.
* Postgres provides the production-like guarantees that matter during execution: transactions, row locks (`SELECT ... FOR UPDATE`), and uniqueness constraints.
* Two concurrent requests on the same quote can be proven safe only at the database layer; an application-level lock would not survive multi-worker deployments.
* Tradeoff: heavier local setup than SQLite; better evidence for concurrency safety.

### 2. Money Model

* Chose Python `Decimal` plus Postgres `NUMERIC` over integer minor units. Storage and rounding rules are in `SPEC.md` §2.
* Reasoning: FX still needs decimal rate math, so integer cents would add boundary-conversion code at the API and DB without removing the underlying precision problem.
* `ROUND_HALF_EVEN` reduces long-term rounding bias across repeated transactions and is the banker's-rounding default.

### 3. Ledger Model

* Chose a **double-entry ledger with a materialized `balances` cache** over in-place balance updates.
* Every credit and every execution inserts append-only `ledger_entries` rows (positive amounts, `direction = debit | credit`) in the same transaction as the `balances` update.
* `ledger_entries` is the source of truth; `balances` is a cache. The invariant `balance == SUM(credits) - SUM(debits)` per `(customer, currency)` is asserted in tests after every money-moving operation and can be reconciled in production from the ledger alone.
* DB-level guards: `CHECK (amount > 0)`, `CHECK (direction IN ('debit','credit'))`, and `UNIQUE (reference_type, reference_id, direction)` so a single execution cannot produce two debits or two credits.
* DB-level immutability: a Postgres `BEFORE UPDATE OR DELETE` trigger raises on `ledger_entries`, `credit_adjustments`, `executions`, `quotes`, `quote_legs`, and `rate_snapshots`. An ad-hoc query against the audit tables cannot silently corrupt history; the app role never gets the privilege to do so by accident.
* Tradeoff: an extra two inserts per execution; in return, a full audit trail and the ability to rebuild balances at any point in time.

### 4. Quotes and Rate Storage

**Stored quote terms.** The accepted executable rate, route, spread, source/destination amounts, expiry, and snapshot link are persisted at quote time (table layout in `SPEC.md` §3, §6). Execution uses the stored terms only — never recomputed. This prevents repricing after a rate refresh and makes idempotent replay trivial.

**Per-leg storage for cross routes.** `quote_legs` stores each leg's rate, spread side/bps, and snapshot link.

* A routed quote can depend on more than one currency pair and more than one provider snapshot.
* A single parent-level snapshot pointer cannot describe a multi-leg quote.
* Multiplying the stored leg `executable_rate` values reproduces the parent `Quote.executable_rate` exactly — the audit round-trip is the design goal.

**Current rates + immutable snapshots.** `current_rates` gives fast reads and a simple freshness check; `rate_snapshots` keeps immutable provider history for audit. Tradeoff: refresh writes are slightly more complex (history insert + latest upsert in one transaction); the read path stays simple.

**Only store rates the provider actually sends.** `CANONICAL_PAIRS` keeps only USD/KES, USD/NGN, and USD/EUR. EUR/KES, EUR/NGN, and KES/NGN were previously stored as triangulated rates (USD rates divided), which earned one spread (~50 bps) where two were owed (~100 bps). Removing them forces cross pairs through USD at quote time, giving each leg its own directional spread. The audit invariant holds: product of stored leg rates equals the parent rate.

**`pair_mid_rate` simplified.** Previously handled direct lookup, inverse, and cross-pair division. With only USD pairs in `CANONICAL_PAIRS`, only direct lookup is needed. The function now takes the quote currency and returns the provider rate for it. Restore the other branches if non-USD pairs are added back.

### 5. Rate Provider

* Used `fxapi.app` because it returns real exchange rates without an API key and was reachable during implementation.
* The earlier-considered provider required a key and signup was unavailable from Nairobi.
* Tradeoff: practical for a take-home, not enough for production on its own.
* Production improvement: add an SLA-backed provider, failure monitoring, and a fallback rate source.

### 6. Observability

* Structured JSON logs plus `/healthz`, `/readyz`, and `/metrics` (fields and metrics listed in `SPEC.md` §14).
* Logs link quote and execute activity by `request_id`, `customer_id`, `quote_id`, and `execution_id`.
* `/healthz` reports process liveness only (no DB or rate check); `/readyz` is stricter (DB ping + rate freshness) so a stale provider or unreachable database takes the service out of rotation without killing the process.
* Metrics distinguish client-side rejections (insufficient funds, idempotency conflict, expired quote) from upstream and provider failures, so a single counter spike has a clear cause.
* What I did not add: full distributed tracing — extra infrastructure without proving more of the core behavior.

### 7. Scope

* Deferred Alembic migrations, scheduled rate refresh, fallback provider, demo script, load-test output, CI lint/type gating, and tracing dashboards (full list in `README.md` "With Another Day").
* Spent that time on tests for money correctness, concurrency, idempotency, and atomic rollback — the behaviors the assignment grades hardest.
* Tradeoff: smaller feature surface, stronger evidence on the core invariants.

## What I Owned vs Delegated

**Owned:**

* Money model and rounding policy.
* Concurrency invariants (row locks, deterministic lock order, DB-level uniqueness).
* Idempotency invariants (request-hash binding, replay semantics).
* Spread-direction rules and the cross-route audit-reproducibility constraint.
* Error semantics clients depend on (status codes, machine codes, problem+json shape).
* Rate-refresh transaction boundary (history insert + latest upsert in one commit).
* Final review of generated code and tests.
* Scope decisions for the time-boxed assignment.

**Delegated:**

* First-pass scaffolding.
* Repetitive API, schema, and test drafting.
* Edge-case suggestions during reviews.
* Second-pass review for missed failure modes.

**Boundary:**

* AI for speed, not final judgment.
* Generated code was not trusted by default; spec compliance was verified before merging.
* Anything affecting balances, rates, retries, or transactions had to be backed by a test, not a hand-wave.
* When a generated suggestion contradicted SPEC, I changed the code to match the spec, not the other way around.

## What I Accepted, Rejected, and Overrode

**Accepted:**

* Problem-style error shape — stable, machine-readable.
* Property-based tests for money — random beats hand-picked.
* Explicit idempotency keys at the API boundary — retries safe, visible, testable.
* One canonical current-rate row per pair, with inverse derived in code — avoids inverse drift.

**Rejected:**

* Binary floats for money or rate logic — exact decimal end to end.
* Treating execute as only flipping `executed` — real work is the atomic debit/credit.
* In-memory state for concurrency tests — cannot prove row locking, transactions, or DB uniqueness.
* JSON numbers for money inputs — API takes decimal strings to avoid float ambiguity.

**Overrode:**

* Recomputing rates during execution — stored quote terms are the source of truth.
* Relying on application checks alone — DB must also enforce uniqueness and consistency under concurrency.

## AI Mistakes I Caught

1. **Integer minor units instead of `Decimal`.** The AI initially pushed toward integer cents. I switched to `Decimal` + `NUMERIC` because the assignment asks for decimal precision throughout.
2. **Duplicate inverse rate rows.** The AI suggested storing both direct and inverse rates; the two could disagree over time. Changed to one canonical row per pair, with the inverse computed in code when needed.
3. **Repeated magic values and missing comments.** Replaced repeated literals with named constants and added comments only where the WHY was non-obvious.
4. **Cross-route round-once-at-end.** The AI proposed full-precision legs and rounding only the compound. I tried it and reverted: stored leg rates must fit `NUMERIC(20,10)`, so multiplying stored leg rates would compute a different parent rate than the row carries. Audit reproducibility wins.

## What I Verified Myself

* Currency minor units for USD/EUR/KES/NGN before locking the rounding rules.
* `fxapi.app` response shape, reachable endpoints, and failure behavior — so provider handling matched the real API rather than an assumed one.
* HTTP status choices for validation, conflicts, and upstream failures — so API errors stayed intentional, not incidental.
* Idempotency for same-key retries and same-key different-payload conflicts; replay does not re-mutate balances.
* Concurrency claims via parallel execution tests, not code inspection alone — N parallel calls on one quote yield exactly one success and N-1 conflicts.
* Provider failure behavior via deterministic mocked-provider tests for stale, down, and slow rates.
* Atomic rollback via a test-only failure injection point between debit and credit; balances and execution row are unchanged after the injected failure.
