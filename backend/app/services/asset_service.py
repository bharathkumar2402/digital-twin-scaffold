import uuid
from datetime import date
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.facility import Facility


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
) -> Asset:
    await _get_facility_or_404(session, tenant_id=tenant_id, facility_id=facility_id)

    asset = Asset(
        tenant_id=tenant_id,
        facility_id=facility_id,
        name=name,
        type=type,
        x=x,
        y=y,
        status=status_,
        installed_date=installed_date,
        manufacturer=manufacturer,
        model=model,
    )
    session.add(asset)
    await session.commit()
    await session.refresh(asset)
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
    for field, value in updates.items():
        setattr(asset, field, value)
    await session.commit()
    await session.refresh(asset)
    return asset


async def delete_asset(
    session: AsyncSession, *, tenant_id: uuid.UUID, facility_id: uuid.UUID, asset_id: uuid.UUID
) -> None:
    asset = await _get_asset_or_404(
        session, tenant_id=tenant_id, facility_id=facility_id, asset_id=asset_id
    )
    await session.delete(asset)
    await session.commit()
