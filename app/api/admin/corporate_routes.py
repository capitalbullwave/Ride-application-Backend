"""Platform admin corporate management APIs."""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.auth.dependencies import get_current_admin
from app.corporate.dependencies import get_corporate_service
from app.corporate.schemas import (
    CompanyRejectRequest,
    CompanyUpdateRequest,
    EmployeeCreateRequest,
    EmployeeUpdateRequest,
    PolicyUpsertRequest,
)
from app.corporate.service import CorporateService
from app.models import AdminUser
from app.schemas.common import MessageResponse

router = APIRouter(prefix="/corporate", tags=["Admin Corporate"])


@router.get("/dashboard")
async def corporate_dashboard(
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    service: Annotated[CorporateService, Depends(get_corporate_service)],
):
    return await service.dashboard()


@router.get("/companies")
async def list_companies(
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    service: Annotated[CorporateService, Depends(get_corporate_service)],
    status: Optional[str] = None,
    search: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
):
    return await service.list_companies(status=status, search=search, page=page, limit=limit)


@router.get("/companies/{company_id}")
async def company_details(
    company_id: UUID,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    service: Annotated[CorporateService, Depends(get_corporate_service)],
):
    return await service.get_company_details(company_id)


@router.patch("/companies/{company_id}")
async def update_company(
    company_id: UUID,
    data: CompanyUpdateRequest,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    service: Annotated[CorporateService, Depends(get_corporate_service)],
):
    return await service.update_company(company_id, data)


@router.post("/companies/{company_id}/approve")
async def approve_company(
    company_id: UUID,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    service: Annotated[CorporateService, Depends(get_corporate_service)],
):
    return await service.approve_company(company_id, admin.id)


@router.post("/companies/{company_id}/reject")
async def reject_company(
    company_id: UUID,
    data: CompanyRejectRequest,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    service: Annotated[CorporateService, Depends(get_corporate_service)],
):
    return await service.reject_company(company_id, data)


@router.post("/companies/{company_id}/suspend")
async def suspend_company(
    company_id: UUID,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    service: Annotated[CorporateService, Depends(get_corporate_service)],
):
    return await service.suspend_company(company_id)


@router.delete("/companies/{company_id}", response_model=MessageResponse)
async def delete_company(
    company_id: UUID,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    service: Annotated[CorporateService, Depends(get_corporate_service)],
):
    await service.delete_company(company_id)
    return MessageResponse(message="Company deleted")


@router.get("/companies/{company_id}/employees")
async def list_company_employees(
    company_id: UUID,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    service: Annotated[CorporateService, Depends(get_corporate_service)],
    status: Optional[str] = None,
    search: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
):
    return await service.list_employees(
        company_id, status=status, search=search, page=page, limit=limit
    )


@router.post("/companies/{company_id}/employees")
async def add_company_employee(
    company_id: UUID,
    data: EmployeeCreateRequest,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    service: Annotated[CorporateService, Depends(get_corporate_service)],
):
    return await service.add_employee(company_id, data)


@router.patch("/companies/{company_id}/employees/{employee_id}")
async def update_company_employee(
    company_id: UUID,
    employee_id: UUID,
    data: EmployeeUpdateRequest,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    service: Annotated[CorporateService, Depends(get_corporate_service)],
):
    return await service.update_employee(company_id, employee_id, data)


@router.post("/companies/{company_id}/employees/{employee_id}/activate")
async def activate_employee(
    company_id: UUID,
    employee_id: UUID,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    service: Annotated[CorporateService, Depends(get_corporate_service)],
):
    return await service.set_employee_status(company_id, employee_id, "ACTIVE")


@router.post("/companies/{company_id}/employees/{employee_id}/deactivate")
async def deactivate_employee(
    company_id: UUID,
    employee_id: UUID,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    service: Annotated[CorporateService, Depends(get_corporate_service)],
):
    return await service.set_employee_status(company_id, employee_id, "INACTIVE")


@router.delete("/companies/{company_id}/employees/{employee_id}", response_model=MessageResponse)
async def remove_employee(
    company_id: UUID,
    employee_id: UUID,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    service: Annotated[CorporateService, Depends(get_corporate_service)],
):
    await service.remove_employee(company_id, employee_id)
    return MessageResponse(message="Employee removed")


@router.get("/companies/{company_id}/policy")
async def get_policy(
    company_id: UUID,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    service: Annotated[CorporateService, Depends(get_corporate_service)],
):
    return await service.get_policy(company_id)


@router.put("/companies/{company_id}/policy")
async def upsert_policy(
    company_id: UUID,
    data: PolicyUpsertRequest,
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    service: Annotated[CorporateService, Depends(get_corporate_service)],
):
    return await service.upsert_policy(company_id, data)


@router.get("/rides")
async def corporate_rides(
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    service: Annotated[CorporateService, Depends(get_corporate_service)],
    company_id: Optional[UUID] = None,
    employee_id: Optional[UUID] = None,
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
):
    return await service.list_corporate_rides(
        company_id=company_id,
        employee_id=employee_id,
        status=status,
        page=page,
        limit=limit,
    )


@router.get("/policies")
async def list_policies_overview(
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    service: Annotated[CorporateService, Depends(get_corporate_service)],
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
):
    """List approved companies with their policy summary."""
    result = await service.list_companies(
        status="APPROVED", page=page, limit=limit
    )
    items = []
    for company in result["items"]:
        try:
            policy = await service.get_policy(company.id)
            items.append({"company": company, "policy": policy})
        except Exception:
            items.append({"company": company, "policy": None})
    return {"items": items, "total": result["total"], "page": page, "limit": limit}


@router.get("/reports")
async def corporate_reports(
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    service: Annotated[CorporateService, Depends(get_corporate_service)],
    company_id: Optional[UUID] = None,
    employee_id: Optional[UUID] = None,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
):
    since = (
        datetime.combine(from_date, time.min, tzinfo=timezone.utc) if from_date else None
    )
    until = (
        datetime.combine(to_date, time.max, tzinfo=timezone.utc) if to_date else None
    )
    return await service.reports(
        company_id=company_id,
        employee_id=employee_id,
        from_date=since,
        to_date=until,
    )


@router.get("/employees")
async def all_employees(
    admin: Annotated[AdminUser, Depends(get_current_admin)],
    service: Annotated[CorporateService, Depends(get_corporate_service)],
    company_id: UUID = Query(...),
    status: Optional[str] = None,
    search: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
):
    return await service.list_employees(
        company_id, status=status, search=search, page=page, limit=limit
    )
