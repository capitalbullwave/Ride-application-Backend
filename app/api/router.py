"""Central API router registration."""
from fastapi import APIRouter

from app.api.admin.routes import router as admin_router
from app.api.auth.routes import router as auth_router
from app.api.common.routes import router as common_router
from app.api.driver.routes import router as driver_router
from app.api.public.routes import router as public_router
from app.api.user.routes import router as user_router
from app.rides.router import router as rides_router

api_router = APIRouter()

# Spec-aligned prefixes (unified backend)
api_router.include_router(auth_router, prefix="/auth", tags=["Authentication"])
api_router.include_router(user_router, prefix="/users", tags=["Users"])
api_router.include_router(driver_router, prefix="/drivers", tags=["Drivers"])
api_router.include_router(rides_router, prefix="/rides", tags=["Rides"])
api_router.include_router(admin_router, prefix="/admin", tags=["Admin"])
api_router.include_router(common_router, prefix="/common", tags=["Common"])
api_router.include_router(public_router, prefix="/public", tags=["Public"])

# Backward compatibility (existing User/Driver panels)
api_router.include_router(user_router, prefix="/user", tags=["User (legacy)"], include_in_schema=False)
api_router.include_router(driver_router, prefix="/driver", tags=["Driver (legacy)"], include_in_schema=False)

__all__ = ["api_router"]