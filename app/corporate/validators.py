"""Corporate booking and entity validators."""
from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Optional
from uuid import UUID

from app.core.constants import CompanyEmployeeStatus, CompanyStatus, RideType
from app.core.exceptions import ForbiddenException, ValidationException
from app.corporate.models import Company, CompanyEmployee, CompanyPolicy
from app.corporate.repository import CorporateRepository


def normalize_gst(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    cleaned = value.strip().upper()
    if len(cleaned) != 15:
        raise ValidationException("GST number must be 15 characters")
    return cleaned


def normalize_pan(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    cleaned = value.strip().upper()
    if len(cleaned) != 10:
        raise ValidationException("PAN number must be 10 characters")
    return cleaned


def ensure_company_approved(company: Company) -> None:
    if company.status != CompanyStatus.APPROVED.value:
        raise ForbiddenException("Company is not approved for corporate rides")


def ensure_employee_active(employee: CompanyEmployee) -> None:
    if employee.status != CompanyEmployeeStatus.ACTIVE.value:
        raise ForbiddenException("Employee is not active for corporate rides")


def _in_office_hours(policy: CompanyPolicy, now: datetime) -> bool:
    if policy.working_days is not None:
        if int(now.weekday()) not in [int(d) for d in policy.working_days]:
            return False
    start: Optional[time] = policy.office_start_time
    end: Optional[time] = policy.office_end_time
    if start and end:
        current = now.timetz().replace(tzinfo=None) if now.tzinfo else now.time()
        if start <= end:
            return start <= current <= end
        return current >= start or current <= end
    return True


async def validate_corporate_booking(
    repo: CorporateRepository,
    *,
    user_id: UUID,
    company_id: Optional[UUID],
    employee_id: Optional[UUID],
    vehicle_type_id: UUID,
    estimated_fare: float,
) -> tuple[Company, CompanyEmployee]:
    membership = await repo.get_active_membership_for_user(user_id)
    if not membership:
        raise ForbiddenException("You are not linked to a company")

    if employee_id and membership.id != employee_id:
        raise ForbiddenException("Employee does not match your corporate membership")
    if company_id and membership.company_id != company_id:
        raise ForbiddenException("Company does not match your corporate membership")

    company = membership.company or await repo.get_company(membership.company_id)
    if not company:
        raise ForbiddenException("Company not found")

    ensure_company_approved(company)
    ensure_employee_active(membership)

    policy = await repo.get_policy(company.id)
    if policy:
        if policy.allowed_vehicle_types:
            allowed = {str(v) for v in policy.allowed_vehicle_types}
            if str(vehicle_type_id) not in allowed:
                raise ValidationException("Vehicle type not allowed by company policy")

        if policy.max_ride_amount is not None and estimated_fare > float(policy.max_ride_amount):
            raise ValidationException(
                f"Ride fare exceeds company limit of ₹{policy.max_ride_amount:.0f}"
            )

        now = datetime.now(timezone.utc).astimezone()
        if not _in_office_hours(policy, now):
            raise ValidationException("Corporate rides are only allowed during office hours")

        if membership.ride_limit is not None:
            month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            spent = await repo.sum_corporate_spend(
                company_id=company.id,
                employee_id=membership.id,
                since=month_start,
            )
            if spent + estimated_fare > float(membership.ride_limit):
                raise ValidationException("Employee monthly ride limit exceeded")

        if company.credit_limit and company.credit_limit > 0:
            month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            company_spent = await repo.sum_corporate_spend(company_id=company.id, since=month_start)
            if company_spent + estimated_fare > float(company.credit_limit) + float(
                company.wallet_balance or 0
            ):
                raise ValidationException("Company credit limit exceeded")

    return company, membership


def is_corporate_request(ride_type: Optional[str]) -> bool:
    return (ride_type or RideType.NORMAL.value).upper() == RideType.CORPORATE.value
