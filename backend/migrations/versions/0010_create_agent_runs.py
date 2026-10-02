"""create agent_runs, tenant-isolation RLS, app_role grants (issue 4.8)

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-02

Insert-only history table for 5-agent LangGraph executions:
captures full state snapshot, trigger, status, duration, validation errors,
decision report, and overall confidence per PROJECT_PLAN.md §5 and §4.4.
Enforces tenant isolation RLS with NULLIF session GUC guard per repo rule 2.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "app_role"


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("facility_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("trigger", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("state_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("validation_errors_json", sa.JSON(), nullable=False),
        sa.Column("decision_report", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["facility_id"], ["facilities.id"]),
    )
    op.create_index("ix_agent_runs_tenant_id", "agent_runs", ["tenant_id"])
    op.create_index("ix_agent_runs_facility_id", "agent_runs", ["facility_id"])
    op.create_index("ix_agent_runs_status", "agent_runs", ["status"])
    op.create_index("ix_agent_runs_created_at", "agent_runs", ["created_at"])

    op.execute("ALTER TABLE agent_runs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE agent_runs FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_agent_runs ON agent_runs
        USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
        """
    )

    conn = op.get_bind()
    conn.exec_driver_sql(f"GRANT SELECT, INSERT ON agent_runs TO {APP_ROLE}")


def downgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql(f"REVOKE SELECT, INSERT ON agent_runs FROM {APP_ROLE}")

    op.execute("DROP POLICY IF EXISTS tenant_isolation_agent_runs ON agent_runs")
    op.drop_index("ix_agent_runs_created_at", table_name="agent_runs")
    op.drop_index("ix_agent_runs_status", table_name="agent_runs")
    op.drop_index("ix_agent_runs_facility_id", table_name="agent_runs")
    op.drop_index("ix_agent_runs_tenant_id", table_name="agent_runs")
    op.drop_table("agent_runs")
