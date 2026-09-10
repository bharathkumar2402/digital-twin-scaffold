"""create risk_scores, tenant-isolation RLS, app_role grants (issue 3.3)

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-10

Insert-only history table: one row per risk-inference run per asset, never updated in
place - PROJECT_PLAN.md §5's `risk_scores` sketch plus a `tenant_id` column that sketch
omits (same gap-and-fix pattern as `assets`/`sensor_readings` before it - every
tenant_id-bearing table needs RLS + a cross-tenant test per repo rule 2). Written from
the start with the `NULLIF(current_setting(...), '')::uuid` guard from migration 0008,
not the bare cast 0001/0004/0006 originally shipped with.

No UPDATE grant: rows are append-only, "latest" is read via `ORDER BY computed_at DESC`
per asset, matching this table's history-not-snapshot design.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "app_role"


def upgrade() -> None:
    op.create_table(
        "risk_scores",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("facility_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("model_version", sa.String(length=255), nullable=False),
        sa.Column("factors_json", sa.JSON(), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["facility_id"], ["facilities.id"]),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"]),
        sa.CheckConstraint("score >= 0 AND score <= 100", name="ck_risk_scores_score_range"),
    )
    op.create_index("ix_risk_scores_tenant_id", "risk_scores", ["tenant_id"])
    op.create_index("ix_risk_scores_facility_id", "risk_scores", ["facility_id"])
    op.create_index("ix_risk_scores_asset_id", "risk_scores", ["asset_id"])
    op.create_index("ix_risk_scores_computed_at", "risk_scores", ["computed_at"])

    op.execute("ALTER TABLE risk_scores ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE risk_scores FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_risk_scores ON risk_scores
        USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
        """
    )

    conn = op.get_bind()
    conn.exec_driver_sql(f"GRANT SELECT, INSERT ON risk_scores TO {APP_ROLE}")


def downgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql(f"REVOKE SELECT, INSERT ON risk_scores FROM {APP_ROLE}")

    op.execute("DROP POLICY IF EXISTS tenant_isolation_risk_scores ON risk_scores")
    op.drop_index("ix_risk_scores_computed_at", table_name="risk_scores")
    op.drop_index("ix_risk_scores_asset_id", table_name="risk_scores")
    op.drop_index("ix_risk_scores_facility_id", table_name="risk_scores")
    op.drop_index("ix_risk_scores_tenant_id", table_name="risk_scores")
    op.drop_table("risk_scores")
