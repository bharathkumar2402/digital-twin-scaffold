"""create assets, tenant-isolation RLS, app_role grants (issue 2.6)

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-08

Assets are physical equipment (pump, conveyor, HVAC unit, ...) placed on a
facility's map at local-pixel `(x, y)` coordinates - see app/models/asset.py's
docstring and PROJECT_PLAN.md §6 for the coordinate-system note. Same fail-closed
RLS pattern as `facility_map_uploads` in 0004: a session with no
`app.current_tenant_id` GUC set sees zero rows, and `WITH CHECK` blocks writing a
row stamped with a different tenant_id than the session is scoped to.

Unlike prior tenant-scoped tables, assets are also user-deletable (drag-and-drop
placement + removal from the map), so `app_role` gets DELETE here for the first
time - the RLS policy's `USING` clause covers DELETE the same way it covers
SELECT/UPDATE, so a tenant can never delete a row RLS wouldn't let it see.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "app_role"

asset_status_values = ("operational", "maintenance", "offline")


def upgrade() -> None:
    asset_status = postgresql.ENUM(*asset_status_values, name="asset_status")
    asset_status.create(op.get_bind())
    asset_status_no_create = postgresql.ENUM(
        *asset_status_values, name="asset_status", create_type=False
    )

    op.create_table(
        "assets",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("facility_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("type", sa.String(length=100), nullable=False),
        sa.Column("x", sa.Float(), nullable=False),
        sa.Column("y", sa.Float(), nullable=False),
        sa.Column(
            "status",
            asset_status_no_create,
            nullable=False,
            server_default="operational",
        ),
        sa.Column("installed_date", sa.Date(), nullable=True),
        sa.Column("manufacturer", sa.String(length=255), nullable=True),
        sa.Column("model", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["facility_id"], ["facilities.id"]),
    )
    op.create_index("ix_assets_tenant_id", "assets", ["tenant_id"])
    op.create_index("ix_assets_facility_id", "assets", ["facility_id"])

    op.execute("ALTER TABLE assets ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE assets FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_assets ON assets
        USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
        """
    )

    conn = op.get_bind()
    conn.exec_driver_sql(f"GRANT SELECT, INSERT, UPDATE, DELETE ON assets TO {APP_ROLE}")


def downgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql(f"REVOKE SELECT, INSERT, UPDATE, DELETE ON assets FROM {APP_ROLE}")

    op.execute("DROP POLICY IF EXISTS tenant_isolation_assets ON assets")
    op.drop_index("ix_assets_facility_id", table_name="assets")
    op.drop_index("ix_assets_tenant_id", table_name="assets")
    op.drop_table("assets")

    postgresql.ENUM(name="asset_status").drop(op.get_bind())
