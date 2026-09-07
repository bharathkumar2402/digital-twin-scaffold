from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rbac import require_roles
from app.core.tenant_context import TenantContext, get_tenant_context, get_tenant_scoped_session
from app.models.user import Role, User
from app.schemas.requests.auth import UserResponse

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
