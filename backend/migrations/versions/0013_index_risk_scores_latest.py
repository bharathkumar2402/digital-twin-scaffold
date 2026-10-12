"""index risk_scores for the newest-score-per-asset lookup (Phase L task L.4)

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-12

The scheduled scorer writes a `risk_scores` row per asset every minute, so history grows quickly and
`get_latest_risk_scores` (`DISTINCT ON (asset_id) ... ORDER BY asset_id, computed_at DESC`) must not
read all of it. This adds (facility_id, asset_id, computed_at DESC) so that lookup walks the index.

Index only: no table, column, RLS policy or grant changes (the forced tenant policy and the
`app_role` grants from 0009 stay as they are). tests/cross_tenant/test_scheduled_scoring.py checks
that the policy still isolates tenants with the index in place.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "ix_risk_scores_facility_asset_computed"


def upgrade() -> None:
    op.execute(
        f"CREATE INDEX IF NOT EXISTS {INDEX_NAME} "
        "ON risk_scores (facility_id, asset_id, computed_at DESC)"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {INDEX_NAME}")
