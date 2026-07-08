"""Razorpay checkout for wallet top-ups."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import ValidationException
from app.models import User
from app.services.payment_service import WalletService, _razorpay_client
from app.wallet.models import WalletTopUpPayment


class WalletPaymentService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_checkout(self, user: User, amount: float) -> dict:
        if amount <= 0:
            raise ValidationException("Amount must be greater than zero")
        if amount > 100_000:
            raise ValidationException("Maximum top-up amount is ₹1,00,000")

        amount_paise = max(int(round(amount * 100)), 100)
        receipt = f"wal_{str(user.id).replace('-', '')[:12]}_{int(datetime.now(timezone.utc).timestamp())}"[
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
                        "purpose": "wallet_topup",
                        "amount_inr": str(amount),
                    },
                }
            )

        order = await asyncio.to_thread(_create_order)
        order_id = order.get("id")
        if not order_id:
            raise ValidationException("Unable to create Razorpay order")

        payment = WalletTopUpPayment(
            user_id=user.id,
            amount=amount,
            currency="INR",
            razorpay_order_id=str(order_id),
            status="PENDING",
        )
        self.db.add(payment)
        await self.db.commit()
        await self.db.refresh(payment)

        settings = get_settings()
        if not settings.razorpay_key_id.strip():
            raise ValidationException("Razorpay is not configured on the server")

        return {
            "checkout": {
                "order_id": str(order_id),
                "amount": amount_paise,
                "currency": "INR",
                "key_id": settings.razorpay_key_id.strip(),
                "razorpay_key_id": settings.razorpay_key_id.strip(),
                "description": f"Wallet top-up ₹{int(amount) if amount == int(amount) else amount}",
                "prefill": {
                    "name": f"{user.first_name} {user.last_name}".strip() or "User",
                    "email": user.email,
                    "contact": user.phone,
                },
            }
        }

    async def verify_and_credit(
        self,
        user: User,
        *,
        razorpay_order_id: str,
        razorpay_payment_id: str,
        razorpay_signature: str,
    ) -> dict:
        payment = await self.db.scalar(
            select(WalletTopUpPayment)
            .where(
                WalletTopUpPayment.user_id == user.id,
                WalletTopUpPayment.razorpay_order_id == razorpay_order_id,
            )
            .order_by(WalletTopUpPayment.created_at.desc())
        )
        if not payment:
            raise ValidationException("Wallet payment order not found")

        wallet_service = WalletService(self.db)
        wallet = await wallet_service.get_or_create_wallet(user_id=user.id)

        if payment.status == "COMPLETED":
            txn_payload = None
            if payment.wallet_transaction_id:
                from app.models import WalletTransaction

                txn = await self.db.get(WalletTransaction, payment.wallet_transaction_id)
                if txn:
                    txn_payload = {
                        "id": str(txn.id),
                        "transaction_type": txn.transaction_type,
                        "amount": txn.amount,
                        "description": txn.description,
                        "created_at": txn.created_at.isoformat(),
                    }
            return {
                "balance": wallet.balance,
                "transaction": txn_payload,
                "message": "Wallet already credited for this payment",
            }

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

        txn = await wallet_service.credit(
            wallet.id,
            payment.amount,
            "Wallet top-up via Razorpay",
            reference_id=razorpay_payment_id,
            reference_type="WALLET_TOPUP",
        )

        payment.status = "COMPLETED"
        payment.razorpay_payment_id = razorpay_payment_id
        payment.wallet_transaction_id = txn.id
        payment.gateway_response = {
            "razorpay_order_id": razorpay_order_id,
            "razorpay_payment_id": razorpay_payment_id,
        }

        await self.db.commit()
        await self.db.refresh(wallet)
        await self.db.refresh(txn)

        try:
            from app.notifications.service import NotificationService

            amount = float(payment.amount)
            await NotificationService(self.db).notify_and_push(
                title="Wallet Credited",
                message=f"₹{amount:.0f} has been added to your wallet.",
                notification_type="WALLET",
                user_id=user.id,
                event="wallet_credit",
                data={
                    "amount": amount,
                    "balance": float(wallet.balance),
                    "screen": "wallet",
                },
                channel_id="wallet",
            )
            await self.db.commit()
        except Exception:
            pass

        return {
            "balance": wallet.balance,
            "transaction": {
                "id": str(txn.id),
                "transaction_type": txn.transaction_type,
                "amount": txn.amount,
                "description": txn.description,
                "created_at": txn.created_at.isoformat(),
            },
            "message": f"₹{payment.amount:g} added to your wallet",
        }
