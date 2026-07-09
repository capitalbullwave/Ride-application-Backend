"""Default platform records seeded on startup and via scripts/seed.py."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import VehicleType


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


DEFAULT_VEHICLE_TYPES: tuple[DefaultVehicleType, ...] = (
    DefaultVehicleType(
        name="Bike",
        slug="bike",
        description="Two-wheeler rides",
        base_fare=25,
        per_km_rate=8,
        per_minute_rate=1.5,
        capacity=1,
    ),
    DefaultVehicleType(
        name="Auto",
        slug="auto",
        description="Three-wheeler auto rickshaw",
        base_fare=30,
        per_km_rate=10,
        per_minute_rate=1.5,
        capacity=3,
    ),
    DefaultVehicleType(
        name="E-Rickshaw",
        slug="e-rickshaw",
        description="Electric rickshaw rides",
        base_fare=28,
        per_km_rate=9,
        per_minute_rate=1.5,
        capacity=3,
    ),
    DefaultVehicleType(
        name="Cab",
        slug="cab",
        description="Standard cab rides",
        base_fare=40,
        per_km_rate=12,
        per_minute_rate=2,
        capacity=4,
    ),
    DefaultVehicleType(
        name="Economy",
        slug="economy",
        description="Affordable everyday rides",
        base_fare=40,
        per_km_rate=12,
        per_minute_rate=2,
        capacity=4,
    ),
    DefaultVehicleType(
        name="Comfort",
        slug="comfort",
        description="Extra legroom and comfort",
        base_fare=60,
        per_km_rate=16,
        per_minute_rate=2.5,
        capacity=4,
    ),
    DefaultVehicleType(
        name="Premium",
        slug="premium",
        description="Luxury vehicles",
        base_fare=100,
        per_km_rate=25,
        per_minute_rate=3,
        capacity=4,
    ),
    DefaultVehicleType(
        name="XL",
        slug="xl",
        description="6-seater for groups",
        base_fare=80,
        per_km_rate=18,
        per_minute_rate=2.5,
        capacity=6,
    ),
)


async def ensure_default_vehicle_types(db: AsyncSession) -> int:
    """Insert any missing default vehicle types (matched by slug). Returns count added."""
    result = await db.execute(select(VehicleType.slug))
    existing_slugs = {row[0] for row in result.all()}

    added = 0
    for item in DEFAULT_VEHICLE_TYPES:
        if item.slug in existing_slugs:
            continue
        db.add(
            VehicleType(
                name=item.name,
                slug=item.slug,
                description=item.description,
                base_fare=item.base_fare,
                per_km_rate=item.per_km_rate,
                per_minute_rate=item.per_minute_rate,
                capacity=item.capacity,
                service_group=item.service_group,
                is_active=True,
            )
        )
        added += 1

    if added:
        await db.flush()
    return added
