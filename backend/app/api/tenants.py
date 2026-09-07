import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.rbac import require_roles
from app.core.tenant_context import TenantContext
from app.models.user import Role
from app.schemas.requests.auth import UserResponse
from app.schemas.requests.tenants import TenantCreate, TenantResponse, TenantUpdate
from app.schemas.requests.users import AdminCreateUserRequest
from app.services.auth_service import EmailAlreadyRegisteredError, create_user_as_admin
from app.services.tenant_service import (
    TenantNotFoundError,
    create_tenant,
    list_tenants,
    update_tenant,
)

router = APIRouter(tags=["tenants"])


@router.post("/tenants", response_model=TenantResponse, status_code=status.HTTP_201_CREATED)
async def create_tenant_route(
    body: TenantCreate,
    _context: TenantContext = Depends(require_roles(Role.SUPERADMIN)),
    session: AsyncSession = Depends(get_session),
) -> TenantResponse:
    tenant = await create_tenant(session, name=body.name, plan_tier=body.plan_tier)
    return TenantResponse.model_validate(tenant)


@router.get("/tenants", response_model=list[TenantResponse])
async def list_tenants_route(
    _context: TenantContext = Depends(require_roles(Role.SUPERADMIN)),
    session: AsyncSession = Depends(get_session),
) -> list[TenantResponse]:
    tenants = await list_tenants(session)
    return [TenantResponse.model_validate(tenant) for tenant in tenants]


@router.patch("/tenants/{tenant_id}", response_model=TenantResponse)
async def update_tenant_route(
    tenant_id: uuid.UUID,
    body: TenantUpdate,
    _context: TenantContext = Depends(require_roles(Role.SUPERADMIN)),
    session: AsyncSession = Depends(get_session),
) -> TenantResponse:
    try:
        tenant = await update_tenant(
            session, tenant_id=tenant_id, name=body.name, plan_tier=body.plan_tier
        )
    except TenantNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found"
        ) from exc
    return TenantResponse.model_validate(tenant)


@router.post(
    "/tenants/{tenant_id}/users",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_user_in_tenant_route(
    tenant_id: uuid.UUID,
    body: AdminCreateUserRequest,
    _context: TenantContext = Depends(require_roles(Role.SUPERADMIN)),
    session: AsyncSession = Depends(get_session),
) -> UserResponse:
    try:
        user = await create_user_as_admin(
            session,
            tenant_id=tenant_id,
            email=body.email,
            password=body.password,
            role=body.role,
        )
    except EmailAlreadyRegisteredError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
        ) from exc
    return UserResponse.model_validate(user)
