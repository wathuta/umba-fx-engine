from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.api.schemas.base import DecimalStringModel, require_decimal_string
from app.utils.money import Currency


class QuoteRequest(BaseModel):
    customer_id: UUID
    source_currency: Currency
    destination_currency: Currency
    source_amount: Decimal = Field(gt=0)

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
