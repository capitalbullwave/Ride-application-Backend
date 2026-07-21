"""Corporate public + company-admin + user membership APIs."""
from __future__ import annotations

from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.auth.dependencies import get_current_user
from app.corporate.dependencies import get_corporate_service, get_current_company
from app.corporate.models import Company
from app.corporate.schemas import (
    CompanyLoginRequest,
    CompanyRegisterRequest,
    CompanyUpdateRequest,
    EmployeeCreateRequest,
    EmployeeUpdateRequest,
    PolicyUpsertRequest,
)
from app.corporate.service import CorporateService
from app.models import User
from app.schemas.common import MessageResponse, TokenResponse

router = APIRouter(tags=["Corporate"])


# ── Public company registration / login ───────────────────────


@router.post("/register", response_model=dict)
async def register_company(
    data: CompanyRegisterRequest,
    service: Annotated[CorporateService, Depends(get_corporate_service)],
):
    company = await service.register_company(data)
    return {"success": True, "company": company}


@router.post("/login", response_model=TokenResponse)
async def login_company(
    data: CompanyLoginRequest,
    service: Annotated[CorporateService, Depends(get_corporate_service)],
):
    return await service.login_company(data)


# ── Company admin (JWT role=COMPANY) ───────────────────────────


@router.get("/me")
async def company_profile(
    company: Annotated[Company, Depends(get_current_company)],
    service: Annotated[CorporateService, Depends(get_corporate_service)],
):
    return await service.get_company_profile(company.id)


@router.patch("/me")
async def update_company_profile(
    data: CompanyUpdateRequest,
    company: Annotated[Company, Depends(get_current_company)],
    service: Annotated[CorporateService, Depends(get_corporate_service)],
):
    return await service.update_company(company.id, data)


@router.get("/me/employees")
async def my_employees(
    company: Annotated[Company, Depends(get_current_company)],
    service: Annotated[CorporateService, Depends(get_corporate_service)],
    status: Optional[str] = None,
    search: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
):
    return await service.list_employees(
        company.id, status=status, search=search, page=page, limit=limit
    )


@router.post("/me/employees")
async def add_my_employee(
    data: EmployeeCreateRequest,
    company: Annotated[Company, Depends(get_current_company)],
    service: Annotated[CorporateService, Depends(get_corporate_service)],
):
    return await service.add_employee(company.id, data)


@router.patch("/me/employees/{employee_id}")
async def update_my_employee(
    employee_id: UUID,
    data: EmployeeUpdateRequest,
    company: Annotated[Company, Depends(get_current_company)],
    service: Annotated[CorporateService, Depends(get_corporate_service)],
):
    return await service.update_employee(company.id, employee_id, data)


@router.post("/me/employees/{employee_id}/activate")
async def activate_my_employee(
    employee_id: UUID,
    company: Annotated[Company, Depends(get_current_company)],
    service: Annotated[CorporateService, Depends(get_corporate_service)],
):
    return await service.set_employee_status(company.id, employee_id, "ACTIVE")


@router.post("/me/employees/{employee_id}/deactivate")
async def deactivate_my_employee(
    employee_id: UUID,
    company: Annotated[Company, Depends(get_current_company)],
    service: Annotated[CorporateService, Depends(get_corporate_service)],
):
    return await service.set_employee_status(company.id, employee_id, "INACTIVE")


@router.delete("/me/employees/{employee_id}", response_model=MessageResponse)
async def remove_my_employee(
    employee_id: UUID,
    company: Annotated[Company, Depends(get_current_company)],
    service: Annotated[CorporateService, Depends(get_corporate_service)],
):
    await service.remove_employee(company.id, employee_id)
    return MessageResponse(message="Employee removed")


@router.get("/me/policy")
async def my_policy(
    company: Annotated[Company, Depends(get_current_company)],
    service: Annotated[CorporateService, Depends(get_corporate_service)],
):
    return await service.get_policy(company.id)


@router.put("/me/policy")
async def upsert_my_policy(
    data: PolicyUpsertRequest,
    company: Annotated[Company, Depends(get_current_company)],
    service: Annotated[CorporateService, Depends(get_corporate_service)],
):
    return await service.upsert_policy(company.id, data)


@router.get("/me/rides")
async def my_corporate_rides(
    company: Annotated[Company, Depends(get_current_company)],
    service: Annotated[CorporateService, Depends(get_corporate_service)],
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
):
    return await service.list_corporate_rides(
        company_id=company.id, status=status, page=page, limit=limit
    )


# ── User app membership ───────────────────────────────────────


@router.get("/membership")
async def user_corporate_membership(
    user: Annotated[User, Depends(get_current_user)],
    service: Annotated[CorporateService, Depends(get_corporate_service)],
):
    return await service.get_user_membership(user.id)
