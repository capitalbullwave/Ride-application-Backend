import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field

from app.schemas.common import BaseSchema


class AdminLogin(BaseModel):
    email: EmailStr
    password: str


class AdminUserCreate(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8)
    first_name: str
    last_name: str
    role_id: uuid.UUID


class AdminUserResponse(BaseSchema):
    id: uuid.UUID
    email: str
    first_name: str
    last_name: str
    role_id: uuid.UUID
    is_active: bool
    last_login_at: Optional[datetime] = None
    created_at: datetime


class AdminRoleCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=50)
    description: Optional[str] = None
    permission_ids: list[uuid.UUID] = []


class AdminRoleResponse(BaseSchema):
    id: uuid.UUID
    name: str
    description: Optional[str] = None
    is_active: bool


class DashboardStats(BaseModel):
    total_users: int
    total_drivers: int
    active_rides: int
    completed_rides_today: int
    total_revenue_today: float
    total_revenue_month: float
    pending_kyc: int
    pending_vehicles: int
    open_tickets: int


class AnalyticsResponse(BaseModel):
    rides_by_day: list[dict]
    revenue_by_day: list[dict]
    rides_by_status: list[dict]
    top_drivers: list[dict]
