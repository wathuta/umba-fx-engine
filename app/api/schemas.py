from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from app.core.money import Currency, decimal_to_str


class DecimalStringModel(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    @field_serializer("*", check_fields=False)
    def serialize_decimal(self, value):
        if isinstance(value, Decimal):
            return decimal_to_str(value)
        return value


def require_decimal_string(value: object) -> object:
    if not isinstance(value, str):
        raise ValueError("amount must be a decimal string")
    return value


class CustomerResponse(BaseModel):
    customer_id: UUID


class BalanceCreditRequest(BaseModel):
    currency: Currency
    amount: Decimal = Field(gt=Decimal("0"))

    @field_validator("amount", mode="before")
    @classmethod
    def amount_must_be_string(cls, value: object) -> object:
        return require_decimal_string(value)


class BalanceResponse(DecimalStringModel):
    currency: Currency
    amount: Decimal


class BalancesResponse(DecimalStringModel):
    customer_id: UUID
    balances: dict[Currency, Decimal]


class QuoteRequest(BaseModel):
    customer_id: UUID
    source_currency: Currency
    destination_currency: Currency
    source_amount: Decimal = Field(gt=Decimal("0"))

    @field_validator("source_amount", mode="before")
    @classmethod
    def source_amount_must_be_string(cls, value: object) -> object:
        return require_decimal_string(value)


class QuoteResponse(DecimalStringModel):
    quote_id: UUID
    source_currency: Currency
    destination_currency: Currency
    source_amount: Decimal
    destination_amount: Decimal
    executable_rate: Decimal
    route: list[Currency]
    expires_at: str


class ExecutionRequest(BaseModel):
    quote_id: UUID


class LegResponse(DecimalStringModel):
    currency: Currency
    amount: Decimal


class ExecutionResponse(DecimalStringModel):
    execution_id: UUID
    quote_id: UUID
    debit: LegResponse
    credit: LegResponse
    balances: dict[Currency, Decimal]


class RateRefreshResponse(BaseModel):
    rate_refresh_id: UUID
    status: str
    fetched_at: str
    pairs_updated: int


class HealthResponse(BaseModel):
    status: str
    database: str
    rates: str


class ReadinessResponse(HealthResponse):
    pass
