"""Bootstraps a demo tenant/admin user/facility for manual testing.

Run once per fresh environment: `python -m scripts.seed_demo`. Uses the app's own
models/services so it exercises the same RLS-scoped code paths as the real API.
Prints the tenant_id, email and password for immediate use against /auth/login.
"""

import asyncio

from sqlalchemy import select

from app.core.db import async_session_factory
from app.core.tenant_context import scope_session_to_tenant
from app.models.facility import Facility
from app.models.tenant import Tenant
from app.models.user import Role, User
from app.services.auth_service import EmailAlreadyRegisteredError, create_user_as_admin
from app.services.tenant_service import create_tenant

DEMO_TENANT_NAME = "Twin Labs Demo"
DEMO_EMAIL = "demo@twinlabs.com"
DEMO_PASSWORD = "demo-password-123"


async def main() -> None:
    async with async_session_factory() as session:
        # Reuse the existing demo tenant if one is already seeded - previously this
        # created a brand-new tenant (and tenant_id) on every rerun, which left orphaned
        # duplicate "Twin Labs Demo" rows and made login fail with a generic "Invalid
        # email or password" whenever someone used a tenant_id from an earlier run.
        tenant_result = await session.execute(
            select(Tenant).where(Tenant.name == DEMO_TENANT_NAME)
        )
        tenant = tenant_result.scalars().first()
        if tenant is None:
            tenant = await create_tenant(session, name=DEMO_TENANT_NAME, plan_tier="pro")
        tenant_id = tenant.id

    async with async_session_factory() as session:
        try:
            user = await create_user_as_admin(
                session,
                tenant_id=tenant_id,
                email=DEMO_EMAIL,
                password=DEMO_PASSWORD,
                role=Role.TENANT_ADMIN,
            )
        except EmailAlreadyRegisteredError:
            # Already seeded on a prior run - look the user up instead of bailing out
            # silently, so the tenant_id/credentials to log in with are always printed.
            await scope_session_to_tenant(session, tenant_id)
            user_result = await session.execute(select(User).where(User.email == DEMO_EMAIL))
            user = user_result.scalar_one()

    async with async_session_factory() as session:
        await scope_session_to_tenant(session, tenant_id)
        facility_result = await session.execute(
            select(Facility).where(Facility.tenant_id == tenant_id)
        )
        facility = facility_result.scalars().first()
        if facility is None:
            facility = Facility(tenant_id=tenant_id, name="Demo Plant 1")
            session.add(facility)
            await session.commit()

    print(f"tenant_id={tenant_id}")
    print(f"user_id={user.id}")
    print(f"facility_id={facility.id}")
    print(f"email={DEMO_EMAIL}")
    print(f"password={DEMO_PASSWORD}")


if __name__ == "__main__":
    asyncio.run(main())
