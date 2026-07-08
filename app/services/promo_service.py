"""Promo code validation and discount calculation."""
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationException
from app.coupons.models import PromoCode


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_discount_type(value: str) -> str:
    return (value or "").strip().upper()


def calculate_promo_discount(promo: PromoCode, order_amount: float) -> float:
    if order_amount < float(promo.min_order_amount or 0):
        raise ValidationException(
            f"Minimum order amount is ₹{int(promo.min_order_amount)} for this coupon"
        )

    discount_type = _normalize_discount_type(promo.discount_type)
    if discount_type == "PERCENTAGE":
        discount = order_amount * (float(promo.discount_value) / 100.0)
        if promo.max_discount is not None:
            discount = min(discount, float(promo.max_discount))
    else:
        discount = float(promo.discount_value)

    return round(min(discount, order_amount), 2)


async def get_active_promo_codes(db: AsyncSession) -> list[PromoCode]:
    now = _utc_now()
    result = await db.execute(
        select(PromoCode)
        .where(PromoCode.is_active.is_(True))
        .order_by(PromoCode.created_at.desc())
    )
    promos = list(result.scalars().all())
    active: list[PromoCode] = []
    for promo in promos:
        valid_from = promo.valid_from
        valid_until = promo.valid_until
        if valid_from.tzinfo is None:
            valid_from = valid_from.replace(tzinfo=timezone.utc)
        if valid_until.tzinfo is None:
            valid_until = valid_until.replace(tzinfo=timezone.utc)
        if valid_from <= now <= valid_until and promo.used_count < promo.max_uses:
            active.append(promo)
    return active


async def resolve_promo_code(
    db: AsyncSession,
    code: str,
    *,
    order_amount: float,
) -> tuple[PromoCode, float]:
    normalized = (code or "").strip().upper()
    if not normalized:
        raise ValidationException("Enter a coupon code")

    result = await db.execute(select(PromoCode).where(PromoCode.code == normalized))
    promo = result.scalar_one_or_none()
    if not promo:
        raise ValidationException("Invalid coupon code")

    now = _utc_now()
    if not promo.is_active:
        raise ValidationException("This coupon is no longer active")
    valid_from = promo.valid_from
    valid_until = promo.valid_until
    if valid_from.tzinfo is None:
        valid_from = valid_from.replace(tzinfo=timezone.utc)
    if valid_until.tzinfo is None:
        valid_until = valid_until.replace(tzinfo=timezone.utc)
    if now < valid_from:
        raise ValidationException("This coupon is not active yet")
    if now > valid_until:
        raise ValidationException("This coupon has expired")
    if promo.used_count >= promo.max_uses:
        raise ValidationException("This coupon has reached its usage limit")

    discount = calculate_promo_discount(promo, order_amount)
    if discount <= 0:
        raise ValidationException("Coupon cannot be applied to this fare")

    return promo, discount


def serialize_user_coupon(promo: PromoCode) -> dict:
    discount_type = "percentage" if _normalize_discount_type(promo.discount_type) == "PERCENTAGE" else "flat"
    title = promo.description or (
        f"{int(promo.discount_value)}% off"
        if discount_type == "percentage"
        else f"₹{int(promo.discount_value)} off"
    )
    return {
        "id": str(promo.id),
        "code": promo.code,
        "title": title,
        "description": promo.description,
        "discount_type": discount_type,
        "discount_value": promo.discount_value,
        "max_discount": promo.max_discount,
        "min_order_amount": promo.min_order_amount,
        "valid_until": promo.valid_until.isoformat(),
    }
