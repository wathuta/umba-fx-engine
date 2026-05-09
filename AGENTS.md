# AGENTS.md

Instructions for AI agents implementing the FX take-home.

## Source of Truth

* `SPEC.md` defines engine behavior.
* Do not add behavior that conflicts with `SPEC.md`.
* If something is ambiguous, make the smallest reasonable assumption, document it in `DECISIONS.md`, and continue.
* Do not reference files that do not exist, except files this assignment requires the agent to create.
* `README.md` must be created or updated as part of the final deliverable.
* `DECISIONS.md` already exists; update it when making implementation decisions, assumptions, or accepting/rejecting AI-suggested changes.

## Assignment Constraints

* Build a production-minded FX engine for USD, EUR, KES, and NGN.
* Use persistent Postgres storage for core state.
* Do not use pure in-memory storage, SQLite fallback, or fake repositories for required behavior.
* Demonstrate decimal precision, concurrency safety, idempotency, atomic execution, rate-source failure handling, and observability.
* Produce a clear `README.md` explaining setup, API usage, tests, design choices, and tradeoffs.

## Tech Stack

* Use Python FastAPI.
* Use Postgres via Docker Compose on host port `55432`.
* Use SQLAlchemy for persistence.
* Use Pydantic for API schemas.
* Use pytest and Hypothesis for tests.

## Commands

```bash
docker compose up -d postgres
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest
uvicorn app.main:app --reload
```

If migrations exist, run the project migration command before tests.

## Architecture Rules

* Keep FastAPI routers thin.
* Put business logic in services.
* Put DB access in repositories/data-access modules.
* Keep Decimal parsing, rounding, and currency metadata centralized.
* Use explicit DB transactions for money movement.
* Keep code concise but clear.
* Add contextual comments where the reason for the code is not obvious; explain why the code exists, not what each line does.
* Use Python docstrings for public modules, classes, and functions where they clarify purpose, inputs, side effects, or important invariants.
* Keep comments and docstrings accurate, short, and useful; do not add boilerplate comments just to satisfy a style rule.
* Do not duplicate business logic across routers, services, repositories, or tests.
* Reuse shared helpers for Decimal parsing, rounding, currency metadata, error mapping, and response formatting.
* Follow DRY where it improves correctness and maintainability.
* Do not over-abstract simple one-off code.

## Hard Requirements

* Use Python `Decimal`; never use binary floats for money or rates.
* Use UUIDs for IDs.
* Accept and return money/rates as decimal strings.
* Set `ROUND_HALF_EVEN` explicitly.
* Quote creation must not read, reserve, debit, credit, or mutate balances.
* Quotes must store executable terms: rate, route, total spread bps, amounts, and expiry. Per-leg pricing detail (mid, executable rate, spread side/bps, snapshot id) is stored in `quote_legs`. `quote_legs` rows are never updated.
* Execute must use stored quote terms and never recompute rates.
* Debit, credit, execution row, and completed idempotency result must commit together.
* The same quote can execute exactly once.
* `POST /executions` must require `Idempotency-Key`.
* Same idempotency key and same request must return the original response.
* Same idempotency key and different request must return `409`.
* Idempotent replay must not mutate balances.

## Testing Rules

* Use DB-backed tests, not pure in-memory tests.
* Mock external rate provider calls.
* Test direct, inverse, cross-route, multi-leg spread compounding, quote expiry, single execution, concurrency, idempotency, rollback, stale/down/slow/bad provider responses, `/healthz`, and `/metrics`.
* Run the full test suite before reporting completion.

## Git Rules

* Do not create git commits unless explicitly asked.
* Work in small coherent changes.
* Do not push changes.
* Before finishing, report:

  * files changed
  * tests run
  * known limitations
  * suggested commit message

## Process Rules

* Keep code readable and scoped.
* Do not leave TODOs or stubs for required behavior.
* Do not create prompt scratch files.
* Do not commit assignment-local notes.
* Update `DECISIONS.md` when making assumptions, choosing between valid implementation options, or rejecting an AI suggestion.
* Create or update `README.md` before completion.

## README Requirements

`README.md` must be concise and include:

* project overview
* setup and run commands
* test command
* sample API requests
* known limitations
* what would be improved with another day
* estimated wall-clock time and active-engagement time
* short design notes for Decimal precision, concurrency, idempotency, atomic execution, rate-source failure handling, and observability
* example structured log showing correlation from quote to execute
* `/healthz` and `/metrics` usage
* link or pointer to `SPEC.md`, `DECISIONS.md` and `AGENTS.md`

## Completion Checklist

Before reporting done:

* `SPEC.md` requirements are implemented.
* `README.md` exists and is accurate.
* `DECISIONS.md` is updated.
* Postgres-backed tests pass.
* No money/rate logic uses binary floats.
* Execution and idempotency result commit atomically.
* No git commits were created.
