"""create sensor_readings TimescaleDB hypertable

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-07

Task 1.6. `PROJECT_PLAN.md` §5 sketches `sensor_readings` without a `tenant_id` column,
but repo rule 2 requires an RLS policy + cross-tenant test on every tenant-scoped table,
so `tenant_id` is added here and enforced with the same fail-closed policy pattern as
migration 0001. `asset_id` is a bare indexed UUID with no FK yet — the `assets` table is
Phase 2 task 6; a follow-up migration adds the FK once it exists.

Primary key is `(id, timestamp)`, not just `id`: TimescaleDB requires every unique/
primary-key index on a hypertable to include the partitioning column.

Per migration 0002's reminder, `app_role` needs an explicit grant on this new table.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "app_role"


def upgrade() -> None:
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
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
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

    conn = op.get_bind()
    conn.exec_driver_sql(f"GRANT SELECT, INSERT ON sensor_readings TO {APP_ROLE}")


def downgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql(f"REVOKE SELECT, INSERT ON sensor_readings FROM {APP_ROLE}")

    op.execute("DROP POLICY IF EXISTS tenant_isolation_sensor_readings ON sensor_readings")
    op.drop_index("ix_sensor_readings_asset_time", table_name="sensor_readings")
    op.drop_index("ix_sensor_readings_tenant_id", table_name="sensor_readings")
    op.drop_table("sensor_readings")
