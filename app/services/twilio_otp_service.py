"""Twilio Verify API for SMS OTP."""
from functools import lru_cache

from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client

from app.core.config import settings
from app.core.exceptions import ValidationException
from app.core.logging import get_logger

logger = get_logger(__name__)


class TwilioOtpService:
    def __init__(self) -> None:
        self.account_sid = settings.twilio_account_sid
        self.auth_token = settings.twilio_auth_token
        self.verify_service_sid = settings.twilio_verify_service_sid
        self._client: Client | None = None

    @property
    def is_configured(self) -> bool:
        return bool(self.account_sid and self.auth_token and self.verify_service_sid)

    def _client_or_raise(self) -> Client:
        if not self.is_configured:
            raise ValidationException("SMS OTP is not configured. Contact support.")
        if self._client is None:
            self._client = Client(self.account_sid, self.auth_token)
        return self._client

    def send_otp(self, phone: str) -> None:
        client = self._client_or_raise()
        try:
            verification = client.verify.v2.services(self.verify_service_sid).verifications.create(
                to=phone,
                channel="sms",
            )
            logger.info("twilio_otp_sent", phone=phone[-4:], status=verification.status)
            if verification.status not in ("pending", "approved"):
                raise ValidationException("Failed to send OTP. Please try again.")
        except TwilioRestException as exc:
            logger.warning("twilio_otp_send_failed", code=exc.code, message=exc.msg)
            raise ValidationException(self._friendly_error(exc)) from exc

    def verify_otp(self, phone: str, code: str) -> None:
        client = self._client_or_raise()
        try:
            check = client.verify.v2.services(self.verify_service_sid).verification_checks.create(
                to=phone,
                code=code,
            )
            if check.status != "approved":
                raise ValidationException("Invalid OTP")
        except TwilioRestException as exc:
            logger.warning("twilio_otp_verify_failed", code=exc.code, message=exc.msg)
            if exc.status in (404, 400):
                raise ValidationException("Invalid or expired OTP") from exc
            raise ValidationException(self._friendly_error(exc)) from exc

    def verify_credentials(self) -> dict:
        """Validate account + Verify service SID (for health checks / startup)."""
        client = self._client_or_raise()
        try:
            service = client.verify.v2.services(self.verify_service_sid).fetch()
            account = client.api.accounts(self.account_sid).fetch()
            return {
                "valid": True,
                "account_sid": account.sid,
                "account_status": account.status,
                "verify_service_sid": service.sid,
                "verify_service_name": service.friendly_name,
            }
        except TwilioRestException as exc:
            return {
                "valid": False,
                "error": exc.msg,
                "code": exc.code,
            }

    @staticmethod
    def _friendly_error(exc: TwilioRestException) -> str:
        if exc.code == 60200:
            return "Invalid mobile number"
        if exc.code == 60203:
            return "Maximum OTP attempts reached. Please try again later."
        if exc.code == 20429:
            return "Too many OTP requests. Please wait and try again."
        return exc.msg or "Failed to process OTP request"


@lru_cache
def get_twilio_otp_service() -> TwilioOtpService:
    return TwilioOtpService()
