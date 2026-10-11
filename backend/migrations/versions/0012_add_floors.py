"""add floors: assets.floor_level, facility_map_uploads.floor_level / floor_name

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-11

A facility can have several floors, each with its own floor-plan map. A floor is identified by an
integer level (0 = ground, negative = below, positive = above) and a map upload carries the level
(and an optional display name) it depicts; an asset records the level it stands on.

No new table, so no new RLS policy: both tables keep their existing policies and grants (adding a
column changes neither), and tests/cross_tenant/ already covers them. Existing rows get level 0,
so single-floor facilities behave exactly as before.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEVEL_CHECK = "floor_level BETWEEN -10 AND 100"


def upgrade() -> None:
    op.add_column(
        "assets", sa.Column("floor_level", sa.Integer(), nullable=False, server_default="0")
    )
    op.create_check_constraint("ck_assets_floor_level_range", "assets", LEVEL_CHECK)
    op.add_column(
        "facility_map_uploads",
        sa.Column("floor_level", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("facility_map_uploads", sa.Column("floor_name", sa.String(100), nullable=True))
    op.create_check_constraint(
        "ck_facility_map_uploads_floor_level_range", "facility_map_uploads", LEVEL_CHECK
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_facility_map_uploads_floor_level_range", "facility_map_uploads", type_="check"
    )
    op.drop_column("facility_map_uploads", "floor_name")
    op.drop_column("facility_map_uploads", "floor_level")
    op.drop_constraint("ck_assets_floor_level_range", "assets", type_="check")
    op.drop_column("assets", "floor_level")
