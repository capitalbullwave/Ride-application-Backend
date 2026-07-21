"""Corporate B2B business logic."""
from __future__ import annotations

import secrets
import string
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import (
    CompanyEmployeeStatus,
    CompanyStatus,
    RideStatus,
    UserRole,
)
from app.core.exceptions import (
    ConflictException,
    ForbiddenException,
    NotFoundException,
    UnauthorizedException,
    ValidationException,
)
from app.core.security import (
    create_access_token,
    create_refresh_token,
    hash_password,
    verify_password,
)
from app.corporate.models import Company, CompanyEmployee, CompanyPolicy
from app.corporate.repository import CorporateRepository
from app.corporate.schemas import (
    CompanyDetailResponse,
    CompanyListItem,
    CompanyLoginRequest,
    CompanyRegisterRequest,
    CompanyRejectRequest,
    CompanyResponse,
    CompanyUpdateRequest,
    CorporateDashboardResponse,
    CorporateMembershipResponse,
    CorporateRideHistoryItem,
    EmployeeCreateRequest,
    EmployeeResponse,
    EmployeeUpdateRequest,
    PolicyResponse,
    PolicyUpsertRequest,
)
from app.corporate.validators import normalize_gst, normalize_pan
from app.schemas.common import TokenResponse


def _generate_company_code(name: str) -> str:
    prefix = "".join(ch for ch in name.upper() if ch.isalnum())[:4] or "CORP"
    suffix = "".join(secrets.choice(string.digits) for _ in range(4))
    return f"{prefix}{suffix}"


def _month_start(now: Optional[datetime] = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _today_start(now: Optional[datetime] = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


class CorporateService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = CorporateRepository(db)

    # ── Auth / registration ────────────────────────────────────

    async def register_company(self, data: CompanyRegisterRequest) -> CompanyResponse:
        existing = await self.repo.get_company_by_email(data.email)
        if existing:
            raise ConflictException("A company with this email already exists")

        code = _generate_company_code(data.company_name)
        while await self.repo.get_company_by_code(code):
            code = _generate_company_code(data.company_name)

        company = Company(
            company_name=data.company_name.strip(),
            company_code=code,
            gst_number=normalize_gst(data.gst_number),
            pan_number=normalize_pan(data.pan_number),
            website=data.website,
            industry=data.industry,
            company_size=data.company_size,
            address=data.address,
            city=data.city,
            state=data.state,
            country=data.country or "India",
            contact_person=data.contact_person.strip(),
            email=data.email.lower().strip(),
            phone=data.phone.strip(),
            password_hash=hash_password(data.password),
            status=CompanyStatus.PENDING.value,
        )
        company = await self.repo.create_company(company)

        # Default policy scaffold for later configuration
        policy = CompanyPolicy(
            company_id=company.id,
            working_days=[0, 1, 2, 3, 4],
            approval_required=False,
            purpose_required=False,
        )
        await self.repo.upsert_policy(policy)
        return CompanyResponse.model_validate(company)

    async def login_company(self, data: CompanyLoginRequest) -> TokenResponse:
        company = await self.repo.get_company_by_email(data.email)
        if not company or not verify_password(data.password, company.password_hash):
            raise UnauthorizedException("Invalid email or password")
        if company.status == CompanyStatus.SUSPENDED.value:
            raise ForbiddenException("Company account is suspended")
        if company.status == CompanyStatus.REJECTED.value:
            raise ForbiddenException("Company registration was rejected")

        return TokenResponse(
            access_token=create_access_token(
                str(company.id),
                UserRole.COMPANY,
                company.token_version,
                extra_claims={"company_id": str(company.id)},
            ),
            refresh_token=create_refresh_token(
                str(company.id), UserRole.COMPANY, company.token_version
            ),
        )

    async def get_company_profile(self, company_id: UUID) -> CompanyDetailResponse:
        return await self.get_company_details(company_id)

    # ── Company CRUD / admin actions ───────────────────────────

    async def update_company(
        self, company_id: UUID, data: CompanyUpdateRequest
    ) -> CompanyResponse:
        company = await self.repo.get_company(company_id)
        if not company:
            raise NotFoundException("Company not found")
        payload = data.model_dump(exclude_unset=True)
        if "gst_number" in payload:
            payload["gst_number"] = normalize_gst(payload.get("gst_number"))
        if "pan_number" in payload:
            payload["pan_number"] = normalize_pan(payload.get("pan_number"))
        for key, value in payload.items():
            setattr(company, key, value)
        await self.db.flush()
        await self.db.refresh(company)
        return CompanyResponse.model_validate(company)

    async def approve_company(self, company_id: UUID, admin_id: UUID) -> CompanyResponse:
        company = await self.repo.get_company(company_id)
        if not company:
            raise NotFoundException("Company not found")
        company.status = CompanyStatus.APPROVED.value
        company.approved_by = admin_id
        company.approved_at = datetime.now(timezone.utc)
        company.rejection_reason = None
        await self.db.flush()
        await self.db.refresh(company)
        return CompanyResponse.model_validate(company)

    async def reject_company(
        self, company_id: UUID, data: CompanyRejectRequest
    ) -> CompanyResponse:
        company = await self.repo.get_company(company_id)
        if not company:
            raise NotFoundException("Company not found")
        company.status = CompanyStatus.REJECTED.value
        company.rejection_reason = data.reason
        company.approved_at = None
        await self.db.flush()
        await self.db.refresh(company)
        return CompanyResponse.model_validate(company)

    async def suspend_company(self, company_id: UUID) -> CompanyResponse:
        company = await self.repo.get_company(company_id)
        if not company:
            raise NotFoundException("Company not found")
        company.status = CompanyStatus.SUSPENDED.value
        company.token_version = int(company.token_version or 1) + 1
        await self.db.flush()
        await self.db.refresh(company)
        return CompanyResponse.model_validate(company)

    async def delete_company(self, company_id: UUID) -> None:
        company = await self.repo.get_company(company_id)
        if not company:
            raise NotFoundException("Company not found")
        await self.db.delete(company)
        await self.db.flush()

    async def _enrich_company(self, company: Company) -> CompanyListItem:
        today = _today_start()
        month = _month_start()
        employee_count = await self.repo.count_active_employees(company.id)
        today_rides = await self.repo.count_corporate_rides(company_id=company.id, since=today)
        monthly_spend = await self.repo.sum_corporate_spend(company_id=company.id, since=month)
        base = CompanyResponse.model_validate(company).model_dump()
        return CompanyListItem(
            **base,
            employee_count=employee_count,
            today_rides=today_rides,
            monthly_spend=round(monthly_spend, 2),
        )

    async def list_companies(
        self,
        *,
        status: Optional[str] = None,
        search: Optional[str] = None,
        page: int = 1,
        limit: int = 50,
    ) -> dict:
        companies, total = await self.repo.list_companies(
            status=status, search=search, page=page, limit=limit
        )
        items = [await self._enrich_company(c) for c in companies]
        return {
            "items": items,
            "total": total,
            "page": page,
            "limit": limit,
        }

    async def get_company_details(self, company_id: UUID) -> CompanyDetailResponse:
        company = await self.repo.get_company(company_id)
        if not company:
            raise NotFoundException("Company not found")
        month = _month_start()
        enriched = await self._enrich_company(company)
        total_rides = await self.repo.count_corporate_rides(company_id=company.id)
        current_month_spend = enriched.monthly_spend
        outstanding = max(0.0, current_month_spend - float(company.wallet_balance or 0))
        return CompanyDetailResponse(
            **enriched.model_dump(),
            outstanding_amount=round(outstanding, 2),
            current_month_spend=round(current_month_spend, 2),
            total_employees=enriched.employee_count,
            total_rides=total_rides,
        )

    # ── Employees ──────────────────────────────────────────────

    def _map_employee(
        self,
        emp: CompanyEmployee,
        *,
        ride_count: int = 0,
        monthly_spend: float = 0.0,
    ) -> EmployeeResponse:
        user = emp.user
        name = None
        phone = None
        email = None
        if user:
            name = f"{user.first_name} {user.last_name}".strip()
            phone = user.phone
            email = user.email
        return EmployeeResponse(
            id=emp.id,
            company_id=emp.company_id,
            user_id=emp.user_id,
            employee_code=emp.employee_code,
            department=emp.department,
            designation=emp.designation,
            ride_limit=emp.ride_limit,
            status=emp.status,
            joined_at=emp.joined_at,
            employee_name=name,
            phone=phone,
            email=email,
            ride_count=ride_count,
            monthly_spend=round(monthly_spend, 2),
        )

    async def add_employee(
        self, company_id: UUID, data: EmployeeCreateRequest
    ) -> EmployeeResponse:
        company = await self.repo.get_company(company_id)
        if not company:
            raise NotFoundException("Company not found")

        user = await self.repo.resolve_user(
            user_id=data.user_id, phone=data.phone, email=str(data.email) if data.email else None
        )
        if not user:
            raise NotFoundException("User not found. Employee must already have a user account.")

        existing_membership = await self.repo.get_membership_for_user(user.id)
        if existing_membership and existing_membership.company_id != company_id:
            raise ConflictException("User already belongs to another company")

        existing = await self.repo.find_employee(company_id, user.id)
        if existing and existing.status != CompanyEmployeeStatus.REMOVED.value:
            raise ConflictException("Employee already linked to this company")

        if existing:
            existing.employee_code = data.employee_code.strip()
            existing.department = data.department
            existing.designation = data.designation
            existing.ride_limit = data.ride_limit
            existing.status = CompanyEmployeeStatus.ACTIVE.value
            existing.joined_at = datetime.now(timezone.utc)
            await self.db.flush()
            await self.db.refresh(existing)
            emp = existing
        else:
            emp = CompanyEmployee(
                company_id=company_id,
                user_id=user.id,
                employee_code=data.employee_code.strip(),
                department=data.department,
                designation=data.designation,
                ride_limit=data.ride_limit,
                status=CompanyEmployeeStatus.ACTIVE.value,
                joined_at=datetime.now(timezone.utc),
            )
            emp = await self.repo.create_employee(emp)

        emp = await self.repo.get_employee(emp.id)
        return self._map_employee(emp)

    async def update_employee(
        self, company_id: UUID, employee_id: UUID, data: EmployeeUpdateRequest
    ) -> EmployeeResponse:
        emp = await self.repo.get_employee(employee_id)
        if not emp or emp.company_id != company_id:
            raise NotFoundException("Employee not found")
        payload = data.model_dump(exclude_unset=True)
        for key, value in payload.items():
            setattr(emp, key, value)
        await self.db.flush()
        emp = await self.repo.get_employee(employee_id)
        month = _month_start()
        ride_count = await self.repo.count_corporate_rides(employee_id=employee_id)
        monthly_spend = await self.repo.sum_corporate_spend(employee_id=employee_id, since=month)
        return self._map_employee(emp, ride_count=ride_count, monthly_spend=monthly_spend)

    async def set_employee_status(
        self, company_id: UUID, employee_id: UUID, status: str
    ) -> EmployeeResponse:
        return await self.update_employee(
            company_id, employee_id, EmployeeUpdateRequest(status=status)
        )

    async def remove_employee(self, company_id: UUID, employee_id: UUID) -> None:
        emp = await self.repo.get_employee(employee_id)
        if not emp or emp.company_id != company_id:
            raise NotFoundException("Employee not found")
        emp.status = CompanyEmployeeStatus.REMOVED.value
        await self.db.flush()

    async def list_employees(
        self,
        company_id: UUID,
        *,
        status: Optional[str] = None,
        search: Optional[str] = None,
        page: int = 1,
        limit: int = 50,
    ) -> dict:
        employees, total = await self.repo.list_employees(
            company_id, status=status, search=search, page=page, limit=limit
        )
        month = _month_start()
        items = []
        for emp in employees:
            ride_count = await self.repo.count_corporate_rides(employee_id=emp.id)
            monthly_spend = await self.repo.sum_corporate_spend(employee_id=emp.id, since=month)
            items.append(self._map_employee(emp, ride_count=ride_count, monthly_spend=monthly_spend))
        return {"items": items, "total": total, "page": page, "limit": limit}

    # ── Policies ───────────────────────────────────────────────

    async def get_policy(self, company_id: UUID) -> PolicyResponse:
        policy = await self.repo.get_policy(company_id)
        if not policy:
            raise NotFoundException("Ride policy not found")
        return PolicyResponse.model_validate(policy)

    async def upsert_policy(
        self, company_id: UUID, data: PolicyUpsertRequest
    ) -> PolicyResponse:
        company = await self.repo.get_company(company_id)
        if not company:
            raise NotFoundException("Company not found")
        policy = await self.repo.get_policy(company_id)
        payload = data.model_dump()
        if policy is None:
            policy = CompanyPolicy(company_id=company_id, **payload)
        else:
            for key, value in payload.items():
                setattr(policy, key, value)
        policy = await self.repo.upsert_policy(policy)
        return PolicyResponse.model_validate(policy)

    # ── User membership ────────────────────────────────────────

    async def get_user_membership(self, user_id: UUID) -> CorporateMembershipResponse:
        membership = await self.repo.get_membership_for_user(user_id)
        if not membership or not membership.company:
            return CorporateMembershipResponse(is_corporate_member=False)
        company = membership.company
        can_book = (
            membership.status == CompanyEmployeeStatus.ACTIVE.value
            and company.status == CompanyStatus.APPROVED.value
        )
        return CorporateMembershipResponse(
            is_corporate_member=True,
            company_id=company.id,
            company_name=company.company_name,
            company_status=company.status,
            employee_id=membership.id,
            employee_code=membership.employee_code,
            department=membership.department,
            designation=membership.designation,
            employee_status=membership.status,
            can_book_corporate=can_book,
        )

    # ── Dashboard / reports / rides ────────────────────────────

    async def dashboard(self) -> CorporateDashboardResponse:
        today = _today_start()
        month = _month_start()
        trend_since = today - timedelta(days=13)

        pending, _ = await self.repo.list_companies(
            status=CompanyStatus.PENDING.value, page=1, limit=10
        )
        pending_items = [await self._enrich_company(c) for c in pending]
        monthly_rides, monthly_spend = await self.repo.monthly_series(6)

        return CorporateDashboardResponse(
            total_companies=await self.repo.count_companies(),
            pending_companies=await self.repo.count_companies(CompanyStatus.PENDING.value),
            approved_companies=await self.repo.count_companies(CompanyStatus.APPROVED.value),
            active_employees=await self.repo.count_active_employees(),
            today_corporate_rides=await self.repo.count_corporate_rides(since=today),
            monthly_corporate_revenue=round(
                await self.repo.sum_corporate_spend(since=month), 2
            ),
            pending_approvals=pending_items,
            ride_trend=await self.repo.ride_trend_daily(trend_since),
            top_companies=await self.repo.top_companies_by_spend(month),
            monthly_ride_count=monthly_rides,
            monthly_spending=monthly_spend,
        )

    async def list_corporate_rides(
        self,
        *,
        company_id: Optional[UUID] = None,
        employee_id: Optional[UUID] = None,
        status: Optional[str] = None,
        page: int = 1,
        limit: int = 50,
    ) -> dict:
        rides, total = await self.repo.list_corporate_rides(
            company_id=company_id,
            employee_id=employee_id,
            status=status,
            page=page,
            limit=limit,
        )
        items = []
        for ride in rides:
            emp_name = None
            emp_code = None
            if ride.employee and ride.employee.user:
                u = ride.employee.user
                emp_name = f"{u.first_name} {u.last_name}".strip()
                emp_code = ride.employee.employee_code
            elif ride.user:
                emp_name = f"{ride.user.first_name} {ride.user.last_name}".strip()
            items.append(
                CorporateRideHistoryItem(
                    id=ride.id,
                    public_id=ride.public_id,
                    company_id=ride.company_id,
                    company_name=ride.company.company_name if ride.company else None,
                    employee_id=ride.employee_id,
                    employee_name=emp_name,
                    employee_code=emp_code,
                    status=ride.status,
                    ride_type=ride.ride_type,
                    payment_source=ride.payment_source,
                    estimated_fare=ride.estimated_fare,
                    final_fare=ride.final_fare,
                    pickup_address=ride.pickup_address,
                    dropoff_address=ride.dropoff_address,
                    created_at=ride.created_at,
                    completed_at=ride.completed_at,
                )
            )
        return {"items": items, "total": total, "page": page, "limit": limit}

    async def reports(
        self,
        *,
        company_id: Optional[UUID] = None,
        employee_id: Optional[UUID] = None,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
    ) -> dict:
        since = from_date
        until = to_date
        total = await self.repo.count_corporate_rides(
            company_id=company_id, employee_id=employee_id, since=since, until=until
        )
        completed = await self.repo.count_corporate_rides(
            company_id=company_id,
            employee_id=employee_id,
            since=since,
            until=until,
            status=RideStatus.COMPLETED.value,
        )
        cancelled = await self.repo.count_corporate_rides(
            company_id=company_id,
            employee_id=employee_id,
            since=since,
            until=until,
            status=RideStatus.CANCELLED.value,
        )
        spend = await self.repo.sum_corporate_spend(
            company_id=company_id, employee_id=employee_id, since=since, until=until
        )
        return {
            "ride_count": total,
            "completed_rides": completed,
            "cancelled_rides": cancelled,
            "monthly_spending": round(spend, 2),
            "company_id": str(company_id) if company_id else None,
            "employee_id": str(employee_id) if employee_id else None,
        }
