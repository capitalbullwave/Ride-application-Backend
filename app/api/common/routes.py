"""Shared APIs — /api/v1/common/*"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.models import AppSetting, VehicleType

router = APIRouter(tags=["Common"])

DEFAULT_CITIES = [
    {"id": "delhi", "name": "Delhi", "country": "India"},
    {"id": "mumbai", "name": "Mumbai", "country": "India"},
    {"id": "bangalore", "name": "Bangalore", "country": "India"},
]


def _serialize_vehicle_type(vt: VehicleType) -> dict:
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
        "per_minute_rate": vt.per_minute_rate,
        "waiting_charge_per_min": vt.waiting_charge_per_min,
        "icon_url": vt.icon,
        "service_group": vt.service_group or "ride",
        "capacity": vt.capacity,
    }


@router.get("/vehicle-types")
async def vehicle_types(
    db: AsyncSession = Depends(get_db),
    service_group: str | None = Query(None, description="Filter: ride, rental, etc."),
):
    query = select(VehicleType).where(VehicleType.is_active == True)
    if service_group:
        query = query.where(VehicleType.service_group == service_group.strip().lower())
    result = await db.execute(query.order_by(VehicleType.name))
    return [_serialize_vehicle_type(vt) for vt in result.scalars().all()]


@router.get("/rental-categories")
async def rental_categories(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(VehicleType)
        .where(VehicleType.is_active == True, VehicleType.service_group == "rental")
        .order_by(VehicleType.name)
    )
    return [_serialize_vehicle_type(vt) for vt in result.scalars().all()]


@router.get("/cities")
async def cities():
    return DEFAULT_CITIES


@router.get("/app-settings")
async def app_settings(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AppSetting).where(AppSetting.is_public == True))
    settings = {row.key: row.value for row in result.scalars().all()}
    return {
        "app_name": settings.get("app_name", "Bull Wave Rides"),
        "contact_email": settings.get("contact_email", "support@ridebook.com"),
        "contact_phone": settings.get("contact_phone", "+91 98765 43210"),
    }


@router.get("/pricing")
async def pricing(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(VehicleType).where(VehicleType.is_active == True))
    return [
        {
            "vehicle_type_id": str(vt.id),
            "name": vt.name,
            "base_fare": vt.base_fare,
            "per_km_rate": vt.per_km_rate,
            "waiting_charge_per_min": vt.waiting_charge_per_min,
        }
        for vt in result.scalars().all()
    ]


@router.get("/banners", include_in_schema=False)
async def banners():
    return []


@router.get("/support/faqs")
async def faqs(db: AsyncSession = Depends(get_db)):
    from app.models import Faq

    result = await db.execute(
        select(Faq).where(Faq.is_active.is_(True)).order_by(Faq.sort_order, Faq.category)
    )
    items = result.scalars().all()
    if not items:
        fallback = [
            {"id": "1", "category": "Rides", "question": "How do I book a ride?", "answer": "Enter pickup and drop locations and confirm."},
            {"id": "2", "category": "Payments", "question": "What payment methods are supported?", "answer": "Cash, wallet, UPI, and card."},
        ]
        return {"data": fallback}
    return {
        "data": [
            {
                "id": str(f.id),
                "category": f.category,
                "question": f.question,
                "answer": f.answer,
            }
            for f in items
        ]
    }
