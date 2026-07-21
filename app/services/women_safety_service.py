"""Women safety alerts — enable mode + mid-ride SOS."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from twilio.rest import Client

from app.core.config import settings
from app.core.constants import SupportTicketPriority, SupportTicketStatus
from app.core.logging import get_logger
from app.models import Notification, Ride, SupportTicket, User
from app.notifications.service import NotificationService
from app.utils.phone import format_phone_display, normalize_phone

logger = get_logger(__name__)


@dataclass
class EmergencySmsResult:
    sent: bool
    reason: str
    to_phone: str | None = None

    @property
    def status(self) -> str:
        return "sent" if self.sent else self.reason


def _send_emergency_sms(phone: str, body: str) -> EmergencySmsResult:
    """Send SMS via Twilio Programmable Messaging (needs TWILIO_PHONE_NUMBER)."""
    to_phone = normalize_phone(phone)

    if not settings.twilio_account_sid or not settings.twilio_auth_token:
        logger.warning(
            "emergency_sms_skipped",
            reason="twilio_credentials_missing",
            to=to_phone[-4:] if to_phone else None,
        )
        return EmergencySmsResult(False, "twilio_credentials_missing", to_phone)

    if not (settings.twilio_phone_number or "").strip():
        # Verify Service SID alone cannot send free-form SOS texts.
        logger.warning(
            "emergency_sms_skipped",
            reason="twilio_phone_number_missing",
            to=to_phone[-4:] if to_phone else None,
            hint="Set TWILIO_PHONE_NUMBER to a Twilio SMS-capable number",
        )
        logger.info("emergency_sms_preview", to=to_phone, body=body)
        return EmergencySmsResult(False, "twilio_phone_number_missing", to_phone)

    try:
        client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
        msg = client.messages.create(
            to=to_phone,
            from_=settings.twilio_phone_number.strip(),
            body=body,
        )
        logger.info(
            "emergency_sms_sent",
            to=to_phone[-4:],
            sid=getattr(msg, "sid", None),
            status=getattr(msg, "status", None),
        )
        return EmergencySmsResult(True, "sent", to_phone)
    except Exception as exc:
        logger.warning(
            "emergency_sms_failed",
            error=str(exc),
            to=to_phone[-4:] if to_phone else None,
        )
        return EmergencySmsResult(False, "twilio_send_failed", to_phone)


def _maps_link(lat: float | None, lng: float | None) -> str | None:
    if lat is None or lng is None:
        return None
    return f"https://www.google.com/maps?q={lat},{lng}"


def _mask_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) < 4:
        return phone
    return f"******{digits[-4:]}"


async def notify_women_safety_enabled(db: AsyncSession, ride: Ride, user: User) -> EmergencySmsResult:
    """Alert the rider, admin inbox, and emergency contact when safety mode is on."""
    ride.women_safety_enabled = True
    await db.flush()

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

    emergency_phone = (user.emergency_contact_phone or "").strip()
    sms_result = EmergencySmsResult(False, "no_emergency_phone")
    if emergency_phone:
        sms_body = (
            f"Bull Wave Rides Safety Alert: {user_name} has started a ride with Safety Mode enabled. "
            f"Route: {pickup} to {dropoff}. Ride ID: {ride.public_id}."
        )
        sms_result = _send_emergency_sms(emergency_phone, sms_body)
        logger.info(
            "women_safety_emergency_contact_notified",
            ride_id=str(ride.id),
            user_id=str(user.id),
            contact=(user.emergency_contact_name or "").strip() or None,
            sms_status=sms_result.status,
            to=_mask_phone(sms_result.to_phone),
        )
    else:
        logger.warning(
            "women_safety_missing_emergency_phone",
            ride_id=str(ride.id),
            user_id=str(user.id),
        )

    user_message = (
        "Your emergency contact was notified by SMS."
        if sms_result.sent
        else (
            "Safety Mode is on. Add an emergency contact in Profile so we can SMS them."
            if sms_result.reason == "no_emergency_phone"
            else "Safety Mode is on. Support was notified; SMS to emergency contact could not be sent right now."
        )
    )

    await service.notify_and_push(
        title="Safety Mode Enabled",
        message=user_message,
        notification_type="SYSTEM",
        user_id=user.id,
        event="women_safety_enabled",
        ride_id=str(ride.id),
        channel_id="emergency",
        data={**ride_payload, "emergency_sms_status": sms_result.status},
    )

    await service.create_in_app(
        title="Women Safety Alert",
        message=(
            f"{user_name} ({user_phone}) enabled safety mode for a ride.\n"
            f"{pickup} → {dropoff}\n"
            f"Emergency SMS: {sms_result.status}"
        ),
        notification_type="WOMEN_SAFETY",
        data={**ride_payload, "emergency_sms_status": sms_result.status},
    )

    return sms_result


async def trigger_ride_sos(
    db: AsyncSession,
    ride: Ride,
    user: User,
    *,
    lat: float | None = None,
    lng: float | None = None,
    message: str | None = None,
) -> tuple[SupportTicket, EmergencySmsResult]:
    """Mark ride as emergency and alert admin + emergency contacts with live details."""
    from sqlalchemy import select

    from app.models import DriverLocation

    loaded = await db.execute(
        select(Ride)
        .options(selectinload(Ride.driver), selectinload(Ride.vehicle))
        .where(Ride.id == ride.id)
    )
    ride = loaded.scalar_one()
    ride.is_emergency = True
    ride.women_safety_enabled = True
    await db.flush()

    user_name = f"{user.first_name} {user.last_name}".strip() or "Rider"
    user_phone = format_phone_display(user.phone)
    driver = ride.driver
    vehicle = ride.vehicle
    driver_name = (
        f"{driver.first_name} {driver.last_name}".strip() if driver else "Not assigned"
    )
    driver_phone = format_phone_display(driver.phone) if driver and driver.phone else "—"
    plate = vehicle.license_plate if vehicle else "—"

    # Current = passenger SOS trigger point; Live = captain GPS (fallback to current).
    current_lat = lat if lat is not None else ride.pickup_lat
    current_lng = lng if lng is not None else ride.pickup_lng
    live_lat = current_lat
    live_lng = current_lng
    if ride.driver_id:
        loc_result = await db.execute(
            select(DriverLocation).where(DriverLocation.driver_id == ride.driver_id)
        )
        driver_loc = loc_result.scalar_one_or_none()
        if driver_loc is not None:
            live_lat = driver_loc.lat
            live_lng = driver_loc.lng

    current_maps = _maps_link(current_lat, current_lng) or "Location unavailable"
    live_maps = _maps_link(live_lat, live_lng) or "Location unavailable"
    note = (message or "").strip() or "Passenger triggered SOS"

    ticket = SupportTicket(
        user_id=user.id,
        subject="SOS Emergency Alert — Women Safety",
        description=(
            f"{note}\n"
            f"Rider: {user_name} ({user_phone})\n"
            f"Driver: {driver_name} ({driver_phone})\n"
            f"Vehicle: {plate}\n"
            f"Route: {ride.pickup_address} → {ride.dropoff_address}\n"
            f"Current location: {current_maps}\n"
            f"Live location: {live_maps}\n"
            f"Ride ID: {ride.public_id}"
        ),
        status=SupportTicketStatus.OPEN.value,
        priority=SupportTicketPriority.URGENT.value,
    )
    db.add(ticket)
    await db.flush()

    emergency_phone = (user.emergency_contact_phone or "").strip()
    sms_result = EmergencySmsResult(False, "no_emergency_phone")
    sms_body = (
        f"SOS ALERT — Bull Wave Rides: {user_name} needs help.\n"
        f"Driver: {driver_name} | Vehicle: {plate}\n"
        f"Going to: {ride.dropoff_address}\n"
        f"Current location: {current_maps}\n"
        f"Live location: {live_maps}\n"
        f"Ride: {ride.public_id}"
    )
    if emergency_phone:
        sms_result = _send_emergency_sms(emergency_phone, sms_body)
        logger.info(
            "ride_sos_emergency_sms",
            ride_id=str(ride.id),
            sms_status=sms_result.status,
            to=_mask_phone(sms_result.to_phone),
        )
    else:
        logger.warning("ride_sos_missing_emergency_phone", ride_id=str(ride.id))

    payload = {
        "event": "ride_sos",
        "type": "emergency",
        "ride_id": str(ride.id),
        "public_id": ride.public_id,
        "user_id": str(user.id),
        "ticket_id": str(ticket.id),
        "current_lat": current_lat,
        "current_lng": current_lng,
        "live_lat": live_lat,
        "live_lng": live_lng,
        "lat": current_lat,
        "lng": current_lng,
        "is_emergency": True,
        "emergency_sms_status": sms_result.status,
        "emergency_sms_sent": sms_result.sent,
        "emergency_contact_masked": _mask_phone(sms_result.to_phone),
    }

    if sms_result.sent:
        user_sos_message = (
            f"SOS sent. Your emergency contact ({_mask_phone(sms_result.to_phone)}) "
            "and support have been notified."
        )
    elif sms_result.reason == "no_emergency_phone":
        user_sos_message = (
            "SOS sent to support. Add an emergency contact in Profile to SMS them next time."
        )
    else:
        user_sos_message = (
            "SOS sent to support. SMS to your emergency contact could not be delivered "
            f"({sms_result.reason})."
        )

    service = NotificationService(db)
    await service.notify_and_push(
        title="SOS Alert Sent",
        message=user_sos_message,
        notification_type="SYSTEM",
        user_id=user.id,
        event="ride_sos",
        ride_id=str(ride.id),
        channel_id="emergency",
        data=payload,
    )

    db.add(
        Notification(
            title="Passenger SOS Alert",
            message=(
                f"{user_name} triggered SOS. Driver: {driver_name}, Vehicle: {plate}. "
                f"Current: {current_maps} | Live: {live_maps} | SMS: {sms_result.status}"
            ),
            notification_type="ADMIN",
            data=payload,
        )
    )

    if driver is not None:
        await service.notify_and_push(
            title="Passenger needs help",
            message="Your passenger triggered an SOS alert. Stay calm and assist if safe.",
            notification_type="RIDE",
            driver_id=driver.id,
            event="ride_sos",
            ride_id=str(ride.id),
            channel_id="emergency",
            data=payload,
        )

    await db.flush()
    return ticket, sms_result
