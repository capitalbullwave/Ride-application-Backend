"""Ride API schemas (Pydantic V2)."""
import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from app.schemas.common import BaseSchema


class RideEstimateRequest(BaseModel):
    pickup_lat: float = Field(..., ge=-90, le=90, description="Pickup latitude")
    pickup_lng: float = Field(..., ge=-180, le=180, description="Pickup longitude")
    dropoff_lat: float = Field(..., ge=-90, le=90, description="Dropoff latitude")
    dropoff_lng: float = Field(..., ge=-180, le=180, description="Dropoff longitude")
    vehicle_type_id: Optional[uuid.UUID] = Field(None, description="Filter estimate to one vehicle type")
    service_group: Optional[str] = Field(default="ride", description="ride or rental")
    rental_hours: Optional[float] = Field(default=None, ge=0)
    distance_km: Optional[float] = Field(
        default=None,
        ge=0,
        description="Route distance from maps (km). When set, used instead of straight-line geodesic.",
    )
    duration_min: Optional[float] = Field(
        default=None,
        ge=0,
        description="Route duration from maps (minutes). Optional when distance_km is provided.",
    )


class VehicleTypeEstimate(BaseModel):
    vehicle_type_id: uuid.UUID
    name: str
    estimated_fare: float
    original_fare: Optional[float] = None
    member_discount: float = 0.0
    discount_percent: float = 0.0
    base_fare: float
    distance_fare: float
    time_fare: float
    night_charges: float = 0.0
    peak_charges: float = 0.0
    tax_amount: float
    platform_fee: float


class RideEstimateResponse(BaseModel):
    distance_km: float
    duration_min: float
    vehicle_types: List[VehicleTypeEstimate]
    discount_percent: Optional[float] = None


class RideBookRequest(BaseModel):
    pickup_address: str = Field(..., min_length=3, max_length=500)
    pickup_lat: float = Field(..., ge=-90, le=90)
    pickup_lng: float = Field(..., ge=-180, le=180)
    dropoff_address: str = Field(..., min_length=3, max_length=500)
    dropoff_lat: float = Field(..., ge=-90, le=90)
    dropoff_lng: float = Field(..., ge=-180, le=180)
    vehicle_type_id: uuid.UUID
    payment_method: str = Field(default="CASH", pattern="^(CASH|WALLET|UPI|CARD)$")
    promo_code: Optional[str] = None
    scheduled_at: Optional[datetime] = None
    rental_hours: Optional[float] = Field(default=None, ge=0)
    distance_km: Optional[float] = Field(
        default=None,
        ge=0,
        description="Route distance from maps (km). When set, used instead of straight-line geodesic.",
    )
    duration_min: Optional[float] = Field(
        default=None,
        ge=0,
        description="Route duration from maps (minutes). Optional when distance_km is provided.",
    )


class RideCancelRequest(BaseModel):
    reason: str = Field(..., min_length=3, max_length=500)


class RideOtpVerifyRequest(BaseModel):
    otp: str = Field(..., min_length=4, max_length=6)


class DriverAcceptRequest(BaseModel):
    vehicle_id: uuid.UUID


class RideTimelineEvent(BaseSchema):
    event_type: str
    actor_type: str
    actor_id: Optional[uuid.UUID] = None
    created_at: datetime
    metadata: Optional[dict] = None


class RideResponse(BaseSchema):
    id: uuid.UUID
    public_id: str
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
    driver_commission_percentage: Optional[float] = None
    driver_earning: Optional[float] = None
    company_earning: Optional[float] = None
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
    timeline: List[RideTimelineEvent] = Field(default_factory=list)
    driver: Optional[dict] = None
    vehicle: Optional[dict] = None
