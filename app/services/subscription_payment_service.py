"""Razorpay checkout for paid subscription plans."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import Settings
from app.core.exceptions import NotFoundException, ValidationException
from app.models import User
from app.services.payment_service import _razorpay_client
from app.services.user_benefits_service import map_subscription_plan
from app.subscriptions.models import SubscriptionPayment, SubscriptionPlan, UserSubscription


async def _get_active_plan(db: AsyncSession, plan_slug: str) -> SubscriptionPlan:
    plan = await db.scalar(
        select(SubscriptionPlan).where(
            SubscriptionPlan.slug == plan_slug,
            SubscriptionPlan.is_active.is_(True),
        )
    )
    if not plan:
        raise NotFoundException("Subscription plan not found")
    return plan


async def activate_user_subscription(
    db: AsyncSession,
    user_id: uuid.UUID,
    plan: SubscriptionPlan,
) -> UserSubscription:
    now = datetime.now(timezone.utc)
    expires_at = None
    if plan.price > 0 or plan.period_label not in ("forever", "free"):
        expires_at = now + timedelta(days=30)

    sub = await db.scalar(select(UserSubscription).where(UserSubscription.user_id == user_id))
    if sub:
        sub.plan_id = plan.id
        sub.status = "ACTIVE"
        sub.started_at = now
        sub.expires_at = expires_at
    else:
        sub = UserSubscription(
            user_id=user_id,
            plan_id=plan.id,
            status="ACTIVE",
            started_at=now,
            expires_at=expires_at,
        )
        db.add(sub)

    await db.flush()
    await db.refresh(sub, attribute_names=["plan"])
    return sub


class SubscriptionPaymentService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_checkout(self, user: User, plan_slug: str) -> dict:
        plan = await _get_active_plan(self.db, plan_slug)
        if plan.price <= 0:
            raise ValidationException("Free plan does not require payment")

        amount_paise = max(int(round(plan.price * 100)), 100)
        receipt = f"sub_{str(user.id).replace('-', '')[:12]}_{int(datetime.now(timezone.utc).timestamp())}"[
            :40
        ]

        def _create_order() -> dict:
            client = _razorpay_client()
            return client.order.create(
                {
                    "amount": amount_paise,
                    "currency": "INR",
                    "receipt": receipt,
                    "notes": {
                        "user_id": str(user.id),
                        "plan_slug": plan.slug,
                        "plan_id": str(plan.id),
                    },
                }
            )

        order = await asyncio.to_thread(_create_order)
        order_id = order.get("id")
        if not order_id:
            raise ValidationException("Unable to create Razorpay order")

        payment = SubscriptionPayment(
            user_id=user.id,
            plan_id=plan.id,
            amount=plan.price,
            currency="INR",
            razorpay_order_id=str(order_id),
            status="PENDING",
        )
        self.db.add(payment)
        await self.db.commit()
        await self.db.refresh(payment)

        settings = Settings()
        if not settings.razorpay_key_id.strip():
            raise ValidationException("Razorpay is not configured on the server")

        return {
            "checkout": {
                "order_id": str(order_id),
                "amount": amount_paise,
                "currency": "INR",
                "key_id": settings.razorpay_key_id.strip(),
                "razorpay_key_id": settings.razorpay_key_id.strip(),
                "plan": map_subscription_plan(plan),
                "prefill": {
                    "name": f"{user.first_name} {user.last_name}".strip() or "User",
                    "email": user.email,
                    "contact": user.phone,
                },
            }
        }

    async def verify_and_activate(
        self,
        user: User,
        *,
        plan_slug: str,
        razorpay_order_id: str,
        razorpay_payment_id: str,
        razorpay_signature: str,
    ) -> dict:
        plan = await _get_active_plan(self.db, plan_slug)
        if plan.price <= 0:
            raise ValidationException("Free plan does not require payment verification")

        payment = await self.db.scalar(
            select(SubscriptionPayment)
            .options(selectinload(SubscriptionPayment.plan))
            .where(
                SubscriptionPayment.user_id == user.id,
                SubscriptionPayment.razorpay_order_id == razorpay_order_id,
                SubscriptionPayment.plan_id == plan.id,
            )
            .order_by(SubscriptionPayment.created_at.desc())
        )
        if not payment:
            raise ValidationException("Subscription payment order not found")
        if payment.status == "COMPLETED":
            sub = await self.db.scalar(
                select(UserSubscription)
                .options(selectinload(UserSubscription.plan))
                .where(UserSubscription.user_id == user.id)
            )
            if sub and sub.plan:
                return self._success_payload(sub, plan, "Subscription already active")
            raise ValidationException("Payment already processed but subscription missing")

        def _verify() -> None:
            client = _razorpay_client()
            client.utility.verify_payment_signature(
                {
                    "razorpay_order_id": razorpay_order_id,
                    "razorpay_payment_id": razorpay_payment_id,
                    "razorpay_signature": razorpay_signature,
                }
            )

        try:
            await asyncio.to_thread(_verify)
        except Exception as exc:
            payment.status = "FAILED"
            payment.gateway_response = {"error": str(exc)}
            await self.db.commit()
            raise ValidationException("Payment verification failed") from exc

        payment.status = "COMPLETED"
        payment.razorpay_payment_id = razorpay_payment_id
        payment.gateway_response = {
            "razorpay_order_id": razorpay_order_id,
            "razorpay_payment_id": razorpay_payment_id,
        }

        sub = await activate_user_subscription(self.db, user.id, plan)
        await self.db.commit()
        await self.db.refresh(sub, attribute_names=["plan"])
        return self._success_payload(sub, plan, f"{plan.name} plan activated")

    @staticmethod
    def _success_payload(sub: UserSubscription, plan: SubscriptionPlan, message: str) -> dict:
        return {
            "subscription": {
                "plan": map_subscription_plan(plan),
                "status": sub.status.lower(),
                "started_at": sub.started_at.isoformat(),
                "expires_at": sub.expires_at.isoformat() if sub.expires_at else None,
            },
            "message": message,
        }
