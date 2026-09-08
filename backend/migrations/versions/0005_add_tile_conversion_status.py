"""add tiled/conversion_failed statuses and tile_prefix column (issue 2.3)

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-08

Task 2.3's GDAL conversion pipeline runs as a second sandbox stage after
sanitization (app/sandbox/tasks.py's `convert_sanitized_map`), so
`facility_map_uploads.status` needs two more terminal values: "tiled" (raster tile
pyramid built and uploaded to the facility-map-tiles MinIO bucket) and
"conversion_failed" (rasterization/tiling raised). `tile_prefix` records the
object-key prefix under which the tile pyramid lives, once known, for task 2.4's
tile server wiring to consume.

`ALTER TYPE ... ADD VALUE` cannot run inside the same transaction that later uses
the new value, so it's issued in its own autocommit block.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_STATUS_VALUES = ("tiled", "conversion_failed")


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for value in NEW_STATUS_VALUES:
            op.execute(
                f"ALTER TYPE facility_map_upload_status ADD VALUE IF NOT EXISTS '{value}'"
            )

    op.add_column(
        "facility_map_uploads",
        sa.Column("tile_prefix", sa.String(length=512), nullable=True),
    )


def downgrade() -> None:
    # Postgres has no ALTER TYPE ... DROP VALUE; downgrading the enum itself would
    # require rebuilding the type (drop+recreate), which isn't safe to do blindly
    # against a table that may hold rows using the new values. Only the column add
    # is reversible here.
    op.drop_column("facility_map_uploads", "tile_prefix")
