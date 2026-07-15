"""Payment gateways and wallet operations."""
import asyncio
import logging
import re
from abc import ABC, abstractmethod
from typing import Any, Optional
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.constants import PaymentMethod, PaymentStatus, WalletTransactionType
from app.core.exceptions import PaymentException, ValidationException
from app.models import Payment, Wallet, WalletTransaction
from app.repositories.admin_repository import WalletRepository
from app.utils.phone import normalize_phone

logger = logging.getLogger(__name__)


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


class CashfreeGateway(PaymentGateway):
    """Cashfree PG — create order + UPI dynamic QR for ride fare collection."""

    API_VERSION = "2023-08-01"

    def _base_url(self) -> str:
        settings = get_settings()
        env = (settings.cashfree_env or "sandbox").strip().lower()
        if env in {"production", "prod", "live"}:
            return "https://api.cashfree.com/pg"
        return "https://sandbox.cashfree.com/pg"

    def _headers(self) -> dict[str, str]:
        settings = get_settings()
        if not settings.cashfree_app_id or not settings.cashfree_secret_key:
            raise ValidationException("Cashfree is not configured on the server")
        return {
            "x-client-id": settings.cashfree_app_id,
            "x-client-secret": settings.cashfree_secret_key,
            "x-api-version": self.API_VERSION,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    @staticmethod
    def _customer_phone(raw: Optional[str]) -> str:
        if not raw:
            return "9999999999"
        digits = re.sub(r"\D", "", normalize_phone(raw))
        if digits.startswith("91") and len(digits) >= 12:
            return digits[-10:]
        if len(digits) >= 10:
            return digits[-10:]
        return digits or "9999999999"

    @staticmethod
    def _order_id_for_ride(ride_id: str) -> str:
        compact = re.sub(r"[^a-zA-Z0-9]", "", ride_id)[:32] or "ride"
        return f"ride_{compact}"[:50]

    @staticmethod
    def _extract_qr_fields(pay_response: dict[str, Any]) -> tuple[Optional[str], Optional[str], Optional[str]]:
        """Return (image_content_data_uri, image_url, qr_payload_string)."""
        data = pay_response.get("data") or {}
        payload = data.get("payload")
        image_content: Optional[str] = None
        image_url: Optional[str] = None
        qr_payload: Optional[str] = None

        if isinstance(payload, dict):
            for key in ("qrcode", "qr_code", "qr", "image"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    if value.startswith("data:image") or value.startswith("iVBOR") or len(value) > 200:
                        image_content = value if value.startswith("data:") else f"data:image/png;base64,{value}"
                    elif value.startswith("upi://") or value.startswith("http"):
                        qr_payload = value
                    break
            if not qr_payload:
                for key in ("upiIntentData", "default", "url"):
                    nested = payload.get(key)
                    if isinstance(nested, str) and nested.startswith(("upi://", "http")):
                        qr_payload = nested
                        break
                    if isinstance(nested, dict):
                        for v in nested.values():
                            if isinstance(v, str) and v.startswith(("upi://", "http")):
                                qr_payload = v
                                break
        elif isinstance(payload, str) and payload.strip():
            if payload.startswith("data:image") or len(payload) > 200:
                image_content = payload if payload.startswith("data:") else f"data:image/png;base64,{payload}"
            else:
                qr_payload = payload

        url = data.get("url")
        if isinstance(url, str) and url.strip():
            if url.startswith("data:image"):
                image_content = image_content or url
            elif url.startswith(("http://", "https://")):
                image_url = url
            elif url.startswith("upi://"):
                qr_payload = qr_payload or url

        # SoftPOS-style field at top level
        qrcode = pay_response.get("qrcode")
        if isinstance(qrcode, str) and qrcode.strip():
            if qrcode.startswith("data:image") or "base64" in qrcode[:40].lower():
                image_content = image_content or qrcode
            elif qrcode.startswith("upi://"):
                qr_payload = qr_payload or qrcode

        return image_content, image_url, qr_payload

    async def create_payment(self, amount: float, currency: str, metadata: dict) -> dict:
        return await self.create_qr_payment(amount, metadata)

    async def create_qr_payment(self, amount: float, metadata: dict) -> dict:
        ride_id = str(metadata.get("ride_id") or "")
        user_id = str(metadata.get("user_id") or "guest")
        order_id = str(metadata.get("order_id") or self._order_id_for_ride(ride_id))
        amount_inr = round(max(float(amount), 1.0), 2)
        phone = self._customer_phone(metadata.get("customer_phone"))
        email = (metadata.get("customer_email") or "").strip() or f"{user_id[:8]}@wavego.passenger"
        name = (metadata.get("customer_name") or "Passenger").strip()[:100]

        create_body = {
            "order_id": order_id,
            "order_amount": amount_inr,
            "order_currency": "INR",
            "customer_details": {
                "customer_id": re.sub(r"[^a-zA-Z0-9_-]", "", user_id)[:50] or "passenger",
                "customer_phone": phone,
                "customer_email": email[:100],
                "customer_name": name,
            },
            "order_note": f"Ride fare {ride_id[:36]}",
            "order_tags": {"ride_id": ride_id[:64]},
        }

        headers = self._headers()
        base = self._base_url()

        async with httpx.AsyncClient(timeout=30.0) as client:
            order_res = await client.post(f"{base}/orders", headers=headers, json=create_body)
            if order_res.status_code >= 400:
                # Reuse existing order if already created for this ride
                detail = order_res.text
                if order_res.status_code in {409, 400} and "order_id" in detail.lower():
                    existing = await client.get(f"{base}/orders/{order_id}", headers=headers)
                    if existing.status_code >= 400:
                        raise PaymentException(f"Cashfree create order failed: {detail[:300]}")
                    order = existing.json()
                else:
                    # Retry with a unique order id
                    order_id = f"ride_{re.sub(r'[^a-zA-Z0-9]', '', ride_id)[:20]}_{uuid4().hex[:8]}"[:50]
                    create_body["order_id"] = order_id
                    order_res = await client.post(f"{base}/orders", headers=headers, json=create_body)
                    if order_res.status_code >= 400:
                        raise PaymentException(f"Cashfree create order failed: {order_res.text[:300]}")
                    order = order_res.json()
            else:
                order = order_res.json()

            payment_session_id = order.get("payment_session_id")
            order_status = str(order.get("order_status") or "").upper()
            if order_status == "PAID":
                return {
                    "status": "completed",
                    "payment_type": "upi_qr",
                    "provider": "cashfree",
                    "order_id": order_id,
                    "transaction_id": order_id,
                    "qr_code_id": order_id,
                    "payment_session_id": payment_session_id,
                    "amount": amount_inr,
                }
            if not payment_session_id:
                raise PaymentException("Cashfree did not return a payment session")

            pay_res = await client.post(
                f"{base}/orders/sessions",
                headers=headers,
                json={
                    "payment_session_id": payment_session_id,
                    "payment_method": {"upi": {"channel": "qrcode"}},
                },
            )

            image_content: Optional[str] = None
            image_url: Optional[str] = None
            short_url: Optional[str] = None
            qr_payload: Optional[str] = None
            cf_payment_id: Optional[str] = None
            payment_type = "upi_qr"

            if pay_res.status_code < 400:
                pay_data = pay_res.json()
                cf_payment_id = str(pay_data.get("cf_payment_id") or "") or None
                image_content, image_url, qr_payload = self._extract_qr_fields(pay_data)
                short_url = qr_payload if qr_payload and qr_payload.startswith("http") else None
            else:
                logger.warning(
                    "Cashfree Order Pay UPI QR failed (%s): %s — falling back to payment link",
                    pay_res.status_code,
                    pay_res.text[:400],
                )
                # Fallback: payment link (QR encodes the link URL)
                link_id = f"link_{re.sub(r'[^a-zA-Z0-9]', '', ride_id)[:16]}_{uuid4().hex[:8]}"[:50]
                link_res = await client.post(
                    f"{base}/links",
                    headers=headers,
                    json={
                        "link_id": link_id,
                        "link_amount": amount_inr,
                        "link_currency": "INR",
                        "link_purpose": f"Ride fare {ride_id[:36]}",
                        "customer_details": {
                            "customer_phone": phone,
                            "customer_email": email[:100],
                            "customer_name": name,
                        },
                        "link_notify": {"send_sms": False, "send_email": False},
                    },
                )
                if link_res.status_code >= 400:
                    raise PaymentException(
                        "Cashfree UPI QR failed. Enable S2S/Order Pay in Cashfree dashboard, "
                        f"or fix payment links: {link_res.text[:250]}"
                    )
                link = link_res.json()
                short_url = link.get("link_url") or link.get("cf_link_id")
                qr_payload = short_url
                payment_type = "payment_link"
                order_id = link.get("link_id") or link_id
                cf_payment_id = str(link.get("cf_link_id") or link_id)

            if not image_content and not image_url and not qr_payload and not short_url:
                raise PaymentException("Cashfree did not return a scannable UPI QR")

            # Prefer base64 image for display; else encode UPI/link string as QR on client
            display_payload = qr_payload or short_url
            return {
                "status": "pending",
                "payment_type": payment_type,
                "provider": "cashfree",
                "order_id": order_id,
                "transaction_id": order_id,
                "qr_code_id": order_id,
                "cf_payment_id": cf_payment_id,
                "payment_session_id": payment_session_id,
                "short_url": short_url or display_payload,
                "image_url": image_url,
                "image_content": image_content or (
                    # Raw UPI intent / link string for QrImageView on the client
                    display_payload if display_payload and not str(display_payload).startswith("data:") else None
                ),
                "amount": amount_inr,
            }

    async def verify_payment(self, transaction_id: str) -> dict:
        return await self.check_qr_payment(transaction_id)

    async def check_qr_payment(self, payment_reference: str) -> dict:
        headers = self._headers()
        base = self._base_url()

        async with httpx.AsyncClient(timeout=20.0) as client:
            # Try order status first
            order_res = await client.get(f"{base}/orders/{payment_reference}", headers=headers)
            if order_res.status_code < 400:
                order = order_res.json()
                status = str(order.get("order_status") or "").upper()
                if status == "PAID":
                    return {
                        "status": "completed",
                        "transaction_id": payment_reference,
                        "gateway_response": order,
                    }
                return {
                    "status": "pending",
                    "transaction_id": payment_reference,
                    "gateway_response": order,
                }

            # Payment link fallback
            link_res = await client.get(f"{base}/links/{payment_reference}", headers=headers)
            if link_res.status_code < 400:
                link = link_res.json()
                status = str(link.get("link_status") or "").upper()
                paid = status in {"PAID", "PARTIALLY_PAID"} or float(link.get("link_amount_paid") or 0) > 0
                if paid:
                    return {
                        "status": "completed",
                        "transaction_id": payment_reference,
                        "gateway_response": link,
                    }
                return {
                    "status": "pending",
                    "transaction_id": payment_reference,
                    "gateway_response": link,
                }

        raise PaymentException(f"Unable to check Cashfree payment status for {payment_reference}")

    async def refund_payment(self, transaction_id: str, amount: float) -> dict:
        return {"status": "refunded", "amount": amount}


class PaymentService:
    GATEWAYS = {
        PaymentMethod.CASH.value: CashGateway,
        PaymentMethod.WALLET.value: WalletGateway,
        PaymentMethod.STRIPE.value: StripeGateway,
        PaymentMethod.UPI.value: CashfreeGateway,
        PaymentMethod.CARD.value: StripeGateway,
        PaymentMethod.CASHFREE.value: CashfreeGateway,
        # Legacy aliases — all online methods use Cashfree
        PaymentMethod.RAZORPAY.value: CashfreeGateway,
        PaymentMethod.PHONEPE.value: CashfreeGateway,
    }

    ONLINE_QR_METHODS = {
        PaymentMethod.CASHFREE.value,
        PaymentMethod.UPI.value,
        PaymentMethod.RAZORPAY.value,
    }

    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def _preferred_qr_gateway() -> PaymentGateway:
        settings = get_settings()
        if not settings.cashfree_app_id or not settings.cashfree_secret_key:
            raise ValidationException(
                "Online UPI QR is not configured. Set CASHFREE_APP_ID and CASHFREE_SECRET_KEY."
            )
        return CashfreeGateway()

    @staticmethod
    def _qr_payment_method_for_gateway(gateway: PaymentGateway) -> str:
        return PaymentMethod.CASHFREE.value

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
        *,
        customer_phone: Optional[str] = None,
        customer_email: Optional[str] = None,
        customer_name: Optional[str] = None,
    ) -> Payment:
        existing = await self.get_ride_payment(ride_id)
        if existing and existing.status == PaymentStatus.COMPLETED.value:
            raise ValidationException("Payment already collected for this ride")

        if (
            existing
            and existing.status == PaymentStatus.PENDING.value
            and existing.payment_method in self.ONLINE_QR_METHODS
            and existing.gateway_response
            and (
                existing.gateway_response.get("qr_code_id")
                or existing.gateway_response.get("order_id")
                or existing.gateway_response.get("payment_link_id")
                or existing.gateway_response.get("image_content")
                or existing.gateway_response.get("short_url")
            )
        ):
            return existing

        gateway = self._preferred_qr_gateway()
        method = self._qr_payment_method_for_gateway(gateway)
        result = await gateway.create_qr_payment(
            amount,
            {
                "ride_id": str(ride_id),
                "user_id": str(user_id),
                "customer_phone": customer_phone,
                "customer_email": customer_email,
                "customer_name": customer_name,
            },
        )

        txn_id = result.get("qr_code_id") or result.get("order_id") or result.get("transaction_id")
        if existing:
            existing.payment_method = method
            existing.amount = amount
            existing.status = PaymentStatus.PENDING.value
            existing.gateway_transaction_id = txn_id
            existing.gateway_response = result
            payment = existing
        else:
            payment = Payment(
                ride_id=ride_id,
                user_id=user_id,
                amount=amount,
                payment_method=method,
                status=PaymentStatus.PENDING.value,
                gateway_transaction_id=txn_id,
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
        if payment.payment_method not in self.ONLINE_QR_METHODS:
            raise ValidationException("This ride does not have an online QR payment")

        qr_code_id = payment.gateway_transaction_id or (payment.gateway_response or {}).get(
            "qr_code_id"
        ) or (payment.gateway_response or {}).get("order_id")
        if not qr_code_id:
            raise ValidationException("QR payment reference missing")

        provider = (payment.gateway_response or {}).get("provider")
        if payment.payment_method == PaymentMethod.RAZORPAY.value and provider != "cashfree":
            # Legacy non-Cashfree online payments cannot be polled — collect again with UPI
            raise ValidationException(
                "This payment used a retired gateway. Cancel and collect again with UPI."
            )
        gateway: PaymentGateway = CashfreeGateway()

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
