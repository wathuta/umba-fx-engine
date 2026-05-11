"""Shared constants used by multiple application modules."""

from decimal import Decimal

# Endpoint identifiers are shared by routing and idempotency hashing.
HTTP_POST = "POST"
EXECUTIONS_PATH = "/executions"

# Shared outcome strings prevent quote and execution logs from drifting.
OUTCOME_SUCCESS = "success"
OUTCOME_FAILURE = "failure"

# Rate-stale is raised from pricing and freshness checks, so it stays shared.
ERROR_RATES_STALE = "rates_stale"

# Shared Decimal constants prevent hidden numeric coercion in financial code.
DECIMAL_ONE = Decimal("1")
ZERO_MONEY = Decimal("0.00")

# Ledger entry direction — money flows out on debit, in on credit.
LEDGER_DIRECTION_DEBIT = "debit"
LEDGER_DIRECTION_CREDIT = "credit"

# Ledger reference types — identify the business event that produced an entry.
LEDGER_REF_EXECUTION = "execution"
LEDGER_REF_CREDIT_ADJUSTMENT = "credit_adjustment"
