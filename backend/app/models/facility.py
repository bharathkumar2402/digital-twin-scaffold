import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin


class Facility(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "facilities"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    map_file_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    bounds_geojson: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
