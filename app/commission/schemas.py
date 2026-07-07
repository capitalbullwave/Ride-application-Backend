"""Commission and wallet API schemas."""
import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class CommissionSettingsResponse(BaseModel):
    id: uuid.UUID
    driver_commission_percentage: float
    is_active: bool
    updated_by: Optional[uuid.UUID] = None
    updated_by_name: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class CommissionSettingsUpdate(BaseModel):
    driver_commission_percentage: float = Field(..., ge=0, le=100)


class VehicleCommissionItem(BaseModel):
    vehicle_type_id: uuid.UUID
    name: str
    slug: str
    service_group: str
    driver_commission_percentage: float
    is_active: bool


class VehicleCommissionSettingsResponse(BaseModel):
    default_commission_percentage: float
    updated_at: Optional[datetime] = None
    updated_by_name: Optional[str] = None
    vehicles: List[VehicleCommissionItem] = Field(default_factory=list)


class VehicleCommissionUpdateItem(BaseModel):
    vehicle_type_id: uuid.UUID
    driver_commission_percentage: float = Field(..., ge=0, le=100)


class VehicleCommissionSettingsUpdate(BaseModel):
    default_commission_percentage: Optional[float] = Field(None, ge=0, le=100)
    vehicles: List[VehicleCommissionUpdateItem] = Field(default_factory=list)


class DriverWalletResponse(BaseModel):
    available_balance: float
    pending_balance: float
    lifetime_earnings: float


class DriverWalletTransactionResponse(BaseModel):
    id: uuid.UUID
    type: str
    amount: float
    description: str
    balance_after_transaction: float
    ride_id: Optional[uuid.UUID] = None
    created_at: datetime


class DriverEarningsRideItem(BaseModel):
    ride_id: uuid.UUID
    ride_fare: float
    driver_commission_percentage: float
    driver_earning: float
    ride_date: Optional[datetime] = None
    status: str


class DriverEarningsResponse(BaseModel):
    period: str
    total_rides: int
    total_earnings: float
    net_earnings: float
    rides: List[DriverEarningsRideItem] = Field(default_factory=list)
