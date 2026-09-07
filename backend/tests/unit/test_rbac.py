import uuid

import pytest
from fastapi import HTTPException

from app.core.rbac import require_roles
from app.core.tenant_context import TenantContext
from app.models.user import Role


def _context(role: str) -> TenantContext:
    return TenantContext(tenant_id=uuid.uuid4(), user_id=uuid.uuid4(), role=role)


def test_require_roles_allows_an_allowed_role() -> None:
    check = require_roles(Role.TENANT_ADMIN, Role.SUPERADMIN)
    context = _context("tenant_admin")

    assert check(context) is context


def test_require_roles_rejects_a_disallowed_role() -> None:
    check = require_roles(Role.TENANT_ADMIN, Role.SUPERADMIN)

    with pytest.raises(HTTPException) as exc_info:
        check(_context("viewer"))

    assert exc_info.value.status_code == 403


def test_require_roles_rejects_every_role_when_none_are_allowed() -> None:
    check = require_roles()

    with pytest.raises(HTTPException) as exc_info:
        check(_context("superadmin"))

    assert exc_info.value.status_code == 403
