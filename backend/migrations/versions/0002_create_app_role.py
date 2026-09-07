"""create non-bypass app_role for the application's runtime DB connection

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-07

The app previously connected as the migration/admin role (`postgres_user`), which on
managed Postgres providers (including Supabase's `postgres` role) carries BYPASSRLS —
meaning the RLS policies from migration 0001 were never actually enforced for the app's
own queries, only for the isolated `app_test_role` used in
`tests/cross_tenant/test_users_facilities_rls.py`. This migration creates a real,
non-bypass role for the app to connect as at runtime (see `app/core/db.py` and
`app/core/config.py`'s `app_database_url`); the admin/migration role is unchanged.

Reminder for future migrations: every new `tenant_id`-bearing table needs its own
`GRANT ... TO app_role` here, alongside its RLS policy and cross-tenant test — otherwise
the app will get "permission denied" (or, on a SELECT with FORCE RLS off, wrongly see all
rows) once it tries to use that table under this non-superuser role.
"""
from collections.abc import Sequence

from alembic import op

from app.core.config import settings

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "app_role"


def upgrade() -> None:
    conn = op.get_bind()

    conn.exec_driver_sql(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                CREATE ROLE {APP_ROLE} LOGIN NOBYPASSRLS;
            END IF;
        END
        $$;
        """
    )
    # Password kept in sync with settings on every migration run (not just role creation),
    # so rotating APP_DB_PASSWORD in .env and rerunning migrations actually takes effect.
    conn.exec_driver_sql(
        f"ALTER ROLE {APP_ROLE} WITH PASSWORD '{settings.app_db_password}'"
    )

    conn.exec_driver_sql(f"GRANT SELECT ON tenants TO {APP_ROLE}")
    conn.exec_driver_sql(f"GRANT SELECT, INSERT, UPDATE, DELETE ON users, facilities TO {APP_ROLE}")


def downgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql(f"REVOKE ALL PRIVILEGES ON users, facilities, tenants FROM {APP_ROLE}")
    conn.exec_driver_sql(f"DROP ROLE IF EXISTS {APP_ROLE}")
