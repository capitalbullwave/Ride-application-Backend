"""Cashfree checkout for wallet top-ups."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationException
from app.models import User
from app.services.cashfree_client import (
    checkout_payload,
    create_order,
    ensure_order_paid,
    make_order_id,
)
from app.services.payment_service import WalletService
from app.wallet.models import WalletTopUpPayment


class WalletPaymentService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_checkout(self, user: User, amount: float) -> dict:
        if amount <= 0:
            raise ValidationException("Amount must be greater than zero")
        if amount > 100_000:
            raise ValidationException("Maximum top-up amount is ₹1,00,000")

        order_id = make_order_id("wal", str(user.id))
        order = await create_order(
            order_id=order_id,
            amount=amount,
            customer_id=str(user.id),
            customer_phone_value=user.phone,
            customer_email=user.email or "",
            customer_name=f"{user.first_name} {user.last_name}".strip() or "User",
            order_note="Wallet top-up",
            order_tags={"user_id": str(user.id), "purpose": "wallet_topup"},
        )

        payment = WalletTopUpPayment(
            user_id=user.id,
            amount=amount,
            currency="INR",
            razorpay_order_id=order_id,  # stores Cashfree order_id
            status="PENDING",
            gateway_response={
                "provider": "cashfree",
                "payment_session_id": order.get("payment_session_id"),
            },
        )
        self.db.add(payment)
        await self.db.commit()
        await self.db.refresh(payment)

        return {
            "checkout": checkout_payload(
                order_id=order_id,
                payment_session_id=str(order["payment_session_id"]),
                amount_inr=amount,
                description=f"Wallet top-up ₹{int(amount) if amount == int(amount) else amount}",
                prefill={
                    "name": f"{user.first_name} {user.last_name}".strip() or "User",
                    "email": user.email,
                    "contact": user.phone,
                },
            )
        }

    async def verify_and_credit(self, user: User, *, order_id: str) -> dict:
        payment = await self.db.scalar(
            select(WalletTopUpPayment)
            .where(
                WalletTopUpPayment.user_id == user.id,
                WalletTopUpPayment.razorpay_order_id == order_id,
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

        try:
            order = await ensure_order_paid(order_id)
        except Exception as exc:
            payment.status = "FAILED"
            payment.gateway_response = {
                **(payment.gateway_response or {}),
                "error": str(exc),
            }
            await self.db.commit()
            raise ValidationException("Payment verification failed") from exc

        cf_payment_id = str(
            order.get("cf_order_id")
            or (order.get("order_meta") or {}).get("payment_id")
            or order_id
        )

        txn = await wallet_service.credit(
            wallet.id,
            payment.amount,
            "Wallet top-up via Cashfree",
            reference_id=cf_payment_id,
            reference_type="WALLET_TOPUP",
        )

        payment.status = "COMPLETED"
        payment.razorpay_payment_id = cf_payment_id
        payment.wallet_transaction_id = txn.id
        payment.gateway_response = {
            "provider": "cashfree",
            "order_id": order_id,
            "order": order,
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
