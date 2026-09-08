import uuid

from sqlalchemy import CheckConstraint, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin


class AssetDependency(UUIDPKMixin, TimestampMixin, Base):
    """A directed edge in an asset's dependency graph: `parent_asset_id` DEPENDS ON
    `child_asset_id` (child is upstream). This direction matters for Phase 4's cascade
    simulation - "what if <child> fails?" walks edges where `child_asset_id` matches the
    failed asset and follows `parent_asset_id` recursively to find downstream-impacted
    assets, per PHASE_PLAN.md's Phase 4 DoD.
    """

    __tablename__ = "asset_dependencies"
    __table_args__ = (
        UniqueConstraint(
            "parent_asset_id", "child_asset_id", name="uq_asset_dependencies_edge"
        ),
        CheckConstraint(
            "parent_asset_id != child_asset_id", name="ck_asset_dependencies_no_self_loop"
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    facility_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("facilities.id"), nullable=False, index=True
    )
    parent_asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.id"), nullable=False, index=True
    )
    child_asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.id"), nullable=False, index=True
    )
