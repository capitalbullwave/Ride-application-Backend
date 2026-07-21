"""Central API router registration."""
from fastapi import APIRouter

from app.api.admin.routes import router as admin_router
from app.api.auth.routes import router as auth_router
from app.api.common.routes import router as common_router
from app.api.driver.routes import router as driver_router
from app.api.public.routes import router as public_router
from app.api.user.routes import router as user_router
from app.corporate.router import router as corporate_router
from app.notifications.router import router as notifications_router
from app.notifications.router import (
    update_driver_device_token,
    update_user_device_token,
)
from app.rides.router import router as rides_router

api_router = APIRouter()

# Spec-aligned prefixes (unified backend)
api_router.include_router(auth_router, prefix="/auth", tags=["Authentication"])
api_router.include_router(user_router, prefix="/users", tags=["Users"])
api_router.include_router(driver_router, prefix="/drivers", tags=["Drivers"])
api_router.include_router(rides_router, prefix="/rides", tags=["Rides"])
api_router.include_router(admin_router, prefix="/admin", tags=["Admin"])
api_router.include_router(corporate_router, prefix="/corporate", tags=["Corporate"])
api_router.include_router(common_router, prefix="/common", tags=["Common"])
api_router.include_router(public_router, prefix="/public", tags=["Public"])
api_router.include_router(notifications_router, prefix="/notifications", tags=["Notifications"])

# Production device-token paths: POST /users/device-token, POST /drivers/device-token
_user_device_token_router = APIRouter()
_user_device_token_router.add_api_route(
    "/device-token",
    update_user_device_token,
    methods=["POST"],
    tags=["Users"],
)
api_router.include_router(_user_device_token_router, prefix="/users")

_driver_device_token_router = APIRouter()
_driver_device_token_router.add_api_route(
    "/device-token",
    update_driver_device_token,
    methods=["POST"],
    tags=["Drivers"],
)
api_router.include_router(_driver_device_token_router, prefix="/drivers")

# Backward compatibility (existing User/Driver panels)
api_router.include_router(user_router, prefix="/user", tags=["User (legacy)"], include_in_schema=False)
api_router.include_router(driver_router, prefix="/driver", tags=["Driver (legacy)"], include_in_schema=False)
api_router.include_router(
    _user_device_token_router, prefix="/user", tags=["User (legacy)"], include_in_schema=False
)
api_router.include_router(
    _driver_device_token_router, prefix="/driver", tags=["Driver (legacy)"], include_in_schema=False
)

__all__ = ["api_router"]
