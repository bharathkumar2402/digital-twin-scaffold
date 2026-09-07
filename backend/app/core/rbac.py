from collections.abc import Callable

from fastapi import Depends, HTTPException, status

from app.core.tenant_context import TenantContext, get_tenant_context
from app.models.user import Role


def require_roles(*allowed: Role) -> Callable[[TenantContext], TenantContext]:
    """Returns a FastAPI dependency that 403s unless the caller's role is in `allowed`."""

    def _check(context: TenantContext = Depends(get_tenant_context)) -> TenantContext:
        if context.role not in {role.value for role in allowed}:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to perform this action",
            )
        return context

    return _check
