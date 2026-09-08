import enum
import uuid
from datetime import date

from sqlalchemy import Date, Enum, Float, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin


class AssetStatus(enum.StrEnum):
    OPERATIONAL = "operational"
    MAINTENANCE = "maintenance"
    OFFLINE = "offline"


class Asset(UUIDPKMixin, TimestampMixin, Base):
    """A physical asset (pump, conveyor, HVAC unit, ...) placed on a facility's map.

    `(x, y)` are in the same local-pixel coordinate space as the facility's tile
    pyramid (see facility_map_upload.py's tile_prefix comment and
    app/sandbox/convert.py) - floor plans have no real-world CRS, so these are plain
    pixel offsets from the raster tile origin, not lat/lon.
    """

    __tablename__ = "assets"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    facility_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("facilities.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(100), nullable=False)
    x: Mapped[float] = mapped_column(Float, nullable=False)
    y: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[AssetStatus] = mapped_column(
        Enum(
            AssetStatus,
            name="asset_status",
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=False,
        default=AssetStatus.OPERATIONAL,
    )
    installed_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    manufacturer: Mapped[str | None] = mapped_column(String(255), nullable=True)
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
