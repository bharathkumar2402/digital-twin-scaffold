import enum
import uuid

from sqlalchemy import Enum, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin


class UploadStatus(enum.StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    SANITIZED = "sanitized"
    TILED = "tiled"
    FAILED = "failed"
    CONVERSION_FAILED = "conversion_failed"


class FacilityMapUpload(UUIDPKMixin, TimestampMixin, Base):
    """Tracks a raw floor-plan upload through the sandbox pipeline.

    The row is created by the main API with status=PENDING before the file ever
    reaches the sandbox worker, and updated only by `app/workers/callback_tasks.py`
    (which runs in the main API's own Celery worker, not the sandbox) — the sandbox
    process never holds DB credentials, see app/sandbox/**.
    """

    __tablename__ = "facility_map_uploads"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    facility_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("facilities.id"), nullable=False, index=True
    )
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    # Object storage key in the raw-uploads bucket (app/core/storage.py), not a local
    # filesystem path — the API and sandbox containers share no filesystem.
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    format: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[UploadStatus] = mapped_column(
        Enum(
            UploadStatus,
            name="facility_map_upload_status",
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=False,
        default=UploadStatus.PENDING,
    )
    status_detail: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    # Object-key prefix in the facility-map-tiles MinIO bucket (task 2.3's GDAL
    # conversion pipeline) under which the {z}/{x}/{y}.png tile pyramid lives, in
    # local pixel coordinates -- floor plans have no real-world CRS, so tiles are
    # generated with gdal2tiles.py's "raster" profile rather than reprojected to
    # WGS84 (see app/sandbox/convert.py and PROJECT_PLAN.md §6). Set once tiling
    # succeeds; null until then.
    tile_prefix: Mapped[str | None] = mapped_column(String(512), nullable=True)
