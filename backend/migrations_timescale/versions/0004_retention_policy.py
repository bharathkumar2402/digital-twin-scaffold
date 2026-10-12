"""drop sensor_readings chunks older than 14 days (Phase L task L.8, decision D10)

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-12

The live simulator writes about 1.3 M rows a day, and nothing ever deleted them. This registers
TimescaleDB's retention policy, a background job that drops whole chunks. Retention only: L.0
proved compression cannot be enabled on a table with row-level security, in either order.

What this does NOT touch: the table, its forced RLS, the `tenant_isolation_sensor_readings`
policy and the grants to `app_role` (still SELECT, INSERT only: the application can never delete
readings, the policy job runs as the table owner).

The first run is scheduled one hour after the migration, not immediately: a deploy should not
start deleting data the moment it finishes, and (found while testing) an immediate first run drops
fixed-date fixture rows out from under other suites' tests mid-run.

Two things to know:
* Granularity is the chunk (7 days by default). A chunk is dropped only when ALL of it is older
  than 14 days, so readings really live between 14 and 21 days.
* Feature engineering also asks for 90- and 365-day windows. For live data those windows can now
  only ever contain what is retained (14-21 days); that is the accepted cost of D10.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RETENTION = "14 days"
FIRST_RUN_DELAY = "1 hour"


def upgrade() -> None:
    op.execute(
        f"SELECT add_retention_policy('sensor_readings', INTERVAL '{RETENTION}', "
        f"if_not_exists => true, initial_start => now() + INTERVAL '{FIRST_RUN_DELAY}')"
    )


def downgrade() -> None:
    op.execute("SELECT remove_retention_policy('sensor_readings', if_exists => true)")
