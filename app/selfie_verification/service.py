"""Shift lifecycle + selfie verification orchestration."""
from __future__ import annotations

import base64
import binascii
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.constants import DriverStatus, KYCStatus, SelfieVerificationStatus, ShiftStatus
from app.core.exceptions import ForbiddenException, ValidationException
from app.core.logging import get_logger
from app.drivers.models import Driver
from app.repositories.driver_repository import DriverRepository
from app.selfie_verification.face import get_face_provider
from app.selfie_verification.liveness import get_liveness_provider
from app.selfie_verification.models import DriverSelfieLog, DriverShift
from app.selfie_verification.repository import DriverSelfieLogRepository, DriverShiftRepository
from app.selfie_verification.schemas import (
    GoOfflineResponse,
    GoOnlineResponse,
    LivenessChallengeResponse,
    SelfieVerifyRequest,
    SelfieVerifyResponse,
    ShiftResponse,
    VerificationStatusResponse,
)
from app.selfie_verification.storage import (
    persist_selfie_bytes,
    resolve_registered_face_bytes,
)
from app.services.driver_matching import DriverMatchingService

logger = get_logger(__name__)

_DATA_URL_RE = re.compile(
    r"^data:image/(?P<fmt>[a-zA-Z0-9.+-]+);base64,(?P<data>.+)$",
    re.DOTALL,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _decode_image(payload: str) -> bytes:
    text = (payload or "").strip()
    if not text:
        raise ValidationException("Selfie image is required.", details={"error_code": "FACE_NOT_DETECTED"})
    match = _DATA_URL_RE.match(text)
    raw_b64 = match.group("data") if match else text
    try:
        raw = base64.b64decode(raw_b64, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise ValidationException(
            "Invalid selfie image payload.",
            details={"error_code": "FACE_NOT_DETECTED"},
        ) from exc
    if len(raw) < 1_000:
        raise ValidationException(
            "Selfie quality too low. Improve lighting and retake.",
            details={"error_code": "POOR_LIGHTING"},
        )
    return raw


def _shift_to_response(shift: DriverShift) -> ShiftResponse:
    return ShiftResponse(
        shift_id=shift.id,
        driver_id=shift.driver_id,
        started_at=shift.started_at,
        ended_at=shift.ended_at,
        status=shift.status,
        selfie_verified=shift.selfie_verified,
        selfie_verified_at=shift.selfie_verified_at,
        force_close_reason=shift.force_close_reason,
    )


class DriverSelfieShiftService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.shifts = DriverShiftRepository(db)
        self.logs = DriverSelfieLogRepository(db)
        self.drivers = DriverRepository(db)

    # ── Auto force-close ──────────────────────────────────────────────

    def _is_stale(self, shift: DriverShift, now: datetime | None = None) -> tuple[bool, str | None]:
        now = now or _utcnow()
        started = shift.started_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        max_age = timedelta(hours=settings.shift_max_hours)
        if now - started > max_age:
            return True, f"Shift exceeded {settings.shift_max_hours} hours"
        if started.astimezone(timezone.utc).date() != now.astimezone(timezone.utc).date():
            return True, "Shift date differs from current date"
        return False, None

    async def _ensure_driver_offline(self, driver: Driver | None) -> None:
        """Always mark the driver offline when a shift ends (complete or force-close)."""
        if driver is None:
            return
        if driver.status != DriverStatus.OFFLINE.value:
            driver.status = DriverStatus.OFFLINE.value
            await self.drivers.update(driver)
        try:
            await DriverMatchingService(self.db).set_driver_offline(driver.id)
        except Exception as exc:
            logger.warning(
                "driver_offline_sync_failed",
                driver_id=str(driver.id),
                error=str(exc),
            )

    async def force_close_shift(self, shift: DriverShift, reason: str) -> DriverShift:
        now = _utcnow()
        shift.status = ShiftStatus.FORCE_CLOSED.value
        shift.ended_at = now
        shift.force_close_reason = reason
        await self.shifts.save(shift)

        driver = await self.drivers.get_by_id(shift.driver_id)
        # Shift over → always offline (including previously ON_RIDE / BUSY / ONLINE).
        await self._ensure_driver_offline(driver)

        logger.info(
            "shift_force_closed",
            shift_id=str(shift.id),
            driver_id=str(shift.driver_id),
            reason=reason,
            driver_status=driver.status if driver else None,
        )
        return shift

    async def close_stale_shifts_for_driver(self, driver_id: uuid.UUID) -> Optional[DriverShift]:
        active = await self.shifts.get_active_shift(driver_id)
        if not active:
            return None
        stale, reason = self._is_stale(active)
        if stale:
            return await self.force_close_shift(active, reason or "Stale shift")
        return active

    async def force_close_all_stale_shifts(self) -> int:
        """Celery / cron entrypoint: close shifts older than 16h or from a prior date."""
        today = _utcnow().date()
        stale = await self.shifts.list_stale_active_shifts(
            max_age=timedelta(hours=settings.shift_max_hours),
            before_date=today,
        )
        closed = 0
        for shift in stale:
            stale_flag, reason = self._is_stale(shift)
            if stale_flag:
                await self.force_close_shift(shift, reason or "Stale shift")
                closed += 1
        await self.db.commit()
        return closed

    # ── Eligibility ───────────────────────────────────────────────────

    def _assert_kyc(self, driver: Driver) -> None:
        if driver.kyc_status != KYCStatus.APPROVED.value:
            if driver.kyc_status == KYCStatus.REJECTED.value:
                raise ForbiddenException(
                    "Your documents were rejected. Please update and resubmit before going online."
                )
            raise ForbiddenException(
                "Account verification is pending. You can go online after admin approval."
            )
        if not driver.is_verified:
            raise ForbiddenException("Phone verification is required before going online.")
        if not driver.is_active:
            raise ForbiddenException("Driver account is not active.")

    async def _lockout_info(self, driver_id: uuid.UUID) -> tuple[int, Optional[datetime]]:
        window = timedelta(minutes=settings.selfie_lockout_minutes)
        since = _utcnow() - window
        failures = await self.logs.count_recent_failures(driver_id, since=since)
        # Mock face matching is for local/dev — don't lock drivers out while tuning.
        if (settings.face_provider or "").lower() == "mock":
            return failures, None
        locked_until = None
        if failures >= settings.selfie_max_failed_attempts:
            locked_until = _utcnow() + window
        return failures, locked_until

    async def get_verification_status(self, driver: Driver) -> VerificationStatusResponse:
        closed = await self.close_stale_shifts_for_driver(driver.id)
        # If a stale shift was just force-closed, persist offline immediately.
        if closed is not None and closed.status == ShiftStatus.FORCE_CLOSED.value:
            await self.db.commit()

        active = await self.shifts.get_active_shift(driver.id)
        failures, locked_until = await self._lockout_info(driver.id)

        if locked_until and locked_until > _utcnow():
            return VerificationStatusResponse(
                can_go_online=False,
                selfie_required=True,
                has_active_shift=False,
                failed_attempts=failures,
                locked_until=locked_until,
                message="Too many failed selfie attempts. Please try again later.",
            )

        if active and active.selfie_verified:
            return VerificationStatusResponse(
                can_go_online=True,
                selfie_required=False,
                has_active_shift=True,
                active_shift=_shift_to_response(active),
                failed_attempts=failures,
                message="Active verified shift. You can go online.",
            )

        since = _utcnow() - timedelta(minutes=settings.selfie_verification_ttl_minutes)
        pending = await self.logs.get_consumable_success(driver.id, since=since)
        return VerificationStatusResponse(
            can_go_online=bool(pending),
            selfie_required=pending is None,
            has_active_shift=False,
            pending_verification_id=pending.id if pending else None,
            failed_attempts=failures,
            message=(
                "Selfie verified. Tap Go Online to start your shift."
                if pending
                else "Selfie verification required before going online."
            ),
        )

    async def issue_liveness_challenge(self, driver: Driver) -> LivenessChallengeResponse:
        self._assert_kyc(driver)
        _, locked_until = await self._lockout_info(driver.id)
        if locked_until and locked_until > _utcnow():
            raise ForbiddenException(
                "Too many failed selfie attempts. Please try again later.",
                details={"error_code": "RATE_LIMITED", "locked_until": locked_until.isoformat()},
            )
        challenge = get_liveness_provider().issue_challenge(str(driver.id))
        return LivenessChallengeResponse(
            challenge_id=challenge.challenge_id,
            actions=challenge.actions,
            expires_at=challenge.expires_at,
        )

    # ── Verify selfie ─────────────────────────────────────────────────

    async def verify_selfie(
        self,
        driver: Driver,
        data: SelfieVerifyRequest,
        *,
        ip_address: str | None = None,
    ) -> SelfieVerifyResponse:
        self._assert_kyc(driver)
        await self.close_stale_shifts_for_driver(driver.id)

        failures, locked_until = await self._lockout_info(driver.id)
        if locked_until and locked_until > _utcnow():
            log = DriverSelfieLog(
                driver_id=driver.id,
                status=SelfieVerificationStatus.RATE_LIMITED.value,
                matched=False,
                liveness_passed=False,
                error_code="RATE_LIMITED",
                error_message="Too many failed attempts.",
                device_id=data.device_id,
                source=data.source,
                ip_address=ip_address,
                attempt_number=failures + 1,
            )
            await self.logs.create(log)
            await self.db.commit()
            raise ForbiddenException(
                "Too many failed selfie attempts. Please try again later.",
                details={"error_code": "RATE_LIMITED"},
            )

        if data.source != "live_camera":
            raise ValidationException(
                "Only live camera selfies are allowed.",
                details={"error_code": "GALLERY_NOT_ALLOWED"},
            )

        live_bytes = _decode_image(data.selfie_base64)
        registered = resolve_registered_face_bytes(driver.profile_photo)
        if not registered:
            raise ValidationException(
                "No registered face photo on file. Complete profile photo registration first.",
                details={"error_code": "NO_REGISTERED_FACE"},
            )

        liveness_provider = get_liveness_provider()
        liveness = await liveness_provider.verify(
            challenge_id=data.challenge_id,
            driver_id=str(driver.id),
            client_results=data.liveness.model_dump(),
            live_selfie=live_bytes,
        )

        face_provider = get_face_provider()
        face_result = None
        if liveness.passed:
            face_result = await face_provider.verify_face(
                registered,
                live_bytes,
                threshold=settings.face_match_threshold,
            )

        selfie_path = persist_selfie_bytes(str(driver.id), live_bytes, prefix="live")
        attempt_number = failures + 1
        matched = bool(face_result and face_result.matched)
        success = liveness.passed and matched

        error_code = None
        error_message = None
        if not liveness.passed:
            error_code = liveness.error_code or "LIVENESS_FAILED"
            error_message = liveness.error_message or "Liveness checks failed."
        elif face_result and not face_result.matched:
            error_code = face_result.error_code or "LOW_CONFIDENCE"
            error_message = face_result.error_message or (
                "We could not confirm your identity from this selfie. "
                "Please face the camera clearly and try again."
            )
            if face_result.face_count and face_result.face_count > 1:
                error_code = "MULTIPLE_FACES"
                error_message = (
                    "More than one face was detected. Please ensure only you are "
                    "visible in the frame and try again."
                )

        log = DriverSelfieLog(
            driver_id=driver.id,
            status=(
                SelfieVerificationStatus.SUCCESS.value
                if success
                else SelfieVerificationStatus.FAILED.value
            ),
            matched=matched,
            confidence_score=face_result.confidence if face_result else None,
            liveness_passed=liveness.passed,
            liveness_details={
                "actions_passed": liveness.actions_passed,
                "actions_failed": liveness.actions_failed,
                "anti_spoof_score": liveness.anti_spoof_score,
                "details": liveness.details,
            },
            face_provider=face_provider.name,
            liveness_provider=liveness_provider.name,
            selfie_image_path=selfie_path,
            registered_image_path=driver.profile_photo,
            error_code=error_code,
            error_message=error_message,
            device_id=data.device_id,
            source=data.source,
            ip_address=ip_address,
            attempt_number=attempt_number,
            consumed_for_shift=False,
        )
        await self.logs.create(log)
        await self.db.commit()

        logger.info(
            "selfie_verification_attempt",
            driver_id=str(driver.id),
            success=success,
            confidence=log.confidence_score,
            error_code=error_code,
            profile_photo=bool(driver.profile_photo),
            registered_bytes=len(registered) if registered else 0,
            live_bytes=len(live_bytes),
        )

        if not success:
            return SelfieVerifyResponse(
                verified=False,
                matched=matched,
                confidence_score=log.confidence_score,
                liveness_passed=liveness.passed,
                verification_id=log.id,
                error_code=error_code,
                message=error_message or "Verification failed. Stay offline.",
                steps={
                    "liveness": liveness.passed,
                    "face_match": matched,
                    "verified": False,
                },
            )

        return SelfieVerifyResponse(
            verified=True,
            matched=True,
            confidence_score=log.confidence_score,
            liveness_passed=True,
            verification_id=log.id,
            message="Identity verified. You can go online.",
            steps={"liveness": True, "face_match": True, "verified": True},
        )

    # ── Go online / offline ───────────────────────────────────────────

    async def go_online(self, driver: Driver) -> GoOnlineResponse:
        self._assert_kyc(driver)
        await self.close_stale_shifts_for_driver(driver.id)

        active = await self.shifts.get_active_shift(driver.id)
        if active and active.selfie_verified:
            # Resume same-day verified shift
            driver.status = DriverStatus.ONLINE.value
            await self.drivers.update(driver)
            matching = DriverMatchingService(self.db)
            lat, lng = await matching.driver_default_location(driver.id)
            await matching.ensure_driver_online(driver, lat, lng)
            await self.db.commit()
            return GoOnlineResponse(
                status=driver.status,
                shift=_shift_to_response(active),
                message="You are online.",
            )

        since = _utcnow() - timedelta(minutes=settings.selfie_verification_ttl_minutes)
        verification = await self.logs.get_consumable_success(driver.id, since=since)
        if not verification:
            raise ForbiddenException(
                "Selfie verification required before going online.",
                details={"error_code": "SELFIE_REQUIRED", "selfie_required": True},
            )

        now = _utcnow()
        shift = DriverShift(
            driver_id=driver.id,
            started_at=now,
            status=ShiftStatus.ACTIVE.value,
            selfie_verified=True,
            selfie_verified_at=verification.created_at or now,
            verification_log_id=verification.id,
        )
        await self.shifts.create_shift(shift)
        await self.logs.mark_consumed(verification.id, shift.id)

        driver.status = DriverStatus.ONLINE.value
        await self.drivers.update(driver)
        matching = DriverMatchingService(self.db)
        lat, lng = await matching.driver_default_location(driver.id)
        await matching.ensure_driver_online(driver, lat, lng)
        await self.db.commit()

        logger.info(
            "driver_shift_started",
            driver_id=str(driver.id),
            shift_id=str(shift.id),
            verification_id=str(verification.id),
        )
        return GoOnlineResponse(
            status=driver.status,
            shift=_shift_to_response(shift),
            message="Shift started. You are online.",
        )

    async def go_offline(self, driver: Driver) -> GoOfflineResponse:
        await self.close_stale_shifts_for_driver(driver.id)
        active = await self.shifts.get_active_shift(driver.id)
        if active:
            active.status = ShiftStatus.COMPLETED.value
            active.ended_at = _utcnow()
            await self.shifts.save(active)

        await self._ensure_driver_offline(driver)
        await self.db.commit()

        return GoOfflineResponse(
            status=driver.status,
            shift=_shift_to_response(active) if active else None,
            message="You are offline. Shift closed.",
        )

    async def get_current_shift(self, driver: Driver) -> Optional[ShiftResponse]:
        closed = await self.close_stale_shifts_for_driver(driver.id)
        if closed is not None and closed.status == ShiftStatus.FORCE_CLOSED.value:
            await self.db.commit()
        active = await self.shifts.get_active_shift(driver.id)
        return _shift_to_response(active) if active else None

    async def assert_can_accept_rides(self, driver: Driver) -> DriverShift:
        """Gate ride acceptance on an active, selfie-verified shift."""
        await self.close_stale_shifts_for_driver(driver.id)
        active = await self.shifts.get_active_shift(driver.id)
        if not active or not active.selfie_verified:
            await self._ensure_driver_offline(driver)
            await self.db.commit()
            raise ForbiddenException(
                "Selfie verification required before accepting rides.",
                details={"error_code": "SELFIE_REQUIRED", "selfie_required": True},
            )
        if driver.status not in (
            DriverStatus.ONLINE.value,
            DriverStatus.ON_RIDE.value,
            DriverStatus.BUSY.value,
        ):
            raise ForbiddenException(
                "You must be online with a verified shift to accept rides.",
                details={"error_code": "NOT_ONLINE"},
            )
        return active

    async def admin_force_offline(self, driver: Driver, reason: str = "Forced offline by admin") -> dict[str, Any]:
        active = await self.shifts.get_active_shift(driver.id)
        if active:
            await self.force_close_shift(active, reason)
        else:
            await self._ensure_driver_offline(driver)
        await self.db.commit()
        return {
            "driver_id": str(driver.id),
            "status": driver.status,
            "shift": _shift_to_response(active).model_dump(mode="json") if active else None,
            "message": reason,
        }
