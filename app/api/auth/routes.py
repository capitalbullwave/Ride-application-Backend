"""Unified authentication API — /api/v1/auth/*"""
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth.dependencies import get_current_user
from app.api.auth.service import AuthApiService
from app.auth.dependencies import security
from app.core.constants import UserRole
from app.core.exceptions import ValidationException
from app.core.security import hash_password, verify_password
from app.database.session import get_db
from app.models import User
from app.repositories.user_repository import UserRepository
from app.schemas.common import MessageResponse, TokenResponse
from app.schemas.driver import (
    DriverLogin,
    DriverPhoneOTPRequest,
    DriverPhoneOTPVerify,
    DriverRegister,
    DriverRegisterOTPSend,
    DriverRegisterOTPVerify,
)
from app.schemas.user import (
    ChangePassword,
    ForgotPassword,
    OTPVerify,
    RefreshTokenRequest,
    ResetPassword,
    UserLogin,
    UserPhoneOTPRequest,
    UserPhoneOTPVerify,
    UserRegister,
    UserRegisterOTPSend,
    UserRegisterOTPVerify,
    UserResponse,
    UserUpdate,
)

router = APIRouter(tags=["Authentication"])


class UnifiedLoginRequest(BaseModel):
    role: Literal["user", "driver", "admin"] = "user"
    email: EmailStr | None = None
    phone: str | None = None
    password: str = Field(min_length=6)


class UnifiedRegisterRequest(BaseModel):
    role: Literal["user", "driver"] = "user"
    email: EmailStr | None = None
    phone: str
    password: str = Field(min_length=6)
    first_name: str = "User"
    last_name: str = ""


class UnifiedOtpSendRequest(BaseModel):
    role: Literal["user", "driver"] = "user"
    phone: str
    purpose: Literal["login", "register"] = "login"


class UnifiedOtpVerifyRequest(BaseModel):
    role: Literal["user", "driver"] = "user"
    phone: str
    otp: str = Field(..., min_length=4, max_length=6)
    purpose: Literal["login", "register"] = "login"
    first_name: str | None = None
    last_name: str | None = None
    email: EmailStr | None = None
    password: str | None = None


@router.post("/login", response_model=TokenResponse)
async def login(data: UnifiedLoginRequest, db: AsyncSession = Depends(get_db)):
    svc = AuthApiService(db)
    if data.role == "admin":
        if not data.email:
            raise ValidationException("Email required for admin login")
        return await svc.login_admin(data.email, data.password)
    if data.role == "driver":
        return await svc.login_driver(DriverLogin(phone=data.phone or "", password=data.password, email=data.email))
    return await svc.login_user(UserLogin(phone=data.phone, email=data.email, password=data.password))


@router.post("/register", response_model=TokenResponse)
async def register(data: UnifiedRegisterRequest, db: AsyncSession = Depends(get_db)):
    svc = AuthApiService(db)
    if data.role == "driver":
        return await svc.register_driver(
            DriverRegister(
                email=data.email or f"{data.phone.replace('+', '')}@ridebook.app",
                phone=data.phone,
                password=data.password,
                first_name=data.first_name,
                last_name=data.last_name,
            )
        )
    return await svc.register_user(
        UserRegister(
            email=data.email or f"{data.phone.replace('+', '')}@ridebook.app",
            phone=data.phone,
            password=data.password,
            first_name=data.first_name,
            last_name=data.last_name,
        )
    )


@router.post("/logout", response_model=MessageResponse)
async def logout(
    request: Request,
    credentials: Annotated[Optional[HTTPAuthorizationCredentials], Depends(security)] = None,
):
    """Idempotent logout — never 401s; clients may call with expired/invalid tokens."""
    from app.core.logging import get_logger
    from app.core.security import decode_token

    logger = get_logger(__name__)
    has_auth_header = credentials is not None and bool(credentials.credentials)
    user_id = None
    role = None
    token_valid = False

    if has_auth_header:
        try:
            payload = decode_token(credentials.credentials)
            user_id = payload.get("sub")
            role = payload.get("role")
            token_valid = payload.get("type") == "access"
        except ValueError:
            token_valid = False

    logger.info(
        "auth_logout",
        has_authorization_header=has_auth_header,
        token_valid=token_valid,
        user_id=str(user_id) if user_id else None,
        role=role,
        client=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", "")[:120],
    )
    return MessageResponse(message="Logged out successfully")


@router.post("/refresh-token", response_model=TokenResponse)
async def refresh_token(data: RefreshTokenRequest, db: AsyncSession = Depends(get_db)):
    return await AuthApiService(db).refresh_token(data.refresh_token)


@router.post("/forgot-password", response_model=MessageResponse)
async def forgot_password(
    data: ForgotPassword,
    role: Literal["user", "driver"] = "user",
    db: AsyncSession = Depends(get_db),
):
    user_role = UserRole.DRIVER if role == "driver" else UserRole.USER
    result = await AuthApiService(db).forgot_password(data, user_role)
    return MessageResponse(**result)


@router.post("/reset-password", response_model=MessageResponse)
async def reset_password(
    data: ResetPassword,
    role: Literal["user", "driver"] = "user",
    db: AsyncSession = Depends(get_db),
):
    user_role = UserRole.DRIVER if role == "driver" else UserRole.USER
    result = await AuthApiService(db).reset_password(data, user_role)
    return MessageResponse(**result)


@router.post("/send-otp", response_model=MessageResponse)
async def send_otp(data: UnifiedOtpSendRequest, db: AsyncSession = Depends(get_db)):
    svc = AuthApiService(db)
    if data.role == "driver":
        if data.purpose == "register":
            result = await svc.send_driver_register_otp(
                DriverRegisterOTPSend(
                    phone=data.phone,
                    password="temp123456",
                    first_name="Driver",
                    last_name="",
                )
            )
        else:
            result = await svc.send_driver_phone_otp(data.phone)
    elif data.purpose == "register":
        result = await svc.send_user_register_otp(
            UserRegisterOTPSend(
                phone=data.phone,
                password="temp123456",
                first_name="User",
                last_name="",
            )
        )
    else:
        result = await svc.send_user_phone_otp(data.phone)
    return MessageResponse(**result)


@router.post("/verify-otp", response_model=TokenResponse)
async def verify_otp(data: UnifiedOtpVerifyRequest, db: AsyncSession = Depends(get_db)):
    svc = AuthApiService(db)
    if data.role == "driver":
        if data.purpose == "register":
            return await svc.verify_driver_register_otp(
                DriverRegisterOTPVerify(phone=data.phone, otp=data.otp)
            )
        return await svc.verify_driver_phone_otp(data.phone, data.otp)
    if data.purpose == "register":
        return await svc.verify_user_register_otp(
            UserRegisterOTPVerify(phone=data.phone, otp=data.otp)
        )
    return await svc.verify_user_phone_otp(data.phone, data.otp)


# OTP helpers kept for backward compatibility (hidden from Swagger)
@router.post("/register/otp/send", response_model=MessageResponse, include_in_schema=False)
async def register_otp_send(data: UserRegisterOTPSend, db: AsyncSession = Depends(get_db)):
    result = await AuthApiService(db).send_user_register_otp(data)
    return MessageResponse(**result)


@router.post("/register/otp/verify", response_model=TokenResponse, include_in_schema=False)
async def register_otp_verify(data: UserRegisterOTPVerify, db: AsyncSession = Depends(get_db)):
    return await AuthApiService(db).verify_user_register_otp(data)


@router.post("/login/otp/send", response_model=MessageResponse, include_in_schema=False)
async def login_otp_send(data: UserPhoneOTPRequest, db: AsyncSession = Depends(get_db)):
    result = await AuthApiService(db).send_user_login_otp(data)
    return MessageResponse(**result)


@router.post("/login/otp/verify", response_model=TokenResponse, include_in_schema=False)
async def login_otp_verify(data: UserPhoneOTPVerify, db: AsyncSession = Depends(get_db)):
    return await AuthApiService(db).verify_user_login_otp(data)


@router.get("/me", response_model=UserResponse)
async def get_me(user: Annotated[User, Depends(get_current_user)]):
    return UserResponse.model_validate(user)


@router.put("/me", response_model=UserResponse)
async def update_me(
    data: UserUpdate,
    user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
):
    repo = UserRepository(db)
    for field in ("first_name", "last_name", "profile_photo", "emergency_contact_name", "emergency_contact_phone", "fcm_token"):
        value = getattr(data, field, None)
        if value is not None:
            setattr(user, field, value)
    await repo.update(user)
    return UserResponse.model_validate(user)


@router.post("/change-password", response_model=MessageResponse)
async def change_password(
    data: ChangePassword,
    user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
):
    if not verify_password(data.current_password, user.password_hash):
        raise ValidationException("Current password is incorrect")
    user.password_hash = hash_password(data.new_password)
    await UserRepository(db).update(user)
    return MessageResponse(message="Password changed successfully")
