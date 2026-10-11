import uuid
from datetime import date
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.facility import Facility
from app.models.facility_map_upload import FacilityMapUpload, UploadStatus


async def _get_facility_or_404(
    session: AsyncSession, *, tenant_id: uuid.UUID, facility_id: uuid.UUID
) -> Facility:
    # Explicit tenant_id filter, not just RLS - same defense-in-depth pattern as
    # facility_map_service.py (session is already RLS-scoped, but this doesn't rely
    # on that alone).
    result = await session.execute(
        select(Facility).where(Facility.id == facility_id, Facility.tenant_id == tenant_id)
    )
    facility = result.scalar_one_or_none()
    if facility is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Facility not found")
    return facility


async def _get_asset_or_404(
    session: AsyncSession, *, tenant_id: uuid.UUID, facility_id: uuid.UUID, asset_id: uuid.UUID
) -> Asset:
    result = await session.execute(
        select(Asset).where(
            Asset.id == asset_id,
            Asset.facility_id == facility_id,
            Asset.tenant_id == tenant_id,
        )
    )
    asset = result.scalar_one_or_none()
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
    return asset


async def ensure_floor_exists(
    session: AsyncSession, *, tenant_id: uuid.UUID, facility_id: uuid.UUID, floor_level: int
) -> None:
    """Reject an asset placed on a floor the facility does not have.

    A facility's floors are the levels of its finished (tiled) map uploads. A facility with no
    finished map yet has no floors to check against, so any level is accepted (the map comes
    first in the normal flow; this only guards facilities that already have floors).
    """
    levels = set(
        (
            await session.execute(
                select(FacilityMapUpload.floor_level)
                .where(
                    FacilityMapUpload.tenant_id == tenant_id,
                    FacilityMapUpload.facility_id == facility_id,
                    FacilityMapUpload.status == UploadStatus.TILED,
                )
                .distinct()
            )
        )
        .scalars()
        .all()
    )
    if levels and floor_level not in levels:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"This facility has no floor {floor_level} (floors: {sorted(levels)})",
        )


async def create_asset(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    facility_id: uuid.UUID,
    name: str,
    type: str,
    x: float,
    y: float,
    status_: str,
    installed_date: date | None,
    manufacturer: str | None,
    model: str | None,
    floor_level: int = 0,
) -> Asset:
    await _get_facility_or_404(session, tenant_id=tenant_id, facility_id=facility_id)
    await ensure_floor_exists(
        session, tenant_id=tenant_id, facility_id=facility_id, floor_level=floor_level
    )

    asset = Asset(
        tenant_id=tenant_id,
        facility_id=facility_id,
        name=name,
        type=type,
        floor_level=floor_level,
        x=x,
        y=y,
        status=status_,
        installed_date=installed_date,
        manufacturer=manufacturer,
        model=model,
    )
    session.add(asset)
    # No session.refresh() here - deliberately. See update_asset's comment below: it
    # would issue a new SELECT after the RLS-scoping GUC's SET LOCAL has gone out of
    # scope along with commit(), which is unnecessary anyway since expire_on_commit=False
    # (app/core/db.py) already keeps `asset`'s attributes - including server-side
    # `id`/`created_at` defaults, populated via Postgres's implicit RETURNING - correct
    # after commit().
    await session.commit()
    return asset


async def list_assets(
    session: AsyncSession, *, tenant_id: uuid.UUID, facility_id: uuid.UUID
) -> list[Asset]:
    await _get_facility_or_404(session, tenant_id=tenant_id, facility_id=facility_id)

    result = await session.execute(
        select(Asset).where(Asset.facility_id == facility_id, Asset.tenant_id == tenant_id)
    )
    return list(result.scalars().all())


async def get_asset(
    session: AsyncSession, *, tenant_id: uuid.UUID, facility_id: uuid.UUID, asset_id: uuid.UUID
) -> Asset:
    return await _get_asset_or_404(
        session, tenant_id=tenant_id, facility_id=facility_id, asset_id=asset_id
    )


async def update_asset(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    facility_id: uuid.UUID,
    asset_id: uuid.UUID,
    updates: dict[str, Any],
) -> Asset:
    asset = await _get_asset_or_404(
        session, tenant_id=tenant_id, facility_id=facility_id, asset_id=asset_id
    )
    if updates.get("floor_level") is not None:
        await ensure_floor_exists(
            session,
            tenant_id=tenant_id,
            facility_id=facility_id,
            floor_level=updates["floor_level"],
        )
    for field, value in updates.items():
        setattr(asset, field, value)
    # No session.refresh() here either - `asset`'s attributes already reflect the
    # setattr() calls above and expire_on_commit=False keeps them from being
    # invalidated by commit(); a refresh() would issue a redundant SELECT after the
    # RLS-scoping GUC has gone out of scope (see create_asset's comment and migration
    # 0008 - found live-verifying issue 2.7, this had been silently broken since 2.6
    # whenever a real, non-bypass-RLS role hit this path, which every real deployment
    # does; the integration tests never caught it because they connect as an
    # RLS-bypassing admin role, not app_role).
    await session.commit()
    return asset


async def delete_asset(
    session: AsyncSession, *, tenant_id: uuid.UUID, facility_id: uuid.UUID, asset_id: uuid.UUID
) -> None:
    asset = await _get_asset_or_404(
        session, tenant_id=tenant_id, facility_id=facility_id, asset_id=asset_id
    )
    await session.delete(asset)
    await session.commit()
