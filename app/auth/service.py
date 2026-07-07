from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import UserRole
from app.core.exceptions import ConflictException, UnauthorizedException, ValidationException
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    generate_otp,
    hash_password,
    verify_password,
)
from app.models import AdminUser, Driver, User
from app.repositories.admin_repository import AdminRepository
from app.repositories.driver_repository import DriverRepository
from app.repositories.user_repository import UserRepository
from app.schemas.common import TokenResponse
from app.utils.phone import normalize_phone
from app.schemas.driver import (
    DriverLogin,
    DriverPhoneOTPRequest,
    DriverPhoneOTPVerify,
    DriverRegister,
    DriverRegisterOTPSend,
    DriverRegisterOTPVerify,
)
from app.schemas.user import (
    ForgotPassword,
    OTPVerify,
    ResetPassword,
    UserLogin,
    UserPhoneOTPRequest,
    UserPhoneOTPVerify,
    UserRegister,
    UserRegisterOTPSend,
    UserRegisterOTPVerify,
)
from app.core.config import settings
from app.core.logging import get_logger
from app.services.payment_service import WalletService
from app.services.driver_deletion_service import purge_soft_deleted_driver_signup_conflicts
from app.services.user_deletion_service import purge_soft_deleted_user_signup_conflicts
from app.services.twilio_otp_service import get_twilio_otp_service

logger = get_logger(__name__)


class AuthService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.user_repo = UserRepository(db)
        self.driver_repo = DriverRepository(db)
        self.admin_repo = AdminRepository(db)

    def _create_tokens(self, user_id: str, role: UserRole, token_version: int = 1) -> TokenResponse:
        return TokenResponse(
            access_token=create_access_token(user_id, role, token_version),
            refresh_token=create_refresh_token(user_id, role, token_version),
        )

    def _otp_response(self, message: str, otp: str | None = None) -> dict:
        response: dict = {"message": message, "success": True}
        # Expose hardcoded OTP in development for local testing.
        if settings.is_development and otp:
            response["otp"] = otp
        return response

    def _driver_otp_email(self, phone: str) -> str:
        return f"{normalize_phone(phone).replace('+', '')}@driver.ridebook.app"

    def _user_otp_email(self, phone: str) -> str:
        return f"{normalize_phone(phone).replace('+', '')}@ridebook.app"

    async def _get_driver_for_phone_otp(self, phone: str) -> Driver | None:
        normalized = normalize_phone(phone)
        return await self.driver_repo.find_for_otp(normalized, self._driver_otp_email(normalized))

    async def _get_user_for_phone_otp(self, phone: str) -> User | None:
        normalized = normalize_phone(phone)
        return await self.user_repo.find_for_otp(normalized, self._user_otp_email(normalized))

    def _twilio_otp(self):
        return get_twilio_otp_service()

    _DEV_HARDCODED_OTP = "123456"

    async def _issue_phone_otp(self, phone: str) -> str | None:
        """Send OTP via Twilio Verify when configured; otherwise local/dev fallback."""
        if self._twilio_otp().is_configured:
            self._twilio_otp().send_otp(phone)
            return None

        if settings.is_development:
            logger.warning(
                "dev_otp_hardcoded",
                phone=phone,
                otp=self._DEV_HARDCODED_OTP,
                hint="Twilio not configured — using hardcoded OTP 123456.",
            )
            return self._DEV_HARDCODED_OTP

        otp = generate_otp()
        return otp

    def _validate_phone_otp(
        self,
        phone: str,
        submitted_otp: str,
        stored_otp: str | None,
        stored_expires_at: datetime | None,
    ) -> None:
        if self._twilio_otp().is_configured:
            self._twilio_otp().verify_otp(phone, submitted_otp)
            return

        if not stored_otp or stored_otp != submitted_otp:
            raise ValidationException("Invalid OTP")
        if stored_expires_at and stored_expires_at < datetime.now(timezone.utc):
            raise ValidationException("OTP expired")

    async def register_user(self, data: UserRegister) -> TokenResponse:
        phone = normalize_phone(data.phone)
        if await self.user_repo.get_by_email(data.email):
            raise ConflictException("Email already registered")
        if await self.user_repo.get_by_phone(phone):
            raise ConflictException("Phone already registered")

        user = User(
            email=data.email,
            phone=phone,
            password_hash=hash_password(data.password),
            first_name=data.first_name,
            last_name=data.last_name or "",
            role=UserRole.USER.value,
            is_verified=True,
        )
        await self.user_repo.create(user)
        await WalletService(self.db).get_or_create_wallet(user_id=user.id)
        return self._create_tokens(str(user.id), UserRole.USER, user.token_version)

    async def send_user_register_otp(self, data: UserRegisterOTPSend) -> dict:
        phone = normalize_phone(data.phone)
        email = data.email or f"{phone.replace('+', '')}@ridebook.app"

        existing = await self.user_repo.find_for_otp(phone, email)
        if existing and existing.is_verified:
            raise ConflictException("Phone already registered")

        otp = await self._issue_phone_otp(phone)
        expires = datetime.now(timezone.utc) + timedelta(minutes=10)

        if existing:
            existing.password_hash = hash_password(data.password)
            existing.first_name = data.first_name
            existing.last_name = data.last_name or ""
            existing.email = email
            existing.phone = phone
            existing.otp = otp
            existing.otp_expires_at = expires if otp else None
            existing.is_verified = False
            await self.user_repo.update(existing)
        else:
            await purge_soft_deleted_user_signup_conflicts(self.db, phone, email)
            user = User(
                email=email,
                phone=phone,
                password_hash=hash_password(data.password),
                first_name=data.first_name,
                last_name=data.last_name or "",
                role=UserRole.USER.value,
                is_verified=False,
                otp=otp,
                otp_expires_at=expires if otp else None,
            )
            await self.user_repo.create(user)

        return self._otp_response("OTP sent to your mobile number", otp)

    async def verify_user_register_otp(self, data: UserRegisterOTPVerify) -> TokenResponse:
        phone = normalize_phone(data.phone)
        user = await self.user_repo.get_by_phone(phone)
        if not user:
            raise UnauthorizedException("No signup found for this mobile number")
        if user.is_verified:
            raise ConflictException("Account already verified. Please login.")
        self._validate_phone_otp(phone, data.otp, user.otp, user.otp_expires_at)

        user.is_verified = True
        user.otp = None
        user.otp_expires_at = None
        user.last_login_at = datetime.now(timezone.utc)
        await self.user_repo.update(user)
        await WalletService(self.db).get_or_create_wallet(user_id=user.id)
        return self._create_tokens(str(user.id), UserRole.USER, user.token_version)

    async def resend_user_register_otp(self, data: UserPhoneOTPRequest) -> dict:
        phone = normalize_phone(data.phone)
        user = await self.user_repo.get_by_phone(phone)
        if not user:
            raise UnauthorizedException("No signup found for this mobile number")
        if user.is_verified:
            raise ConflictException("Account already verified. Please login.")

        otp = await self._issue_phone_otp(phone)
        user.otp = otp
        user.otp_expires_at = datetime.now(timezone.utc) + timedelta(minutes=10) if otp else None
        await self.user_repo.update(user)
        return self._otp_response("OTP resent to your mobile number", otp)

    async def login_user(self, data: UserLogin) -> TokenResponse:
        if data.phone:
            user = await self.user_repo.get_by_phone(data.phone)
            error_msg = "Invalid mobile number or password"
        else:
            user = await self.user_repo.get_by_email(data.email)
            error_msg = "Invalid email or password"

        if not user or not verify_password(data.password, user.password_hash):
            raise UnauthorizedException(error_msg)
        if not user.is_active:
            raise UnauthorizedException("Account is deactivated")
        if not user.is_verified:
            raise UnauthorizedException("Account not verified. Please complete signup first.")
        user.last_login_at = datetime.now(timezone.utc)
        await self.user_repo.update(user)
        return self._create_tokens(str(user.id), UserRole.USER, user.token_version)

    async def send_user_login_otp(self, data: UserPhoneOTPRequest) -> dict:
        phone = normalize_phone(data.phone)
        user = await self.user_repo.get_by_phone(phone)
        if not user:
            raise UnauthorizedException("No account found with this mobile number")
        if not user.is_active:
            raise UnauthorizedException("Account is deactivated")
        if not user.is_verified:
            raise UnauthorizedException("Account not verified. Please complete signup first.")

        otp = await self._issue_phone_otp(phone)
        user.otp = otp
        user.otp_expires_at = datetime.now(timezone.utc) + timedelta(minutes=10) if otp else None
        await self.user_repo.update(user)

        return self._otp_response("OTP sent to your mobile number", otp)

    async def send_user_phone_otp(self, phone: str) -> dict:
        """Send OTP for phone login — auto-selects login vs signup flow."""
        phone = normalize_phone(phone)
        user = await self.user_repo.get_by_phone(phone)
        if user and user.is_verified:
            return await self.send_user_login_otp(UserPhoneOTPRequest(phone=phone))
        return await self.send_user_register_otp(
            UserRegisterOTPSend(
                phone=phone,
                password="temp123456",
                first_name="User",
                last_name="",
            )
        )

    async def verify_user_phone_otp(self, phone: str, otp: str) -> TokenResponse:
        """Verify phone OTP — auto-selects login vs signup flow."""
        phone = normalize_phone(phone)
        user = await self.user_repo.get_by_phone(phone)
        if user and user.is_verified:
            return await self.verify_user_login_otp(
                UserPhoneOTPVerify(phone=phone, otp=otp)
            )
        return await self.verify_user_register_otp(
            UserRegisterOTPVerify(phone=phone, otp=otp)
        )

    async def verify_user_login_otp(self, data: UserPhoneOTPVerify) -> TokenResponse:
        phone = normalize_phone(data.phone)
        user = await self.user_repo.get_by_phone(phone)
        if not user:
            raise UnauthorizedException("No account found with this mobile number")
        self._validate_phone_otp(phone, data.otp, user.otp, user.otp_expires_at)
        if not user.is_active:
            raise UnauthorizedException("Account is deactivated")

        user.is_verified = True
        user.otp = None
        user.otp_expires_at = None
        user.last_login_at = datetime.now(timezone.utc)
        await self.user_repo.update(user)
        return self._create_tokens(str(user.id), UserRole.USER, user.token_version)

    async def register_driver(self, data: DriverRegister) -> TokenResponse:
        phone = normalize_phone(data.phone)
        if await self.driver_repo.get_by_email(data.email):
            raise ConflictException("Email already registered")
        if await self.driver_repo.get_by_phone(phone):
            raise ConflictException("Phone already registered")

        driver = Driver(
            email=data.email,
            phone=phone,
            password_hash=hash_password(data.password),
            first_name=data.first_name,
            last_name=data.last_name or "",
            license_number=data.license_number,
            is_verified=True,
        )
        await self.driver_repo.create(driver)
        await WalletService(self.db).get_or_create_wallet(driver_id=driver.id)
        return self._create_tokens(str(driver.id), UserRole.DRIVER, driver.token_version)

    async def send_driver_register_otp(self, data: DriverRegisterOTPSend) -> dict:
        phone = normalize_phone(data.phone)
        email = data.email or f"{phone.replace('+', '')}@driver.ridebook.app"

        existing = await self.driver_repo.find_for_otp(phone, email)
        if existing and existing.is_verified:
            raise ConflictException("Phone already registered")

        otp = await self._issue_phone_otp(phone)
        expires = datetime.now(timezone.utc) + timedelta(minutes=10)

        if existing:
            existing.password_hash = hash_password(data.password)
            existing.first_name = data.first_name
            existing.last_name = data.last_name or ""
            existing.email = email
            existing.phone = phone
            existing.license_number = data.license_number
            existing.otp = otp
            existing.otp_expires_at = expires if otp else None
            existing.is_verified = False
            await self.driver_repo.update(existing)
        else:
            await purge_soft_deleted_driver_signup_conflicts(self.db, phone, email)
            driver = Driver(
                email=email,
                phone=phone,
                password_hash=hash_password(data.password),
                first_name=data.first_name,
                last_name=data.last_name or "",
                license_number=data.license_number,
                is_verified=False,
                otp=otp,
                otp_expires_at=expires if otp else None,
            )
            await self.driver_repo.create(driver)

        return self._otp_response("OTP sent to your mobile number", otp)

    async def verify_driver_register_otp(self, data: DriverRegisterOTPVerify) -> TokenResponse:
        phone = normalize_phone(data.phone)
        driver = await self._get_driver_for_phone_otp(phone)
        if not driver:
            raise UnauthorizedException("No signup found for this mobile number")
        if driver.is_verified:
            raise ConflictException("Account already verified. Please login.")
        self._validate_phone_otp(phone, data.otp, driver.otp, driver.otp_expires_at)

        driver.is_verified = True
        driver.otp = None
        driver.otp_expires_at = None
        driver.last_login_at = datetime.now(timezone.utc)
        await self.driver_repo.update(driver)
        await WalletService(self.db).get_or_create_wallet(driver_id=driver.id)
        return self._create_tokens(str(driver.id), UserRole.DRIVER, driver.token_version)

    async def resend_driver_register_otp(self, data: DriverPhoneOTPRequest) -> dict:
        phone = normalize_phone(data.phone)
        driver = await self._get_driver_for_phone_otp(phone)
        if not driver:
            raise UnauthorizedException("No signup found for this mobile number")
        if driver.is_verified:
            raise ConflictException("Account already verified. Please login.")

        otp = await self._issue_phone_otp(phone)
        driver.otp = otp
        driver.otp_expires_at = datetime.now(timezone.utc) + timedelta(minutes=10) if otp else None
        driver.phone = phone
        await self.driver_repo.update(driver)
        return self._otp_response("OTP resent to your mobile number", otp)

    async def login_driver(self, data: DriverLogin) -> TokenResponse:
        if data.phone:
            driver = await self.driver_repo.get_by_phone(data.phone)
            error_msg = "Invalid mobile number or password"
        else:
            driver = await self.driver_repo.get_by_email(data.email)
            error_msg = "Invalid email or password"

        if not driver or not verify_password(data.password, driver.password_hash):
            raise UnauthorizedException(error_msg)
        if not driver.is_active:
            raise UnauthorizedException("Account is deactivated")
        if not driver.is_verified:
            raise UnauthorizedException("Account not verified. Please complete registration first.")
        driver.last_login_at = datetime.now(timezone.utc)
        await self.driver_repo.update(driver)
        return self._create_tokens(str(driver.id), UserRole.DRIVER, driver.token_version)

    async def send_driver_login_otp(self, data: DriverPhoneOTPRequest) -> dict:
        phone = normalize_phone(data.phone)
        driver = await self._get_driver_for_phone_otp(phone)
        if not driver:
            raise UnauthorizedException("No driver account found with this mobile number")
        if not driver.is_active:
            raise UnauthorizedException("Account is deactivated")
        if not driver.is_verified:
            raise UnauthorizedException("Account not verified. Please complete registration first.")

        otp = await self._issue_phone_otp(phone)
        driver.otp = otp
        driver.otp_expires_at = datetime.now(timezone.utc) + timedelta(minutes=10) if otp else None
        driver.phone = phone
        await self.driver_repo.update(driver)

        return self._otp_response("OTP sent to your mobile number", otp)

    async def send_driver_phone_otp(self, phone: str) -> dict:
        """Send OTP for phone login — auto-selects login vs signup flow."""
        phone = normalize_phone(phone)
        email = f"{phone.replace('+', '')}@driver.ridebook.app"
        driver = await self.driver_repo.find_for_otp(phone, email)
        if driver and driver.is_verified:
            return await self.send_driver_login_otp(DriverPhoneOTPRequest(phone=phone))
        return await self.send_driver_register_otp(
            DriverRegisterOTPSend(
                phone=phone,
                password="temp123456",
                first_name="Driver",
                last_name="",
            )
        )

    async def verify_driver_phone_otp(self, phone: str, otp: str) -> TokenResponse:
        """Verify phone OTP — auto-selects login vs signup flow."""
        phone = normalize_phone(phone)
        driver = await self._get_driver_for_phone_otp(phone)
        if driver and driver.is_verified:
            return await self.verify_driver_login_otp(
                DriverPhoneOTPVerify(phone=phone, otp=otp)
            )
        return await self.verify_driver_register_otp(
            DriverRegisterOTPVerify(phone=phone, otp=otp)
        )

    async def verify_driver_login_otp(self, data: DriverPhoneOTPVerify) -> TokenResponse:
        phone = normalize_phone(data.phone)
        driver = await self._get_driver_for_phone_otp(phone)
        if not driver:
            raise UnauthorizedException("No driver account found with this mobile number")
        self._validate_phone_otp(phone, data.otp, driver.otp, driver.otp_expires_at)
        if not driver.is_active:
            raise UnauthorizedException("Account is deactivated")

        driver.is_verified = True
        driver.otp = None
        driver.otp_expires_at = None
        driver.last_login_at = datetime.now(timezone.utc)
        await self.driver_repo.update(driver)
        return self._create_tokens(str(driver.id), UserRole.DRIVER, driver.token_version)

    async def login_admin(self, email: str, password: str) -> TokenResponse:
        admin = await self.admin_repo.get_by_email(email)
        if not admin or not verify_password(password, admin.password_hash):
            raise UnauthorizedException("Invalid email or password")
        if not admin.is_active:
            raise UnauthorizedException("Account is deactivated")
        admin.last_login_at = datetime.now(timezone.utc)
        await self.admin_repo.update(admin)
        return self._create_tokens(str(admin.id), UserRole.ADMIN)

    async def verify_otp(self, data: OTPVerify, role: UserRole = UserRole.USER) -> TokenResponse:
        if role == UserRole.USER:
            entity = await self.user_repo.get_by_email(data.email)
        else:
            entity = await self.driver_repo.get_by_email(data.email)

        if not entity:
            raise UnauthorizedException("Account not found")
        if not entity.otp or entity.otp != data.otp:
            raise ValidationException("Invalid OTP")
        if entity.otp_expires_at and entity.otp_expires_at < datetime.now(timezone.utc):
            raise ValidationException("OTP expired")

        entity.is_verified = True
        entity.otp = None
        entity.otp_expires_at = None

        if role == UserRole.USER:
            await self.user_repo.update(entity)
            return self._create_tokens(str(entity.id), UserRole.USER, entity.token_version)
        await self.driver_repo.update(entity)
        return self._create_tokens(str(entity.id), UserRole.DRIVER, entity.token_version)

    async def forgot_password(self, data: ForgotPassword, role: UserRole = UserRole.USER) -> dict:
        if role == UserRole.USER:
            entity = await self.user_repo.get_by_email(data.email)
            repo = self.user_repo
        else:
            entity = await self.driver_repo.get_by_email(data.email)
            repo = self.driver_repo

        if not entity:
            return {"message": "If account exists, OTP has been sent", "success": True}

        entity.otp = generate_otp()
        entity.otp_expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
        await repo.update(entity)
        return {"message": "OTP sent to registered email/phone", "success": True}

    async def reset_password(self, data: ResetPassword, role: UserRole = UserRole.USER) -> dict:
        if role == UserRole.USER:
            entity = await self.user_repo.get_by_email(data.email)
            repo = self.user_repo
        else:
            entity = await self.driver_repo.get_by_email(data.email)
            repo = self.driver_repo

        if not entity:
            raise UnauthorizedException("Account not found")
        if not entity.otp or entity.otp != data.otp:
            raise ValidationException("Invalid OTP")
        if entity.otp_expires_at and entity.otp_expires_at < datetime.now(timezone.utc):
            raise ValidationException("OTP expired")

        entity.password_hash = hash_password(data.new_password)
        entity.otp = None
        entity.otp_expires_at = None
        await repo.update(entity)
        return {"message": "Password reset successfully", "success": True}

    async def refresh_token(self, refresh_token: str) -> TokenResponse:
        try:
            payload = decode_token(refresh_token)
        except ValueError as e:
            raise UnauthorizedException("Invalid refresh token") from e

        if payload.get("type") != "refresh":
            raise UnauthorizedException("Invalid token type")

        role = UserRole(payload["role"])
        subject_id = payload["sub"]
        token_version = int(payload.get("token_version", 1))

        if role == UserRole.USER:
            user = await self.user_repo.get_by_id_active(subject_id)
            if not user or user.token_version != token_version:
                raise UnauthorizedException("Session expired. Please login again.")
            return self._create_tokens(str(user.id), role, user.token_version)

        if role == UserRole.DRIVER:
            driver = await self.driver_repo.get_by_id_active(subject_id)
            if not driver or driver.token_version != token_version:
                raise UnauthorizedException("Session expired. Please login again.")
            return self._create_tokens(str(driver.id), role, driver.token_version)

        return self._create_tokens(subject_id, role, token_version)
