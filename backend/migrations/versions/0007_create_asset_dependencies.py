"""create asset_dependencies, tenant-isolation RLS, app_role grants (issue 2.7)

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-08

Directed edges between assets: `parent_asset_id` depends on `child_asset_id` (child is
upstream). See app/models/asset_dependency.py's docstring for why this direction was
chosen - it's what Phase 4's cascade simulation walks. Same fail-closed RLS pattern as
`assets` in 0006, EXCEPT the policy expression is `NULLIF(current_setting(...), '')::uuid`
instead of a bare `current_setting(...)::uuid` - found live-verifying this task: once a
session has used `SET LOCAL app.current_tenant_id` at all, Postgres's custom-GUC
placeholder makes `current_setting(name, true)` return '' (not NULL) on that same
connection after the transaction commits, so a bare `::uuid` cast raises a hard error
(InvalidTextRepresentationError) instead of failing closed to 0 rows - this bites
`session.refresh()` immediately after any INSERT whenever SQLAlchemy's pool reuses the
connection within one request. `NULLIF(..., '')` coerces that '' to NULL first, so the
cast is always well-formed and the policy still fails closed (no GUC = no rows), just
via 0 rows instead of a 500. See migration 0008 for the same fix applied (by ALTER
POLICY, not by editing them) to the pre-existing `facility_map_uploads` (0004) and
`assets` (0006) policies, which have carried this same bug since Phase 1/2.6.
No UPDATE grant: edges are only ever created or deleted, never edited in place
(changing an edge's endpoints is delete-then-recreate at the API layer).
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "app_role"


def upgrade() -> None:
    op.create_table(
        "asset_dependencies",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("facility_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("child_asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["facility_id"], ["facilities.id"]),
        sa.ForeignKeyConstraint(["parent_asset_id"], ["assets.id"]),
        sa.ForeignKeyConstraint(["child_asset_id"], ["assets.id"]),
        sa.UniqueConstraint(
            "parent_asset_id", "child_asset_id", name="uq_asset_dependencies_edge"
        ),
        sa.CheckConstraint(
            "parent_asset_id != child_asset_id", name="ck_asset_dependencies_no_self_loop"
        ),
    )
    op.create_index("ix_asset_dependencies_tenant_id", "asset_dependencies", ["tenant_id"])
    op.create_index("ix_asset_dependencies_facility_id", "asset_dependencies", ["facility_id"])
    op.create_index(
        "ix_asset_dependencies_parent_asset_id", "asset_dependencies", ["parent_asset_id"]
    )
    op.create_index(
        "ix_asset_dependencies_child_asset_id", "asset_dependencies", ["child_asset_id"]
    )

    op.execute("ALTER TABLE asset_dependencies ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE asset_dependencies FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_asset_dependencies ON asset_dependencies
        USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
        """
    )

    conn = op.get_bind()
    conn.exec_driver_sql(f"GRANT SELECT, INSERT, DELETE ON asset_dependencies TO {APP_ROLE}")


def downgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql(f"REVOKE SELECT, INSERT, DELETE ON asset_dependencies FROM {APP_ROLE}")

    op.execute("DROP POLICY IF EXISTS tenant_isolation_asset_dependencies ON asset_dependencies")
    op.drop_index("ix_asset_dependencies_child_asset_id", table_name="asset_dependencies")
    op.drop_index("ix_asset_dependencies_parent_asset_id", table_name="asset_dependencies")
    op.drop_index("ix_asset_dependencies_facility_id", table_name="asset_dependencies")
    op.drop_index("ix_asset_dependencies_tenant_id", table_name="asset_dependencies")
    op.drop_table("asset_dependencies")
