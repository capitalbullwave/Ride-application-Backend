import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.schemas.common import BaseSchema


class WalletResponse(BaseSchema):
    id: uuid.UUID
    balance: float
    currency: str
    is_active: bool


class WalletTopUp(BaseModel):
    amount: float = Field(..., gt=0, le=100000)
    payment_method: str = "RAZORPAY"


class WalletTransactionResponse(BaseSchema):
    id: uuid.UUID
    transaction_type: str
    amount: float
    balance_before: float
    balance_after: float
    description: str
    reference_id: Optional[str] = None
    created_at: datetime


class PaymentCreate(BaseModel):
    ride_id: uuid.UUID
    payment_method: str
    amount: Optional[float] = None


class PaymentResponse(BaseSchema):
    id: uuid.UUID
    ride_id: uuid.UUID
    amount: float
    currency: str
    payment_method: str
    status: str
    gateway_transaction_id: Optional[str] = None
    invoice_url: Optional[str] = None
    created_at: datetime


class PaymentVerify(BaseModel):
    payment_id: uuid.UUID
    gateway_transaction_id: str
    gateway_response: Optional[dict] = None
