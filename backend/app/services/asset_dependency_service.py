import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.asset_dependency import AssetDependency
from app.models.facility import Facility


async def _get_facility_or_404(
    session: AsyncSession, *, tenant_id: uuid.UUID, facility_id: uuid.UUID
) -> Facility:
    result = await session.execute(
        select(Facility).where(Facility.id == facility_id, Facility.tenant_id == tenant_id)
    )
    facility = result.scalar_one_or_none()
    if facility is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Facility not found")
    return facility


async def _get_asset_in_facility_or_404(
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Asset {asset_id} not found in facility"
        )
    return asset


async def _would_create_cycle(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    facility_id: uuid.UUID,
    new_parent_id: uuid.UUID,
    new_child_id: uuid.UUID,
) -> bool:
    """Adding `new_parent depends_on new_child` creates a cycle iff `new_parent` is
    already reachable from `new_child` by following existing depends_on edges - i.e.
    new_child (transitively) already depends on new_parent. BFS over child_asset_id
    starting at new_child, following parent->child edges outward."""
    result = await session.execute(
        select(AssetDependency.parent_asset_id, AssetDependency.child_asset_id).where(
            AssetDependency.tenant_id == tenant_id, AssetDependency.facility_id == facility_id
        )
    )
    edges = result.all()
    children_of: dict[uuid.UUID, list[uuid.UUID]] = {}
    for parent_id, child_id in edges:
        children_of.setdefault(parent_id, []).append(child_id)

    visited: set[uuid.UUID] = set()
    queue = [new_child_id]
    while queue:
        current = queue.pop()
        if current == new_parent_id:
            return True
        if current in visited:
            continue
        visited.add(current)
        queue.extend(children_of.get(current, []))
    return False


async def create_dependency(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    facility_id: uuid.UUID,
    parent_asset_id: uuid.UUID,
    child_asset_id: uuid.UUID,
) -> AssetDependency:
    await _get_facility_or_404(session, tenant_id=tenant_id, facility_id=facility_id)

    if parent_asset_id == child_asset_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="An asset cannot depend on itself"
        )

    await _get_asset_in_facility_or_404(
        session, tenant_id=tenant_id, facility_id=facility_id, asset_id=parent_asset_id
    )
    await _get_asset_in_facility_or_404(
        session, tenant_id=tenant_id, facility_id=facility_id, asset_id=child_asset_id
    )

    existing = await session.execute(
        select(AssetDependency).where(
            AssetDependency.tenant_id == tenant_id,
            AssetDependency.facility_id == facility_id,
            AssetDependency.parent_asset_id == parent_asset_id,
            AssetDependency.child_asset_id == child_asset_id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="This dependency already exists"
        )

    if await _would_create_cycle(
        session,
        tenant_id=tenant_id,
        facility_id=facility_id,
        new_parent_id=parent_asset_id,
        new_child_id=child_asset_id,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This dependency would create a cycle in the asset graph",
        )

    dependency = AssetDependency(
        tenant_id=tenant_id,
        facility_id=facility_id,
        parent_asset_id=parent_asset_id,
        child_asset_id=child_asset_id,
    )
    session.add(dependency)
    # No session.refresh() here - deliberately. app/core/db.py's session factory sets
    # expire_on_commit=False, so `dependency`'s attributes (including the server-side
    # `id`/`created_at` defaults, populated via Postgres's implicit RETURNING on
    # insert) are already correct after commit(). A refresh() here would issue a new
    # SELECT in a fresh transaction where the RLS-scoping GUC (`SET LOCAL
    # app.current_tenant_id`, set per-request by scope_session_to_tenant) has gone out
    # of scope along with the commit - see migration 0008 for the RLS-policy half of
    # this bug and why that SELECT would otherwise see 0 rows / crash.
    await session.commit()
    return dependency


async def list_dependencies(
    session: AsyncSession, *, tenant_id: uuid.UUID, facility_id: uuid.UUID
) -> list[AssetDependency]:
    await _get_facility_or_404(session, tenant_id=tenant_id, facility_id=facility_id)

    result = await session.execute(
        select(AssetDependency).where(
            AssetDependency.facility_id == facility_id, AssetDependency.tenant_id == tenant_id
        )
    )
    return list(result.scalars().all())


async def delete_dependency(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    facility_id: uuid.UUID,
    dependency_id: uuid.UUID,
) -> None:
    result = await session.execute(
        select(AssetDependency).where(
            AssetDependency.id == dependency_id,
            AssetDependency.facility_id == facility_id,
            AssetDependency.tenant_id == tenant_id,
        )
    )
    dependency = result.scalar_one_or_none()
    if dependency is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dependency not found")
    await session.delete(dependency)
    await session.commit()
