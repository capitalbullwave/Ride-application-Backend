"""Optional bootstrap helpers — vehicle types are managed only from the admin panel."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

# Vehicle categories are not seeded from code. Admins add them manually in the panel.
DEFAULT_VEHICLE_TYPES: tuple = ()


@dataclass(frozen=True)
class DefaultVehicleType:
    name: str
    slug: str
    description: str
    base_fare: float
    per_km_rate: float
    per_minute_rate: float
    capacity: int
    service_group: str = "ride"


async def ensure_default_vehicle_types(db: AsyncSession) -> int:
    """No-op — vehicle types are created only via admin."""
    _ = db
    return 0
