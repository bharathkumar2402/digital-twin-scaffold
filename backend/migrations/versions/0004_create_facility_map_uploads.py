"""create facility_map_uploads, tenant-isolation RLS, app_role grants (issue 2.1)

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-08

Tracks the lifecycle of a raw floor-plan upload through the sandbox pipeline
(app/sandbox/**, app/workers/callback_tasks.py) — see PROJECT_PLAN.md §6. Follows the
same fail-closed RLS pattern as `users`/`facilities` in 0001: a session with no
`app.current_tenant_id` GUC set sees zero rows rather than erroring into a default-allow
state, and `WITH CHECK` blocks writing a row stamped with a different tenant_id than the
session is scoped to.
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

upload_status_values = ("pending", "processing", "sanitized", "failed")


def upgrade() -> None:
    upload_status = postgresql.ENUM(
        *upload_status_values, name="facility_map_upload_status"
    )
    upload_status.create(op.get_bind())
    upload_status_no_create = postgresql.ENUM(
        *upload_status_values, name="facility_map_upload_status", create_type=False
    )

    op.create_table(
        "facility_map_uploads",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("facility_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("format", sa.String(length=10), nullable=False),
        sa.Column(
            "status",
            upload_status_no_create,
            nullable=False,
            server_default="pending",
        ),
        sa.Column("status_detail", sa.String(length=1000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["facility_id"], ["facilities.id"]),
    )
    op.create_index(
        "ix_facility_map_uploads_tenant_id", "facility_map_uploads", ["tenant_id"]
    )
    op.create_index(
        "ix_facility_map_uploads_facility_id", "facility_map_uploads", ["facility_id"]
    )

    op.execute("ALTER TABLE facility_map_uploads ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE facility_map_uploads FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_facility_map_uploads ON facility_map_uploads
        USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
        """
    )

    conn = op.get_bind()
    conn.exec_driver_sql(
        f"GRANT SELECT, INSERT, UPDATE ON facility_map_uploads TO {APP_ROLE}"
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql(f"REVOKE SELECT, INSERT, UPDATE ON facility_map_uploads FROM {APP_ROLE}")

    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_facility_map_uploads ON facility_map_uploads"
    )
    op.drop_index("ix_facility_map_uploads_facility_id", table_name="facility_map_uploads")
    op.drop_index("ix_facility_map_uploads_tenant_id", table_name="facility_map_uploads")
    op.drop_table("facility_map_uploads")

    postgresql.ENUM(name="facility_map_upload_status").drop(op.get_bind())
