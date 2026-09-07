"""grant app_role INSERT/UPDATE on tenants for tenant management CRUD

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-07

Migration 0002 only granted `app_role` SELECT on `tenants` (the app previously only
read tenant rows, e.g. to resolve login/register requests). Task 1.5 adds superadmin
tenant create/update routes, which run under `app_role` at runtime — without this grant
they'd fail with "permission denied for table tenants" despite working fine in tests
that connect with admin/migration credentials directly (see
`tests/integration/test_tenants_routes.py`, which does exactly that and would not have
caught this).

No RLS policy is added here: `tenants` intentionally has none (task 1.1) since it's the
tenant-identity table itself — a superadmin's authority to manage tenants is enforced at
the route level via `require_roles(Role.SUPERADMIN)`, not the DB session GUC.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "app_role"


def upgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql(f"GRANT INSERT, UPDATE ON tenants TO {APP_ROLE}")


def downgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql(f"REVOKE INSERT, UPDATE ON tenants FROM {APP_ROLE}")
