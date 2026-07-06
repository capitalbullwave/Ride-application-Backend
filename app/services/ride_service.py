"""Backward-compatible re-exports — prefer app.rides.service."""
from app.rides.schemas import RideBookRequest as RideCreate
from app.rides.schemas import RideCancelRequest as RideCancel
from app.rides.schemas import RideEstimateRequest, RideEstimateResponse, RideResponse
from app.rides.schemas import RideDetailResponse, VehicleTypeEstimate
from app.rides.service import FareEngine as PricingService
from app.rides.service import RideService

__all__ = [
    "PricingService",
    "RideService",
    "RideCreate",
    "RideCancel",
    "RideEstimateRequest",
    "RideEstimateResponse",
    "RideResponse",
    "RideDetailResponse",
    "VehicleTypeEstimate",
]
