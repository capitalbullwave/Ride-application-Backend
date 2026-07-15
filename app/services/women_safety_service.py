"""Women safety alerts — notify admin and emergency contact when enabled."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession
from twilio.rest import Client

from app.core.config import settings
from app.core.logging import get_logger
from app.models import Ride, User
from app.notifications.service import NotificationService
from app.utils.phone import format_phone_display, normalize_phone

logger = get_logger(__name__)


def _send_emergency_sms(phone: str, body: str) -> bool:
    if not (
        settings.twilio_account_sid
        and settings.twilio_auth_token
        and settings.twilio_phone_number
    ):
        logger.info("women_safety_sms_skipped_twilio_not_configured")
        return False

    try:
        client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
        client.messages.create(
            to=normalize_phone(phone),
            from_=settings.twilio_phone_number,
            body=body,
        )
        return True
    except Exception as exc:
        logger.warning("women_safety_sms_failed", error=str(exc))
        return False


async def notify_women_safety_enabled(db: AsyncSession, ride: Ride, user: User) -> None:
    """Alert the rider, admin inbox, and emergency contact when safety mode is on."""
    service = NotificationService(db)
    user_name = f"{user.first_name} {user.last_name}".strip() or "Rider"
    user_phone = format_phone_display(user.phone)
    pickup = ride.pickup_address or "Pickup"
    dropoff = ride.dropoff_address or "Drop"

    ride_payload = {
        "event": "women_safety",
        "women_safety": True,
        "type": "emergency",
        "ride_id": str(ride.id),
        "user_id": str(user.id),
        "user_name": user_name,
        "user_phone": user_phone,
        "pickup": pickup,
        "dropoff": dropoff,
    }

    await service.notify_and_push(
        title="Women Safety Enabled",
        message="Your emergency contact and admin team have been alerted about your ride.",
        notification_type="SYSTEM",
        user_id=user.id,
        event="women_safety_enabled",
        ride_id=str(ride.id),
        channel_id="emergency",
        data=ride_payload,
    )

    await service.create_in_app(
        title="Women Safety Alert",
        message=(
            f"{user_name} ({user_phone}) enabled women safety for a ride.\n"
            f"{pickup} → {dropoff}"
        ),
        notification_type="WOMEN_SAFETY",
        data=ride_payload,
    )

    emergency_phone = (user.emergency_contact_phone or "").strip()
    if emergency_phone:
        contact_name = (user.emergency_contact_name or "Emergency contact").strip()
        sms_body = (
            f"Bull Wave Rides Safety Alert: {user_name} has started a ride with Women Safety enabled. "
            f"Route: {pickup} to {dropoff}. Ride ID: {str(ride.id)[:8]}."
        )
        sent = _send_emergency_sms(emergency_phone, sms_body)
        logger.info(
            "women_safety_emergency_contact_notified",
            ride_id=str(ride.id),
            user_id=str(user.id),
            contact=contact_name,
            sms_sent=sent,
        )
    else:
        logger.warning(
            "women_safety_missing_emergency_phone",
            ride_id=str(ride.id),
            user_id=str(user.id),
        )
