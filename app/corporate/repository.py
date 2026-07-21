"""Corporate module data access."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Sequence
from uuid import UUID

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.constants import CompanyEmployeeStatus, CompanyStatus, RideStatus, RideType
from app.corporate.models import Company, CompanyEmployee, CompanyPolicy
from app.rides.models import Ride
from app.users.models import User
from app.utils.phone import phone_lookup_variants


class CorporateRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ── Companies ──────────────────────────────────────────────

    async def get_company(self, company_id: UUID) -> Optional[Company]:
        result = await self.db.execute(
            select(Company)
            .options(selectinload(Company.policy), selectinload(Company.employees))
            .where(Company.id == company_id)
        )
        return result.scalar_one_or_none()

    async def get_company_by_email(self, email: str) -> Optional[Company]:
        result = await self.db.execute(
            select(Company).where(func.lower(Company.email) == email.lower().strip())
        )
        return result.scalar_one_or_none()

    async def get_company_by_code(self, code: str) -> Optional[Company]:
        result = await self.db.execute(
            select(Company).where(Company.company_code == code.upper().strip())
        )
        return result.scalar_one_or_none()

    async def create_company(self, company: Company) -> Company:
        self.db.add(company)
        await self.db.flush()
        await self.db.refresh(company)
        return company

    async def list_companies(
        self,
        *,
        status: Optional[str] = None,
        search: Optional[str] = None,
        page: int = 1,
        limit: int = 50,
    ) -> tuple[list[Company], int]:
        filters = []
        if status:
            filters.append(Company.status == status)
        if search:
            q = f"%{search.strip()}%"
            filters.append(
                or_(
                    Company.company_name.ilike(q),
                    Company.company_code.ilike(q),
                    Company.email.ilike(q),
                    Company.phone.ilike(q),
                    Company.contact_person.ilike(q),
                )
            )
        where = and_(*filters) if filters else True
        total = await self.db.scalar(select(func.count()).select_from(Company).where(where)) or 0
        result = await self.db.execute(
            select(Company)
            .where(where)
            .order_by(Company.created_at.desc())
            .offset(max(0, (page - 1) * limit))
            .limit(limit)
        )
        return list(result.scalars().all()), int(total)

    # ── Employees ──────────────────────────────────────────────

    async def get_employee(self, employee_id: UUID) -> Optional[CompanyEmployee]:
        result = await self.db.execute(
            select(CompanyEmployee)
            .options(selectinload(CompanyEmployee.user), selectinload(CompanyEmployee.company))
            .where(CompanyEmployee.id == employee_id)
        )
        return result.scalar_one_or_none()

    async def get_active_membership_for_user(self, user_id: UUID) -> Optional[CompanyEmployee]:
        result = await self.db.execute(
            select(CompanyEmployee)
            .options(selectinload(CompanyEmployee.company), selectinload(CompanyEmployee.user))
            .where(
                CompanyEmployee.user_id == user_id,
                CompanyEmployee.status == CompanyEmployeeStatus.ACTIVE.value,
            )
            .order_by(CompanyEmployee.joined_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_membership_for_user(self, user_id: UUID) -> Optional[CompanyEmployee]:
        result = await self.db.execute(
            select(CompanyEmployee)
            .options(selectinload(CompanyEmployee.company), selectinload(CompanyEmployee.user))
            .where(
                CompanyEmployee.user_id == user_id,
                CompanyEmployee.status != CompanyEmployeeStatus.REMOVED.value,
            )
            .order_by(CompanyEmployee.joined_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def find_employee(
        self, company_id: UUID, user_id: UUID
    ) -> Optional[CompanyEmployee]:
        result = await self.db.execute(
            select(CompanyEmployee).where(
                CompanyEmployee.company_id == company_id,
                CompanyEmployee.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def create_employee(self, employee: CompanyEmployee) -> CompanyEmployee:
        self.db.add(employee)
        await self.db.flush()
        await self.db.refresh(employee)
        return employee

    async def list_employees(
        self,
        company_id: UUID,
        *,
        status: Optional[str] = None,
        search: Optional[str] = None,
        page: int = 1,
        limit: int = 50,
    ) -> tuple[list[CompanyEmployee], int]:
        filters = [
            CompanyEmployee.company_id == company_id,
            CompanyEmployee.status != CompanyEmployeeStatus.REMOVED.value,
        ]
        if status:
            filters.append(CompanyEmployee.status == status)

        stmt = (
            select(CompanyEmployee)
            .options(selectinload(CompanyEmployee.user))
            .where(and_(*filters))
        )
        if search:
            q = f"%{search.strip()}%"
            stmt = stmt.join(User, User.id == CompanyEmployee.user_id).where(
                or_(
                    CompanyEmployee.employee_code.ilike(q),
                    User.first_name.ilike(q),
                    User.last_name.ilike(q),
                    User.phone.ilike(q),
                    User.email.ilike(q),
                )
            )

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = await self.db.scalar(count_stmt) or 0
        result = await self.db.execute(
            stmt.order_by(CompanyEmployee.joined_at.desc())
            .offset(max(0, (page - 1) * limit))
            .limit(limit)
        )
        return list(result.scalars().all()), int(total)

    async def count_active_employees(self, company_id: Optional[UUID] = None) -> int:
        filters = [CompanyEmployee.status == CompanyEmployeeStatus.ACTIVE.value]
        if company_id:
            filters.append(CompanyEmployee.company_id == company_id)
        return int(
            await self.db.scalar(select(func.count()).select_from(CompanyEmployee).where(and_(*filters)))
            or 0
        )

    # ── Policies ───────────────────────────────────────────────

    async def get_policy(self, company_id: UUID) -> Optional[CompanyPolicy]:
        result = await self.db.execute(
            select(CompanyPolicy).where(CompanyPolicy.company_id == company_id)
        )
        return result.scalar_one_or_none()

    async def upsert_policy(self, policy: CompanyPolicy) -> CompanyPolicy:
        self.db.add(policy)
        await self.db.flush()
        await self.db.refresh(policy)
        return policy

    # ── Ride aggregates ────────────────────────────────────────

    async def count_companies(self, status: Optional[str] = None) -> int:
        filters = []
        if status:
            filters.append(Company.status == status)
        where = and_(*filters) if filters else True
        return int(await self.db.scalar(select(func.count()).select_from(Company).where(where)) or 0)

    async def count_corporate_rides(
        self,
        *,
        company_id: Optional[UUID] = None,
        employee_id: Optional[UUID] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        status: Optional[str] = None,
    ) -> int:
        filters = [Ride.ride_type == RideType.CORPORATE.value]
        if company_id:
            filters.append(Ride.company_id == company_id)
        if employee_id:
            filters.append(Ride.employee_id == employee_id)
        if since:
            filters.append(Ride.created_at >= since)
        if until:
            filters.append(Ride.created_at < until)
        if status:
            filters.append(Ride.status == status)
        return int(await self.db.scalar(select(func.count()).select_from(Ride).where(and_(*filters))) or 0)

    async def sum_corporate_spend(
        self,
        *,
        company_id: Optional[UUID] = None,
        employee_id: Optional[UUID] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> float:
        filters = [
            Ride.ride_type == RideType.CORPORATE.value,
            Ride.status == RideStatus.COMPLETED.value,
        ]
        if company_id:
            filters.append(Ride.company_id == company_id)
        if employee_id:
            filters.append(Ride.employee_id == employee_id)
        if since:
            filters.append(Ride.created_at >= since)
        if until:
            filters.append(Ride.created_at < until)
        fare = func.coalesce(Ride.final_fare, Ride.estimated_fare, 0)
        return float(await self.db.scalar(select(func.coalesce(func.sum(fare), 0)).where(and_(*filters))) or 0)

    async def list_corporate_rides(
        self,
        *,
        company_id: Optional[UUID] = None,
        employee_id: Optional[UUID] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        status: Optional[str] = None,
        page: int = 1,
        limit: int = 50,
    ) -> tuple[list[Ride], int]:
        filters = [Ride.ride_type == RideType.CORPORATE.value]
        if company_id:
            filters.append(Ride.company_id == company_id)
        if employee_id:
            filters.append(Ride.employee_id == employee_id)
        if since:
            filters.append(Ride.created_at >= since)
        if until:
            filters.append(Ride.created_at < until)
        if status:
            filters.append(Ride.status == status)
        where = and_(*filters)
        total = await self.db.scalar(select(func.count()).select_from(Ride).where(where)) or 0
        result = await self.db.execute(
            select(Ride)
            .options(
                selectinload(Ride.company),
                selectinload(Ride.employee).selectinload(CompanyEmployee.user),
                selectinload(Ride.user),
            )
            .where(where)
            .order_by(Ride.created_at.desc())
            .offset(max(0, (page - 1) * limit))
            .limit(limit)
        )
        return list(result.scalars().all()), int(total)

    async def ride_trend_daily(self, since: datetime, days: int = 14) -> list[dict]:
        day = func.date(Ride.created_at)
        result = await self.db.execute(
            select(day.label("day"), func.count().label("count"))
            .where(
                Ride.ride_type == RideType.CORPORATE.value,
                Ride.created_at >= since,
            )
            .group_by(day)
            .order_by(day)
        )
        return [{"day": str(row.day), "count": int(row.count)} for row in result.all()]

    async def top_companies_by_spend(self, since: datetime, limit: int = 5) -> list[dict]:
        fare = func.coalesce(Ride.final_fare, Ride.estimated_fare, 0)
        result = await self.db.execute(
            select(
                Company.id,
                Company.company_name,
                func.count(Ride.id).label("ride_count"),
                func.coalesce(func.sum(fare), 0).label("spend"),
            )
            .join(Ride, Ride.company_id == Company.id)
            .where(
                Ride.ride_type == RideType.CORPORATE.value,
                Ride.status == RideStatus.COMPLETED.value,
                Ride.created_at >= since,
            )
            .group_by(Company.id, Company.company_name)
            .order_by(func.sum(fare).desc())
            .limit(limit)
        )
        return [
            {
                "company_id": str(row.id),
                "company_name": row.company_name,
                "ride_count": int(row.ride_count),
                "spend": float(row.spend or 0),
            }
            for row in result.all()
        ]

    async def monthly_series(self, months: int = 6) -> tuple[list[dict], list[dict]]:
        month = func.date_trunc("month", Ride.created_at)
        fare = func.coalesce(Ride.final_fare, Ride.estimated_fare, 0)
        result = await self.db.execute(
            select(
                month.label("month"),
                func.count().label("ride_count"),
                func.coalesce(
                    func.sum(
                        case(
                            (Ride.status == RideStatus.COMPLETED.value, fare),
                            else_=0,
                        )
                    ),
                    0,
                ).label("spend"),
            )
            .where(Ride.ride_type == RideType.CORPORATE.value)
            .group_by(month)
            .order_by(month.desc())
            .limit(months)
        )
        rows = list(reversed(result.all()))
        ride_count = [
            {"month": row.month.strftime("%Y-%m") if row.month else "", "count": int(row.ride_count)}
            for row in rows
        ]
        spending = [
            {"month": row.month.strftime("%Y-%m") if row.month else "", "amount": float(row.spend or 0)}
            for row in rows
        ]
        return ride_count, spending

    async def resolve_user(
        self,
        *,
        user_id: Optional[UUID] = None,
        phone: Optional[str] = None,
        email: Optional[str] = None,
    ) -> Optional[User]:
        if user_id:
            return await self.db.get(User, user_id)

        # Phone: match common stored formats (+91… / 91… / 10-digit)
        if phone:
            for variant in phone_lookup_variants(phone):
                result = await self.db.execute(
                    select(User).where(User.phone == variant, User.is_deleted == False).limit(1)
                )
                user = result.scalar_one_or_none()
                if user:
                    return user

            digits = "".join(c for c in phone if c.isdigit())
            if len(digits) >= 10:
                local = digits[-10:]
                result = await self.db.execute(
                    select(User).where(User.is_deleted == False, User.phone.like(f"%{local}"))
                )
                for user in result.scalars().all():
                    stored = "".join(c for c in (user.phone or "") if c.isdigit())
                    if stored.endswith(local):
                        return user

        if email:
            result = await self.db.execute(
                select(User)
                .where(func.lower(User.email) == email.lower().strip(), User.is_deleted == False)
                .limit(1)
            )
            return result.scalar_one_or_none()

        return None
