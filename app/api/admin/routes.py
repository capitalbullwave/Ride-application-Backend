"""Admin module router — mounts all admin endpoints under /api/v1/admin."""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin._core import router as core_router
from app.api.admin._extended import router as extended_router
from app.auth.dependencies import get_current_admin
from app.core.constants import KYCStatus
from app.core.exceptions import NotFoundException
from app.database.session import get_db
from app.models import AdminUser, Driver, Notification, User
from sqlalchemy import select

router = APIRouter(tags=["Admin"])
router.include_router(core_router)
router.include_router(extended_router)


class ApproveDriverRequest(BaseModel):
    driver_id: UUID


class BlockUserRequest(BaseModel):
    user_id: UUID


class SendNotificationRequest(BaseModel):
    title: str
    message: str
    target: str = "all_users"


@router.get("/dashboard")
async def dashboard_summary(
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: AsyncSession = Depends(get_db),
):
    """Alias — returns dashboard stats summary."""
    from app.api.admin._extended import dashboard_stats

    return await dashboard_stats(admin, db)


@router.get("/payments")
async def list_payments(
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: AsyncSession = Depends(get_db),
    page: int = 1,
    limit: int = 50,
):
    """Alias for finance transactions."""
    from app.api.admin._extended import finance_transactions

    return await finance_transactions(admin, db, type=None, page=page, limit=limit)


@router.get("/reports")
async def reports(
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: AsyncSession = Depends(get_db),
):
    from app.api.admin._extended import dashboard_stats, dashboard_charts

    stats = await dashboard_stats(admin, db)
    charts = await dashboard_charts(admin, db)
    return {"stats": stats, "charts": charts}


@router.put("/approve-driver")
async def approve_driver(
    data: ApproveDriverRequest,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: AsyncSession = Depends(get_db),
):
    from app.api.admin._core import approve_driver as _approve

    return await _approve(data.driver_id, admin, db)


@router.put("/block-user")
async def block_user(
    data: BlockUserRequest,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: AsyncSession = Depends(get_db),
):
    from app.api.admin._core import block_user as _block

    return await _block(data.user_id, admin, db)


@router.post("/send-notification")
async def send_notification(
    data: SendNotificationRequest,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: AsyncSession = Depends(get_db),
):
    from app.notifications.schemas import AdminBroadcastRequest
    from app.notifications.router import admin_broadcast

    # Prefer the production FCM broadcast path while keeping this admin alias.
    result = await admin_broadcast(
        AdminBroadcastRequest(
            title=data.title,
            body=data.message,
            target=data.target if data.target in {
                "all_users",
                "all_drivers",
                "city",
                "user",
                "driver",
                "promotion",
                "news",
                "maintenance",
            } else "all_users",
        ),
        admin,
        db,
    )
    return {"title": data.title, "message": data.message, "target": data.target, **result}


@router.put("/pricing")
async def update_pricing(
    data: dict,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: AsyncSession = Depends(get_db),
):
    from app.api.admin._extended import update_settings

    return await update_settings(data, admin, db)


@router.post("/vehicle-categories")
async def create_vehicle_category_route(
    data: dict,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: AsyncSession = Depends(get_db),
):
    from app.api.admin._extended import create_vehicle_category

    return await create_vehicle_category(data, admin, db)


@router.delete("/vehicle-categories/{category_id}")
async def delete_vehicle_category_route(
    category_id: UUID,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    db: AsyncSession = Depends(get_db),
):
    from app.api.admin._extended import delete_vehicle_category

    return await delete_vehicle_category(category_id, admin, db)
