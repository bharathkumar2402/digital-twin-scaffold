import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import Tenant


class TenantNotFoundError(Exception):
    pass


async def create_tenant(session: AsyncSession, *, name: str, plan_tier: str) -> Tenant:
    tenant = Tenant(name=name, plan_tier=plan_tier)
    session.add(tenant)
    await session.commit()
    return tenant


async def list_tenants(session: AsyncSession) -> list[Tenant]:
    result = await session.execute(select(Tenant))
    return list(result.scalars().all())


async def update_tenant(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    name: str | None,
    plan_tier: str | None,
) -> Tenant:
    result = await session.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if tenant is None:
        raise TenantNotFoundError

    if name is not None:
        tenant.name = name
    if plan_tier is not None:
        tenant.plan_tier = plan_tier

    await session.commit()
    return tenant
