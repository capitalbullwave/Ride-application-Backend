"""Dynamic driver commission settings."""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.commission.models import CommissionSettings
from app.commission.schemas import (
    CommissionSettingsResponse,
    VehicleCommissionItem,
    VehicleCommissionSettingsResponse,
    VehicleCommissionSettingsUpdate,
)
from app.core.exceptions import ValidationException
from app.vehicles.models import VehicleType


class CommissionService:
    DEFAULT_COMMISSION_PERCENTAGE = 30.0

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_active(self) -> CommissionSettings:
        result = await self.db.execute(
            select(CommissionSettings)
            .options(selectinload(CommissionSettings.updated_by_admin))
            .where(CommissionSettings.is_active.is_(True))
            .order_by(CommissionSettings.created_at.desc())
            .limit(1)
        )
        settings = result.scalar_one_or_none()
        if settings:
            return settings

        settings = CommissionSettings(
            driver_commission_percentage=self.DEFAULT_COMMISSION_PERCENTAGE,
            is_active=True,
        )
        self.db.add(settings)
        await self.db.flush()
        return settings

    async def get_active_percentage(self) -> float:
        settings = await self.get_active()
        return float(settings.driver_commission_percentage)

    async def get_percentage_for_vehicle_type_id(
        self,
        vehicle_type_id: Optional[uuid.UUID],
    ) -> float:
        if vehicle_type_id is not None:
            vehicle_type = await self.db.get(VehicleType, vehicle_type_id)
            if vehicle_type and vehicle_type.driver_commission_percentage is not None:
                return float(vehicle_type.driver_commission_percentage)
        return await self.get_active_percentage()

    async def get_settings_response(self) -> CommissionSettingsResponse:
        settings = await self.get_active()
        admin_name = None
        if settings.updated_by_admin:
            admin = settings.updated_by_admin
            admin_name = f"{admin.first_name} {admin.last_name}".strip() or admin.email
        return CommissionSettingsResponse(
            id=settings.id,
            driver_commission_percentage=settings.driver_commission_percentage,
            is_active=settings.is_active,
            updated_by=settings.updated_by,
            updated_by_name=admin_name,
            created_at=settings.created_at,
            updated_at=settings.updated_at,
        )

    async def get_vehicle_settings_response(self) -> VehicleCommissionSettingsResponse:
        global_settings = await self.get_active()
        admin_name = None
        if global_settings.updated_by_admin:
            admin = global_settings.updated_by_admin
            admin_name = f"{admin.first_name} {admin.last_name}".strip() or admin.email

        result = await self.db.execute(
            select(VehicleType).order_by(VehicleType.service_group, VehicleType.display_order, VehicleType.name)
        )
        vehicles = [
            VehicleCommissionItem(
                vehicle_type_id=vt.id,
                name=vt.name,
                slug=vt.slug,
                service_group=vt.service_group or "ride",
                driver_commission_percentage=float(
                    vt.driver_commission_percentage
                    if vt.driver_commission_percentage is not None
                    else global_settings.driver_commission_percentage
                ),
                is_active=vt.is_active,
            )
            for vt in result.scalars().all()
        ]

        return VehicleCommissionSettingsResponse(
            default_commission_percentage=float(global_settings.driver_commission_percentage),
            updated_at=global_settings.updated_at,
            updated_by_name=admin_name,
            vehicles=vehicles,
        )

    async def update_settings(
        self,
        percentage: float,
        admin_id: uuid.UUID,
    ) -> CommissionSettingsResponse:
        if percentage < 0 or percentage > 100:
            raise ValidationException("Commission percentage must be between 0 and 100")

        await self.db.execute(
            update(CommissionSettings).where(CommissionSettings.is_active.is_(True)).values(is_active=False)
        )

        new_settings = CommissionSettings(
            driver_commission_percentage=percentage,
            is_active=True,
            updated_by=admin_id,
        )
        self.db.add(new_settings)
        await self.db.flush()

        result = await self.db.execute(
            select(CommissionSettings)
            .options(selectinload(CommissionSettings.updated_by_admin))
            .where(CommissionSettings.id == new_settings.id)
        )
        saved = result.scalar_one()
        admin_name: Optional[str] = None
        if saved.updated_by_admin:
            admin = saved.updated_by_admin
            admin_name = f"{admin.first_name} {admin.last_name}".strip() or admin.email

        return CommissionSettingsResponse(
            id=saved.id,
            driver_commission_percentage=saved.driver_commission_percentage,
            is_active=saved.is_active,
            updated_by=saved.updated_by,
            updated_by_name=admin_name,
            created_at=saved.created_at,
            updated_at=saved.updated_at,
        )

    async def update_vehicle_settings(
        self,
        data: VehicleCommissionSettingsUpdate,
        admin_id: uuid.UUID,
    ) -> VehicleCommissionSettingsResponse:
        if data.default_commission_percentage is not None:
            await self.update_settings(data.default_commission_percentage, admin_id)

        for item in data.vehicles:
            if item.driver_commission_percentage < 0 or item.driver_commission_percentage > 100:
                raise ValidationException("Commission percentage must be between 0 and 100")

            vehicle_type = await self.db.get(VehicleType, item.vehicle_type_id)
            if not vehicle_type:
                raise ValidationException(f"Vehicle type {item.vehicle_type_id} not found")
            vehicle_type.driver_commission_percentage = item.driver_commission_percentage

        if data.vehicles:
            active = await self.get_active()
            active.updated_by = admin_id

        await self.db.flush()
        return await self.get_vehicle_settings_response()
