"""Shared Cashfree Payment Gateway HTTP helpers."""
from __future__ import annotations

import re
from typing import Any, Optional
from uuid import uuid4

import httpx

from app.core.config import get_settings
from app.core.exceptions import PaymentException, ValidationException
from app.utils.phone import normalize_phone

API_VERSION = "2023-08-01"


def cashfree_environment() -> str:
    settings = get_settings()
    env = (settings.cashfree_env or "sandbox").strip().lower()
    if env in {"production", "prod", "live"}:
        return "production"
    return "sandbox"


def cashfree_base_url() -> str:
    if cashfree_environment() == "production":
        return "https://api.cashfree.com/pg"
    return "https://sandbox.cashfree.com/pg"


def cashfree_headers() -> dict[str, str]:
    settings = get_settings()
    if not settings.cashfree_app_id or not settings.cashfree_secret_key:
        raise ValidationException("Cashfree is not configured on the server")
    return {
        "x-client-id": settings.cashfree_app_id,
        "x-client-secret": settings.cashfree_secret_key,
        "x-api-version": API_VERSION,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def customer_phone(raw: Optional[str]) -> str:
    if not raw:
        return "9999999999"
    digits = re.sub(r"\D", "", normalize_phone(raw))
    if digits.startswith("91") and len(digits) >= 12:
        return digits[-10:]
    if len(digits) >= 10:
        return digits[-10:]
    return digits or "9999999999"


def make_order_id(prefix: str, seed: str = "") -> str:
    compact = re.sub(r"[^a-zA-Z0-9]", "", f"{seed}{uuid4().hex}")[:28] or uuid4().hex[:12]
    return f"{prefix}_{compact}"[:50]


async def create_order(
    *,
    order_id: str,
    amount: float,
    customer_id: str,
    customer_phone_value: str,
    customer_email: str = "",
    customer_name: str = "Customer",
    order_note: str = "",
    order_tags: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "order_id": order_id,
        "order_amount": round(max(float(amount), 1.0), 2),
        "order_currency": "INR",
        "customer_details": {
            "customer_id": re.sub(r"[^a-zA-Z0-9_-]", "", customer_id)[:50] or "customer",
            "customer_phone": customer_phone(customer_phone_value),
            "customer_email": (customer_email or f"{customer_id[:8]}@wavego.app")[:100],
            "customer_name": (customer_name or "Customer").strip()[:100],
        },
    }
    if order_note:
        body["order_note"] = order_note[:200]
    if order_tags:
        body["order_tags"] = {str(k)[:64]: str(v)[:256] for k, v in order_tags.items()}

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{cashfree_base_url()}/orders",
            headers=cashfree_headers(),
            json=body,
        )
        if response.status_code >= 400:
            raise PaymentException(f"Cashfree create order failed: {response.text[:300]}")
        data = response.json()

    if not data.get("payment_session_id"):
        raise PaymentException("Cashfree did not return a payment session")
    return data


async def get_order(order_id: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(
            f"{cashfree_base_url()}/orders/{order_id}",
            headers=cashfree_headers(),
        )
        if response.status_code >= 400:
            raise PaymentException(f"Cashfree get order failed: {response.text[:300]}")
        return response.json()


async def ensure_order_paid(order_id: str) -> dict[str, Any]:
    order = await get_order(order_id)
    status = str(order.get("order_status") or "").upper()
    if status != "PAID":
        raise ValidationException(f"Payment not completed yet (status: {status or 'UNKNOWN'})")
    return order


def checkout_payload(
    *,
    order_id: str,
    payment_session_id: str,
    amount_inr: float,
    description: str,
    prefill: dict[str, Any],
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    settings = get_settings()
    amount_paise = max(int(round(amount_inr * 100)), 100)
    payload = {
        "order_id": order_id,
        "payment_session_id": payment_session_id,
        "amount": amount_paise,
        "amount_inr": round(amount_inr, 2),
        "currency": "INR",
        "environment": cashfree_environment(),
        "app_id": settings.cashfree_app_id,
        "description": description,
        "prefill": prefill,
        "provider": "cashfree",
    }
    if extra:
        payload.update(extra)
    return payload
