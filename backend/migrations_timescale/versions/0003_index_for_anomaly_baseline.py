"""index sensor_readings for the live anomaly baseline lookup (Phase L task L.2b)

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-12

`anomaly_detection_service` looks up, per (asset, sensor), the newest readings before a cutoff.
The existing indexes are (asset_id, timestamp), which has to read every sensor's rows of the asset
and filter afterwards, and (tenant_id). This adds (asset_id, sensor_type, timestamp DESC) so each
lookup is a short backward index scan. Index only: the table, its RLS policy (forced) and the
grants to `app_role` are not touched. On a hypertable CREATE INDEX is applied to every chunk.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "ix_sensor_readings_asset_sensor_time"


def upgrade() -> None:
    op.execute(
        f"CREATE INDEX IF NOT EXISTS {INDEX_NAME} "
        "ON sensor_readings (asset_id, sensor_type, timestamp DESC)"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {INDEX_NAME}")
