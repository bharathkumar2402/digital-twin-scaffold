"""fix sensor_readings RLS policy to guard against the empty-string GUC placeholder bug

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-08

Same defect and same fix as migration 0008 in the main `migrations/` chain - see that
migration's docstring for the full root-cause writeup (confirmed directly against a
real Postgres instance: after a `SET LOCAL app.current_tenant_id` transaction commits,
`current_setting('app.current_tenant_id', true)` returns '' rather than NULL on that
same session/connection, so the policy's bare `::uuid` cast raises a hard error instead
of failing closed to 0 rows). `sensor_readings` uses the identical GUC-scoping pattern
(`get_timescale_scoped_session` in app/core/tenant_context.py) against this physically
separate TimescaleDB instance, so it carries the same bug and needs the same guard.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_GUC_EXPR_OLD = "current_setting('app.current_tenant_id', true)::uuid"
_GUC_EXPR_NEW = "NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.execute(
        f"""
        ALTER POLICY tenant_isolation_sensor_readings ON sensor_readings
        USING (tenant_id = {_GUC_EXPR_NEW})
        WITH CHECK (tenant_id = {_GUC_EXPR_NEW})
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        ALTER POLICY tenant_isolation_sensor_readings ON sensor_readings
        USING (tenant_id = {_GUC_EXPR_OLD})
        WITH CHECK (tenant_id = {_GUC_EXPR_OLD})
        """
    )
