import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any
from pydantic import BaseModel, Field, field_validator # Data validator/serialization


class PaymentRequest(BaseModel):
    order_id: str = Field(..., min_length=1, max_length=100, description="Merchant order reference")
    amount: Decimal = Field(..., gt=Decimal("0.00"), max_digits=12, decimal_places=2, description="Transaction amount")
    currency: str = Field(..., min_length=3, max_length=3, description="ISO-4217 3-letter currency code")

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, v: str) -> str:
        return v.upper()


class PaymentResponse(BaseModel):
    transaction_id: uuid.UUID
    order_id: str
    client_id: str
    amount: Decimal
    currency: str
    status: str
    processed_at: datetime
    idempotent_replay: bool = False


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: Any | None = None

class ErrorResponse(BaseModel):
    error_code: str
    message: str
    detail: dict | str | None = None