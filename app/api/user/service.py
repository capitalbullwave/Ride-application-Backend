"""User module service."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, VehicleType
from app.repositories.ride_repository import RideRepository
from app.services.image_storage import resolve_vehicle_icon_url


class UserApiService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.ride_repo = RideRepository(db)

    async def home_dashboard(self, user: User) -> dict:
        vt_result = await self.db.execute(
            select(VehicleType)
            .where(VehicleType.is_active == True)
            .order_by(VehicleType.service_group, VehicleType.display_order, VehicleType.name)
        )
        vehicle_types = vt_result.scalars().all()
        recent = await self.ride_repo.get_user_rides(user.id, page=1, page_size=5)
        active = await self.ride_repo.get_active_ride_for_user(user.id)
        def _category_payload(vt: VehicleType, *, service_group: str | None = None) -> dict:
            image_url = resolve_vehicle_icon_url(vt.icon)
            return {
                "id": str(vt.id),
                "slug": vt.slug or vt.name.lower().replace(" ", "-"),
                "name": vt.name,
                "description": vt.description,
                "base_fare": vt.base_fare,
                "per_km_rate": vt.per_km_rate,
                "included_distance_km": vt.included_distance_km,
                "included_hours": vt.included_hours,
                "per_hour_rate": vt.per_hour_rate,
                "icon_url": image_url,
                "image_url": image_url,
                "service_group": service_group or vt.service_group or "ride",
                "capacity": vt.capacity,
            }

        return {
            "greeting_name": user.first_name,
            "full_name": f"{user.first_name} {user.last_name}".strip(),
            "vehicle_categories": [
                _category_payload(vt)
                for vt in vehicle_types
                if (vt.service_group or "ride") != "rental"
            ],
            "rental_categories": [
                _category_payload(vt, service_group="rental")
                for vt in vehicle_types
                if (vt.service_group or "ride") == "rental"
            ],
            "offers": [],
            "banners": [],
            "nearby_drivers_count": 0,
            "recent_rides": [
                {
                    "id": str(r.id),
                    "pickup_address": r.pickup_address,
                    "dropoff_address": r.dropoff_address,
                    "status": r.status,
                    "fare_estimate": r.estimated_fare,
                    "created_at": r.created_at.isoformat(),
                }
                for r in recent
            ],
            "active_ride": None if not active else {"id": str(active.id), "status": active.status},
        }


__all__ = ["UserApiService"]
