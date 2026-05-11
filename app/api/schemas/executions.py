from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel

from app.api.schemas.base import DecimalStringModel
from app.utils.money import Currency


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
