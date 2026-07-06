"""Resolve ride discounts from student pass and subscriptions."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.constants import KYCStatus
from app.subscriptions.models import StudentPass, SubscriptionPlan, UserSubscription


async def get_user_ride_discount_percent(db: AsyncSession, user_id: UUID) -> float:
    """Return the best active ride discount percent for a user."""
    student_discount = 0.0
    subscription_discount = 0.0

    student = await db.scalar(
        select(StudentPass).where(
            StudentPass.user_id == user_id,
            StudentPass.status == KYCStatus.APPROVED.value,
        )
    )
    if student:
        student_discount = float(student.discount_percent or 0)

    now = datetime.now(timezone.utc)
    sub = await db.scalar(
        select(UserSubscription)
        .options(selectinload(UserSubscription.plan))
        .where(
            UserSubscription.user_id == user_id,
            UserSubscription.status == "ACTIVE",
        )
    )
    if sub and sub.plan and sub.plan.is_active:
        if sub.expires_at is None or sub.expires_at > now:
            subscription_discount = float(sub.plan.ride_discount_percent or 0)

    return max(student_discount, subscription_discount)


def apply_member_discount_to_fare(fare: dict, discount_pct: float) -> dict:
    """Apply membership discount to a fare breakdown dict (mutates copy)."""
    if discount_pct <= 0:
        return fare
    original = float(fare.get("estimated_fare") or 0)
    member_discount = round(original * (discount_pct / 100), 2)
    updated = dict(fare)
    updated["original_fare"] = round(original, 2)
    updated["member_discount"] = member_discount
    updated["discount_percent"] = round(float(discount_pct), 2)
    updated["estimated_fare"] = round(max(original - member_discount, 0), 2)
    updated["promo_discount"] = round(float(updated.get("promo_discount") or 0) + member_discount, 2)
    return updated


def parse_benefits(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [str(item) for item in data]
    except json.JSONDecodeError:
        pass
    return []


def map_subscription_plan(plan: SubscriptionPlan, *, subscriber_count: int | None = None) -> dict:
    data = {
        "id": str(plan.id),
        "slug": plan.slug,
        "name": plan.name,
        "description": plan.description or "",
        "price": plan.price,
        "price_inr": plan.price,
        "price_label": f"₹{int(plan.price)}" if plan.price == int(plan.price) else f"₹{plan.price:.0f}",
        "period_label": plan.period_label,
        "benefits": parse_benefits(plan.benefits_json),
        "ride_discount_percent": plan.ride_discount_percent,
        "is_popular": plan.is_popular,
        "is_active": plan.is_active,
        "sort_order": plan.sort_order,
    }
    if subscriber_count is not None:
        data["subscriber_count"] = subscriber_count
    return data


def map_student_pass(record: StudentPass, *, mask_aadhar: bool = True) -> dict:
    aadhar = record.aadhar_number
    if mask_aadhar and len(aadhar) >= 4:
        aadhar = f"XXXX XXXX {aadhar[-4:]}"
    return {
        "id": str(record.id),
        "aadhar_number": aadhar,
        "college_name": record.college_name,
        "aadhar_photo_url": record.aadhar_photo_url,
        "student_id_photo_url": record.student_id_photo_url,
        "status": record.status.lower(),
        "discount_percent": record.discount_percent,
        "rejection_reason": record.rejection_reason,
        "verified_at": record.verified_at.isoformat() if record.verified_at else None,
        "created_at": record.created_at.isoformat() if record.created_at else None,
    }
