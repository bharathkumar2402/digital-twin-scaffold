from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rbac import require_roles
from app.core.tenant_context import TenantContext, get_tenant_context, get_tenant_scoped_session
from app.models.user import Role, User
from app.schemas.requests.auth import UserResponse
from app.schemas.requests.users import AdminCreateUserRequest
from app.services.auth_service import EmailAlreadyRegisteredError, create_user_as_admin

router = APIRouter(tags=["users"])


@router.get("/me", response_model=UserResponse)
async def get_current_user(
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_tenant_scoped_session),
) -> UserResponse:
    result = await session.execute(select(User).where(User.id == context.user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    return UserResponse.model_validate(user)


@router.get("/users", response_model=list[UserResponse])
async def list_tenant_users(
    context: TenantContext = Depends(require_roles(Role.TENANT_ADMIN, Role.SUPERADMIN)),
    session: AsyncSession = Depends(get_tenant_scoped_session),
) -> list[UserResponse]:
    result = await session.execute(select(User).where(User.tenant_id == context.tenant_id))
    users = result.scalars().all()
    return [UserResponse.model_validate(user) for user in users]


@router.post("/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user_in_own_tenant(
    body: AdminCreateUserRequest,
    context: TenantContext = Depends(require_roles(Role.TENANT_ADMIN, Role.SUPERADMIN)),
    session: AsyncSession = Depends(get_tenant_scoped_session),
) -> UserResponse:
    if body.role == Role.SUPERADMIN and context.role != Role.SUPERADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only a superadmin can grant the superadmin role",
        )

    try:
        user = await create_user_as_admin(
            session,
            tenant_id=context.tenant_id,
            email=body.email,
            password=body.password,
            role=body.role,
        )
    except EmailAlreadyRegisteredError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
        ) from exc
    return UserResponse.model_validate(user)
