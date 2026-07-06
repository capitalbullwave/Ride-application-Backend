import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from app.schemas.common import BaseSchema


class RideEstimateRequest(BaseModel):
    pickup_lat: float = Field(..., ge=-90, le=90)
    pickup_lng: float = Field(..., ge=-180, le=180)
    dropoff_lat: float = Field(..., ge=-90, le=90)
    dropoff_lng: float = Field(..., ge=-180, le=180)
    vehicle_type_id: Optional[uuid.UUID] = None


class RideEstimateResponse(BaseModel):
    distance_km: float
    duration_min: float
    vehicle_types: List["VehicleTypeEstimate"]


class VehicleTypeEstimate(BaseModel):
    vehicle_type_id: uuid.UUID
    name: str
    estimated_fare: float
    base_fare: float
    distance_fare: float
    time_fare: float
    tax_amount: float
    platform_fee: float


class RideCreate(BaseModel):
    pickup_address: str
    pickup_lat: float = Field(..., ge=-90, le=90)
    pickup_lng: float = Field(..., ge=-180, le=180)
    dropoff_address: str
    dropoff_lat: float = Field(..., ge=-90, le=90)
    dropoff_lng: float = Field(..., ge=-180, le=180)
    vehicle_type_id: uuid.UUID
    payment_method: str = "CASH"
    promo_code: Optional[str] = None
    scheduled_at: Optional[datetime] = None


class RideCancel(BaseModel):
    reason: str = Field(..., min_length=3, max_length=500)


class RideOTPVerify(BaseModel):
    otp: str = Field(..., min_length=4, max_length=6)


class RideResponse(BaseSchema):
    id: uuid.UUID
    user_id: uuid.UUID
    driver_id: Optional[uuid.UUID] = None
    vehicle_id: Optional[uuid.UUID] = None
    vehicle_type_id: uuid.UUID
    status: str
    pickup_address: str
    pickup_lat: float
    pickup_lng: float
    dropoff_address: str
    dropoff_lat: float
    dropoff_lng: float
    estimated_distance_km: float
    estimated_duration_min: float
    estimated_fare: float
    final_fare: Optional[float] = None
    payment_method: str
    created_at: datetime
    accepted_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class RideDetailResponse(RideResponse):
    base_fare: float = 0.0
    distance_fare: float = 0.0
    time_fare: float = 0.0
    waiting_charges: float = 0.0
    night_charges: float = 0.0
    peak_charges: float = 0.0
    tax_amount: float = 0.0
    platform_fee: float = 0.0
    promo_discount: float = 0.0
    wallet_deduction: float = 0.0
    route_polyline: Optional[str] = None
    driver: Optional[dict] = None
    vehicle: Optional[dict] = None
    rating: Optional[dict] = None


class RideTrackingPoint(BaseSchema):
    lat: float
    lng: float
    speed: Optional[float] = None
    heading: Optional[float] = None
    status: str
    created_at: datetime
