from decimal import Decimal

from pydantic import BaseModel, ConfigDict, field_serializer

from app.utils.money import decimal_to_str


class DecimalStringModel(BaseModel):
    # Base for all response schemas — serializes Decimal fields as strings so
    # JSON clients receive exact values without floating-point representation loss.
    model_config = ConfigDict(use_enum_values=True)

    @field_serializer("*", check_fields=False)
    def serialize_decimal(self, value):
        # Applies to every field; non-Decimal values pass through unchanged.
        if isinstance(value, Decimal):
            return decimal_to_str(value)
        return value


def require_decimal_string(value: object) -> object:
    if not isinstance(value, str):
        raise ValueError("amount must be a decimal string")
    return value
