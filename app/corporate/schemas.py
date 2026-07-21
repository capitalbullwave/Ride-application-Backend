"""Corporate module Pydantic schemas."""
import uuid
from datetime import date, datetime, time
from typing import List, Optional

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.schemas.common import BaseSchema


class CompanyRegisterRequest(BaseModel):
    company_name: str = Field(..., min_length=2, max_length=200)
    gst_number: Optional[str] = Field(None, max_length=20)
    pan_number: Optional[str] = Field(None, max_length=20)
    website: Optional[str] = Field(None, max_length=255)
    industry: Optional[str] = Field(None, max_length=100)
    company_size: Optional[str] = Field(None, max_length=50)
    address: Optional[str] = Field(None, max_length=500)
    city: Optional[str] = Field(None, max_length=100)
    state: Optional[str] = Field(None, max_length=100)
    country: str = Field(default="India", max_length=100)
    contact_person: str = Field(..., min_length=2, max_length=150)
    email: EmailStr
    phone: str = Field(..., min_length=8, max_length=20)
    password: str = Field(..., min_length=8, max_length=128)


class CompanyLoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1, max_length=128)


class CompanyUpdateRequest(BaseModel):
    company_name: Optional[str] = Field(None, min_length=2, max_length=200)
    gst_number: Optional[str] = Field(None, max_length=20)
    pan_number: Optional[str] = Field(None, max_length=20)
    website: Optional[str] = Field(None, max_length=255)
    industry: Optional[str] = Field(None, max_length=100)
    company_size: Optional[str] = Field(None, max_length=50)
    address: Optional[str] = Field(None, max_length=500)
    city: Optional[str] = Field(None, max_length=100)
    state: Optional[str] = Field(None, max_length=100)
    country: Optional[str] = Field(None, max_length=100)
    contact_person: Optional[str] = Field(None, min_length=2, max_length=150)
    phone: Optional[str] = Field(None, min_length=8, max_length=20)
    credit_limit: Optional[float] = Field(None, ge=0)


class CompanyRejectRequest(BaseModel):
    reason: Optional[str] = Field(None, max_length=500)


class CompanyResponse(BaseSchema):
    id: uuid.UUID
    company_name: str
    company_code: str
    gst_number: Optional[str] = None
    pan_number: Optional[str] = None
    website: Optional[str] = None
    industry: Optional[str] = None
    company_size: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: str
    contact_person: str
    email: str
    phone: str
    credit_limit: float
    wallet_balance: float
    status: str
    rejection_reason: Optional[str] = None
    approved_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class CompanyListItem(CompanyResponse):
    employee_count: int = 0
    today_rides: int = 0
    monthly_spend: float = 0.0


class CompanyDetailResponse(CompanyListItem):
    outstanding_amount: float = 0.0
    current_month_spend: float = 0.0
    total_employees: int = 0
    total_rides: int = 0


class EmployeeCreateRequest(BaseModel):
    user_id: Optional[uuid.UUID] = None
    phone: Optional[str] = Field(None, min_length=8, max_length=20)
    email: Optional[EmailStr] = None
    employee_code: str = Field(..., min_length=1, max_length=50)
    department: Optional[str] = Field(None, max_length=100)
    designation: Optional[str] = Field(None, max_length=100)
    ride_limit: Optional[float] = Field(None, ge=0)


class EmployeeUpdateRequest(BaseModel):
    employee_code: Optional[str] = Field(None, min_length=1, max_length=50)
    department: Optional[str] = Field(None, max_length=100)
    designation: Optional[str] = Field(None, max_length=100)
    ride_limit: Optional[float] = Field(None, ge=0)
    status: Optional[str] = Field(None, pattern="^(ACTIVE|INACTIVE)$")


class EmployeeResponse(BaseSchema):
    id: uuid.UUID
    company_id: uuid.UUID
    user_id: uuid.UUID
    employee_code: str
    department: Optional[str] = None
    designation: Optional[str] = None
    ride_limit: Optional[float] = None
    status: str
    joined_at: datetime
    employee_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    ride_count: int = 0
    monthly_spend: float = 0.0


class PolicyUpsertRequest(BaseModel):
    allowed_vehicle_types: Optional[List[str]] = None
    max_ride_amount: Optional[float] = Field(None, ge=0)
    office_start_time: Optional[time] = None
    office_end_time: Optional[time] = None
    working_days: Optional[List[int]] = None
    approval_required: bool = False
    purpose_required: bool = False

    @field_validator("working_days")
    @classmethod
    def validate_working_days(cls, value: Optional[List[int]]) -> Optional[List[int]]:
        if value is None:
            return value
        for day in value:
            if day < 0 or day > 6:
                raise ValueError("working_days must be integers 0-6 (Mon-Sun)")
        return value


class PolicyResponse(BaseSchema):
    id: uuid.UUID
    company_id: uuid.UUID
    allowed_vehicle_types: Optional[List[str]] = None
    max_ride_amount: Optional[float] = None
    office_start_time: Optional[time] = None
    office_end_time: Optional[time] = None
    working_days: Optional[List[int]] = None
    approval_required: bool
    purpose_required: bool
    created_at: datetime
    updated_at: datetime


class CorporateMembershipResponse(BaseModel):
    is_corporate_member: bool = False
    company_id: Optional[uuid.UUID] = None
    company_name: Optional[str] = None
    company_status: Optional[str] = None
    employee_id: Optional[uuid.UUID] = None
    employee_code: Optional[str] = None
    department: Optional[str] = None
    designation: Optional[str] = None
    employee_status: Optional[str] = None
    can_book_corporate: bool = False


class CorporateDashboardResponse(BaseModel):
    total_companies: int = 0
    pending_companies: int = 0
    approved_companies: int = 0
    active_employees: int = 0
    today_corporate_rides: int = 0
    monthly_corporate_revenue: float = 0.0
    pending_approvals: List[CompanyListItem] = Field(default_factory=list)
    ride_trend: List[dict] = Field(default_factory=list)
    top_companies: List[dict] = Field(default_factory=list)
    monthly_ride_count: List[dict] = Field(default_factory=list)
    monthly_spending: List[dict] = Field(default_factory=list)


class CorporateReportFilters(BaseModel):
    company_id: Optional[uuid.UUID] = None
    employee_id: Optional[uuid.UUID] = None
    from_date: Optional[date] = None
    to_date: Optional[date] = None


class CorporateRideHistoryItem(BaseSchema):
    id: uuid.UUID
    public_id: str
    company_id: Optional[uuid.UUID] = None
    company_name: Optional[str] = None
    employee_id: Optional[uuid.UUID] = None
    employee_name: Optional[str] = None
    employee_code: Optional[str] = None
    status: str
    ride_type: str
    payment_source: str
    estimated_fare: float
    final_fare: Optional[float] = None
    pickup_address: str
    dropoff_address: str
    created_at: datetime
    completed_at: Optional[datetime] = None
