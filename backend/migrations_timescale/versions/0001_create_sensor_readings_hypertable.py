"""create sensor_readings TimescaleDB hypertable, on its own instance

Revision ID: 0001
Revises:
Create Date: 2026-09-08

Originally this table (and its RLS policy) lived as migration 0004 in the main
`migrations/` chain, on the same Postgres instance as tenants/users/facilities. That
was wrong: `PROJECT_PLAN.md` §3.2 and this file's own `.env.example` always called for
TimescaleDB to be a separate managed instance, and Supabase (the main instance) doesn't
support the `timescaledb` extension at all — the original migration would have failed
outright against a real Supabase database, even though it passed in tests (which ran
against a `timescale/timescaledb:latest-pg16` container standing in for the main DB).

Moving it here means `sensor_readings` can no longer have a foreign key to `tenants.id`
(no cross-database FKs in Postgres) — `tenant_id` is a bare indexed UUID instead, same
as `asset_id` already was. Tenancy is enforced purely by the RLS policy below, sourced
only from the JWT via the tenant-context dependency — never a raw request parameter.

`app_role` also has to be created fresh here: roles are per-cluster, so the app_role
migration 0002 created on the main instance does not exist on this one.

Primary key is `(id, timestamp)`, not just `id`: TimescaleDB requires every unique/
primary-key index on a hypertable to include the partitioning column.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.config import settings

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
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
    conn.exec_driver_sql(
        f"ALTER ROLE {APP_ROLE} WITH PASSWORD '{settings.app_timescale_password}'"
    )

    op.execute('CREATE EXTENSION IF NOT EXISTS "timescaledb" CASCADE')

    op.create_table(
        "sensor_readings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sensor_type", sa.String(length=100), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(length=50), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", "timestamp", name="pk_sensor_readings"),
    )
    op.create_index("ix_sensor_readings_tenant_id", "sensor_readings", ["tenant_id"])
    op.create_index(
        "ix_sensor_readings_asset_time", "sensor_readings", ["asset_id", "timestamp"]
    )

    op.execute("SELECT create_hypertable('sensor_readings', 'timestamp')")

    op.execute("ALTER TABLE sensor_readings ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE sensor_readings FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_sensor_readings ON sensor_readings
        USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
        """
    )

    conn.exec_driver_sql(f"GRANT SELECT, INSERT ON sensor_readings TO {APP_ROLE}")


def downgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql(f"REVOKE SELECT, INSERT ON sensor_readings FROM {APP_ROLE}")

    op.execute("DROP POLICY IF EXISTS tenant_isolation_sensor_readings ON sensor_readings")
    op.drop_index("ix_sensor_readings_asset_time", table_name="sensor_readings")
    op.drop_index("ix_sensor_readings_tenant_id", table_name="sensor_readings")
    op.drop_table("sensor_readings")
    conn.exec_driver_sql(f"DROP ROLE IF EXISTS {APP_ROLE}")
