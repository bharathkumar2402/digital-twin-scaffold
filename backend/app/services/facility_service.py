import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.facility import Facility
from app.models.facility_map_upload import FacilityMapUpload, UploadStatus


async def list_facilities(
    session: AsyncSession, *, tenant_id: uuid.UUID
) -> list[tuple[Facility, uuid.UUID | None, int]]:
    """The tenant's facilities with their newest tiled map upload and asset count.

    Filters on `tenant_id` explicitly as well as relying on RLS (defense in depth, same as
    every other tenant-scoped service here).
    """
    facilities = (
        (
            await session.execute(
                select(Facility).where(Facility.tenant_id == tenant_id).order_by(Facility.name)
            )
        )
        .scalars()
        .all()
    )

    uploads = (
        await session.execute(
            select(FacilityMapUpload.facility_id, FacilityMapUpload.id)
            .where(
                FacilityMapUpload.tenant_id == tenant_id,
                FacilityMapUpload.status == UploadStatus.TILED,
            )
            .order_by(FacilityMapUpload.created_at.desc())
        )
    ).all()
    latest_upload: dict[uuid.UUID, uuid.UUID] = {}
    for facility_id, upload_id in uploads:  # newest first: keep the first seen
        latest_upload.setdefault(facility_id, upload_id)

    count_rows = (
        await session.execute(
            select(Asset.facility_id, func.count(Asset.id))
            .where(Asset.tenant_id == tenant_id)
            .group_by(Asset.facility_id)
        )
    ).all()
    counts: dict[uuid.UUID, int] = {facility_id: int(n) for facility_id, n in count_rows}

    return [(f, latest_upload.get(f.id), int(counts.get(f.id, 0))) for f in facilities]
