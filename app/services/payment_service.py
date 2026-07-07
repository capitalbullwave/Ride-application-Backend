"""Payment gateways and wallet operations."""
import asyncio
from abc import ABC, abstractmethod
from typing import Optional
from uuid import UUID

import razorpay
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.constants import PaymentMethod, PaymentStatus, WalletTransactionType
from app.core.exceptions import PaymentException, ValidationException
from app.models import Payment, Wallet, WalletTransaction
from app.repositories.admin_repository import WalletRepository


class PaymentGateway(ABC):
    @abstractmethod
    async def create_payment(self, amount: float, currency: str, metadata: dict) -> dict:
        pass

    @abstractmethod
    async def verify_payment(self, transaction_id: str) -> dict:
        pass

    @abstractmethod
    async def refund_payment(self, transaction_id: str, amount: float) -> dict:
        pass


def _razorpay_client() -> razorpay.Client:
    settings = get_settings()
    if not settings.razorpay_key_id or not settings.razorpay_key_secret:
        raise ValidationException("Razorpay is not configured on the server")
    return razorpay.Client(auth=(settings.razorpay_key_id, settings.razorpay_key_secret))


class CashGateway(PaymentGateway):
    async def create_payment(self, amount: float, currency: str, metadata: dict) -> dict:
        return {"status": "completed", "transaction_id": f"cash_{metadata.get('ride_id')}"}

    async def verify_payment(self, transaction_id: str) -> dict:
        return {"status": "completed", "transaction_id": transaction_id}

    async def refund_payment(self, transaction_id: str, amount: float) -> dict:
        return {"status": "refunded", "amount": amount}


class WalletGateway(PaymentGateway):
    def __init__(self, db: AsyncSession):
        self.db = db
        self.wallet_repo = WalletRepository(db)

    async def create_payment(self, amount: float, currency: str, metadata: dict) -> dict:
        user_id = metadata.get("user_id")
        wallet = await self.wallet_repo.get_by_user_id(UUID(str(user_id)))
        if not wallet or wallet.balance < amount:
            raise PaymentException("Insufficient wallet balance")

        wallet.balance -= amount
        txn = WalletTransaction(
            wallet_id=wallet.id,
            transaction_type=WalletTransactionType.DEBIT.value,
            amount=amount,
            balance_before=wallet.balance + amount,
            balance_after=wallet.balance,
            description=f"Ride payment {metadata.get('ride_id')}",
            reference_id=str(metadata.get("ride_id")),
            reference_type="RIDE",
        )
        self.db.add(txn)
        await self.wallet_repo.update(wallet)
        return {"status": "completed", "transaction_id": str(txn.id)}

    async def verify_payment(self, transaction_id: str) -> dict:
        return {"status": "completed", "transaction_id": transaction_id}

    async def refund_payment(self, transaction_id: str, amount: float) -> dict:
        return {"status": "refunded", "amount": amount}


class StripeGateway(PaymentGateway):
    async def create_payment(self, amount: float, currency: str, metadata: dict) -> dict:
        return {"status": "pending", "client_secret": "stripe_client_secret", "transaction_id": "stripe_pending"}

    async def verify_payment(self, transaction_id: str) -> dict:
        return {"status": "completed", "transaction_id": transaction_id}

    async def refund_payment(self, transaction_id: str, amount: float) -> dict:
        return {"status": "refunded", "amount": amount}


class RazorpayGateway(PaymentGateway):
    async def create_payment(self, amount: float, currency: str, metadata: dict) -> dict:
        return await self.create_qr_payment(amount, metadata)

    async def create_qr_payment(self, amount: float, metadata: dict) -> dict:
        amount_paise = max(int(round(amount * 100)), 100)
        ride_id = str(metadata.get("ride_id") or "")
        settings = get_settings()

        def _create_upi_qr() -> dict:
            client = _razorpay_client()
            return client.qrcode.create(
                {
                    "type": "upi_qr",
                    "name": f"Ride {ride_id[:8] or 'fare'}",
                    "usage": "single_use",
                    "fixed_amount": True,
                    "payment_amount": amount_paise,
                    "description": "Ride fare payment",
                    "notes": {"ride_id": ride_id},
                }
            )

        def _create_payment_link() -> dict:
            client = _razorpay_client()
            return client.payment_link.create(
                {
                    "amount": amount_paise,
                    "currency": "INR",
                    "description": "Ride fare payment",
                    "notify": {"sms": False, "email": False},
                    "notes": {"ride_id": ride_id},
                }
            )

        try:
            qr = await asyncio.to_thread(_create_upi_qr)
            qr_id = qr.get("id")
            image_url = qr.get("image_url") or ""
            return {
                "status": "pending",
                "payment_type": "qr_code",
                "order_id": qr_id,
                "transaction_id": qr_id,
                "qr_code_id": qr_id,
                "short_url": image_url,
                "image_url": image_url or None,
                "image_content": qr.get("image_content"),
                "amount_paise": amount_paise,
                "key_id": settings.razorpay_key_id,
            }
        except Exception:
            link = await asyncio.to_thread(_create_payment_link)
            link_id = link.get("id")
            short_url = link.get("short_url") or ""
            return {
                "status": "pending",
                "payment_type": "payment_link",
                "order_id": link_id,
                "transaction_id": link_id,
                "payment_link_id": link_id,
                "qr_code_id": link_id,
                "short_url": short_url,
                "image_url": link.get("image_url"),
                "amount_paise": amount_paise,
                "key_id": settings.razorpay_key_id,
            }

    async def verify_payment(self, transaction_id: str) -> dict:
        return await self.check_qr_payment(transaction_id)

    async def check_qr_payment(self, payment_reference: str) -> dict:
        def _fetch() -> tuple[str, dict]:
            client = _razorpay_client()
            if payment_reference.startswith("qr_"):
                return "qr_code", client.qrcode.fetch(payment_reference)
            return "payment_link", client.payment_link.fetch(payment_reference)

        kind, data = await asyncio.to_thread(_fetch)

        if kind == "qr_code":
            amount_received = int(data.get("payments_amount_received") or 0)
            payment_amount = int(data.get("payment_amount") or 0)
            status = (data.get("status") or "").lower()
            paid = amount_received > 0 or (
                payment_amount > 0 and amount_received >= payment_amount
            ) or status == "closed"
            if paid:
                return {
                    "status": "completed",
                    "transaction_id": payment_reference,
                    "gateway_response": data,
                }
            return {
                "status": "pending",
                "transaction_id": payment_reference,
                "gateway_response": data,
            }

        status = (data.get("status") or "").lower()
        amount_paid = int(data.get("amount_paid") or 0)
        if status == "paid" or amount_paid > 0:
            return {
                "status": "completed",
                "transaction_id": payment_reference,
                "gateway_response": data,
            }
        return {
            "status": "pending",
            "transaction_id": payment_reference,
            "gateway_response": data,
        }

    async def refund_payment(self, transaction_id: str, amount: float) -> dict:
        return {"status": "refunded", "amount": amount}


class PaymentService:
    GATEWAYS = {
        PaymentMethod.CASH.value: CashGateway,
        PaymentMethod.WALLET.value: WalletGateway,
        PaymentMethod.STRIPE.value: StripeGateway,
        PaymentMethod.RAZORPAY.value: RazorpayGateway,
        PaymentMethod.UPI.value: RazorpayGateway,
        PaymentMethod.CARD.value: StripeGateway,
        PaymentMethod.CASHFREE.value: RazorpayGateway,
        PaymentMethod.PHONEPE.value: RazorpayGateway,
    }

    def __init__(self, db: AsyncSession):
        self.db = db

    def _get_gateway(self, method: str) -> PaymentGateway:
        gateway_class = self.GATEWAYS.get(method)
        if not gateway_class:
            raise ValidationException(f"Unsupported payment method: {method}")
        if gateway_class == WalletGateway:
            return gateway_class(self.db)
        return gateway_class()

    async def get_ride_payment(self, ride_id: UUID) -> Optional[Payment]:
        result = await self.db.execute(select(Payment).where(Payment.ride_id == ride_id))
        return result.scalar_one_or_none()

    async def process_payment(
        self,
        ride_id: UUID,
        user_id: UUID,
        amount: float,
        payment_method: str,
    ) -> Payment:
        existing = await self.get_ride_payment(ride_id)
        if existing and existing.status == PaymentStatus.COMPLETED.value:
            return existing

        gateway = self._get_gateway(payment_method)
        result = await gateway.create_payment(
            amount, "INR", {"ride_id": str(ride_id), "user_id": str(user_id)}
        )

        if existing:
            existing.payment_method = payment_method
            existing.amount = amount
            existing.status = (
                PaymentStatus.COMPLETED.value
                if result["status"] == "completed"
                else PaymentStatus.PENDING.value
            )
            existing.gateway_transaction_id = result.get("transaction_id")
            existing.gateway_response = result
            payment = existing
        else:
            payment = Payment(
                ride_id=ride_id,
                user_id=user_id,
                amount=amount,
                payment_method=payment_method,
                status=(
                    PaymentStatus.COMPLETED.value
                    if result["status"] == "completed"
                    else PaymentStatus.PENDING.value
                ),
                gateway_transaction_id=result.get("transaction_id"),
                gateway_response=result,
            )
            self.db.add(payment)

        await self.db.flush()
        await self.db.refresh(payment)
        return payment

    async def create_ride_qr_payment(
        self,
        ride_id: UUID,
        user_id: UUID,
        amount: float,
    ) -> Payment:
        existing = await self.get_ride_payment(ride_id)
        if existing and existing.status == PaymentStatus.COMPLETED.value:
            raise ValidationException("Payment already collected for this ride")

        if (
            existing
            and existing.status == PaymentStatus.PENDING.value
            and existing.payment_method == PaymentMethod.RAZORPAY.value
            and existing.gateway_response
            and (
                existing.gateway_response.get("qr_code_id")
                or existing.gateway_response.get("payment_link_id")
            )
        ):
            return existing

        gateway = RazorpayGateway()
        result = await gateway.create_qr_payment(amount, {"ride_id": str(ride_id), "user_id": str(user_id)})

        if existing:
            existing.payment_method = PaymentMethod.RAZORPAY.value
            existing.amount = amount
            existing.status = PaymentStatus.PENDING.value
            existing.gateway_transaction_id = result.get("qr_code_id")
            existing.gateway_response = result
            payment = existing
        else:
            payment = Payment(
                ride_id=ride_id,
                user_id=user_id,
                amount=amount,
                payment_method=PaymentMethod.RAZORPAY.value,
                status=PaymentStatus.PENDING.value,
                gateway_transaction_id=result.get("qr_code_id"),
                gateway_response=result,
            )
            self.db.add(payment)

        await self.db.flush()
        await self.db.refresh(payment)
        return payment

    async def refresh_ride_qr_payment(self, ride_id: UUID) -> Payment:
        payment = await self.get_ride_payment(ride_id)
        if not payment:
            raise ValidationException("No online payment started for this ride")
        if payment.status == PaymentStatus.COMPLETED.value:
            return payment
        if payment.payment_method not in {PaymentMethod.RAZORPAY.value, PaymentMethod.UPI.value}:
            raise ValidationException("This ride does not have an online QR payment")

        qr_code_id = payment.gateway_transaction_id or (payment.gateway_response or {}).get("qr_code_id")
        if not qr_code_id:
            raise ValidationException("QR payment reference missing")

        gateway = RazorpayGateway()
        result = await gateway.check_qr_payment(str(qr_code_id))
        payment.gateway_response = {**(payment.gateway_response or {}), **result}
        if result.get("status") == "completed":
            payment.status = PaymentStatus.COMPLETED.value
        await self.db.flush()
        await self.db.refresh(payment)
        return payment


class WalletService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.wallet_repo = WalletRepository(db)

    async def get_or_create_wallet(self, user_id: Optional[UUID] = None, driver_id: Optional[UUID] = None) -> Wallet:
        if user_id:
            wallet = await self.wallet_repo.get_by_user_id(user_id)
            if not wallet:
                wallet = Wallet(user_id=user_id, balance=0.0)
                await self.wallet_repo.create(wallet)
            return wallet
        if driver_id:
            wallet = await self.wallet_repo.get_by_driver_id(driver_id)
            if not wallet:
                wallet = Wallet(driver_id=driver_id, balance=0.0)
                await self.wallet_repo.create(wallet)
            return wallet
        raise ValidationException("User or driver ID required")

    async def credit(
        self,
        wallet_id: UUID,
        amount: float,
        description: str,
        reference_id: Optional[str] = None,
        reference_type: Optional[str] = None,
    ) -> WalletTransaction:
        wallet = await self.wallet_repo.get_by_id(wallet_id)
        if not wallet:
            raise ValidationException("Wallet not found")

        balance_before = wallet.balance
        wallet.balance += amount
        txn = WalletTransaction(
            wallet_id=wallet_id,
            transaction_type=WalletTransactionType.CREDIT.value,
            amount=amount,
            balance_before=balance_before,
            balance_after=wallet.balance,
            description=description,
            reference_id=reference_id,
            reference_type=reference_type,
        )
        self.db.add(txn)
        await self.wallet_repo.update(wallet)
        await self.db.flush()
        await self.db.refresh(txn)
        return txn
