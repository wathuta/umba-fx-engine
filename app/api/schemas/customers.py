from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.api.schemas.base import DecimalStringModel, require_decimal_string
from app.utils.money import Currency


class CustomerResponse(BaseModel):
    customer_id: UUID


class BalanceCreditRequest(BaseModel):
    currency: Currency
    amount: Decimal = Field(gt=0)

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
