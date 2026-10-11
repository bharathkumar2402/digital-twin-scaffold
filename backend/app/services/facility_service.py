import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.facility import Facility
from app.models.facility_map_upload import FacilityMapUpload, UploadStatus


@dataclass(frozen=True)
class FloorInfo:
    level: int
    name: str
    map_upload_id: uuid.UUID
    asset_count: int


@dataclass(frozen=True)
class FacilityInfo:
    facility: Facility
    latest_map_upload_id: uuid.UUID | None
    asset_count: int
    floors: list[FloorInfo]


async def list_facilities(session: AsyncSession, *, tenant_id: uuid.UUID) -> list[FacilityInfo]:
    """The tenant's facilities with their floors (newest tiled map per level) and asset counts.

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
            select(
                FacilityMapUpload.facility_id,
                FacilityMapUpload.id,
                FacilityMapUpload.floor_level,
                FacilityMapUpload.floor_name,
            )
            .where(
                FacilityMapUpload.tenant_id == tenant_id,
                FacilityMapUpload.status == UploadStatus.TILED,
            )
            .order_by(FacilityMapUpload.created_at.desc())
        )
    ).all()
    # newest first: the first upload seen for each (facility, level) is the one that counts
    newest: dict[tuple[uuid.UUID, int], tuple[uuid.UUID, str | None]] = {}
    for facility_id, upload_id, level, floor_name in uploads:
        newest.setdefault((facility_id, level), (upload_id, floor_name))

    count_rows = (
        await session.execute(
            select(Asset.facility_id, Asset.floor_level, func.count(Asset.id))
            .where(Asset.tenant_id == tenant_id)
            .group_by(Asset.facility_id, Asset.floor_level)
        )
    ).all()
    per_floor: dict[tuple[uuid.UUID, int], int] = {
        (facility_id, level): int(n) for facility_id, level, n in count_rows
    }

    result: list[FacilityInfo] = []
    for facility in facilities:
        floors = [
            FloorInfo(
                level=level,
                name=floor_name or f"Level {level}",
                map_upload_id=upload_id,
                asset_count=per_floor.get((facility.id, level), 0),
            )
            for (fid, level), (upload_id, floor_name) in sorted(
                newest.items(), key=lambda item: item[0][1]
            )
            if fid == facility.id
        ]
        # open the ground floor first if there is one, else the lowest
        first = next((f for f in floors if f.level == 0), floors[0] if floors else None)
        result.append(
            FacilityInfo(
                facility=facility,
                latest_map_upload_id=first.map_upload_id if first else None,
                asset_count=sum(n for (fid, _), n in per_floor.items() if fid == facility.id),
                floors=floors,
            )
        )
    return result
