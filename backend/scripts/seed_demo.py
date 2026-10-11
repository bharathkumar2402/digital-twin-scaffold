"""Bootstraps a demo tenant/admin user/facility for manual testing.

Run once per fresh environment: `python -m scripts.seed_demo`. Uses the app's own
models/services so it exercises the same RLS-scoped code paths as the real API.
Prints the tenant_id, email and password for immediate use against /auth/login.
"""

import asyncio
import uuid

from sqlalchemy import select

from app.core.db import async_session_factory
from app.core.tenant_context import scope_session_to_tenant
from app.models.facility import Facility
from app.models.tenant import Tenant
from app.models.user import Role, User
from app.services.auth_service import EmailAlreadyRegisteredError, create_user_as_admin

DEMO_TENANT_NAME = "Twin Labs Demo"
# Fixed ids, so the demo login (tenant id) and every URL stay the same across rebuilds of the
# local stack. Demo-only: real tenants get random ids.
DEMO_TENANT_ID = uuid.UUID("d3500000-0000-4000-8000-000000000001")
DEMO_FACILITY_ID = uuid.UUID("d3500000-0000-4000-8000-000000000002")
DEMO_UPLOAD_ID = uuid.UUID("d3500000-0000-4000-8000-000000000003")
# One floor-plan upload per floor of the demo plant (level -> id); L0's is DEMO_UPLOAD_ID.
DEMO_FLOOR_UPLOAD_IDS: dict[int, uuid.UUID] = {
    0: DEMO_UPLOAD_ID,
    1: uuid.UUID("d3500000-0000-4000-8000-000000000004"),
    2: uuid.UUID("d3500000-0000-4000-8000-000000000005"),
}
DEMO_EMAIL = "demo@twinlabs.com"
DEMO_PASSWORD = "demo-password-123"
# Extra demo accounts (Phase L, D7/D8): one per role the UI and the ingest rule distinguish.
# The simulator account is a technician used only by scripts/live_simulator.py (it may write
# telemetry); the viewer is read-only and proves ingest is closed to it. Demo-only credentials.
DEMO_TECHNICIAN_EMAIL = "technician@twinlabs.com"
DEMO_VIEWER_EMAIL = "viewer@twinlabs.com"
DEMO_SIMULATOR_EMAIL = "simulator@twinlabs.com"
DEMO_EXTRA_PASSWORD = "demo-password-123"
DEMO_EXTRA_ACCOUNTS: tuple[tuple[str, Role], ...] = (
    (DEMO_TECHNICIAN_EMAIL, Role.TECHNICIAN),
    (DEMO_VIEWER_EMAIL, Role.VIEWER),
    (DEMO_SIMULATOR_EMAIL, Role.TECHNICIAN),
)


async def seed_extra_accounts(tenant_id: uuid.UUID) -> dict[str, Role]:
    """Idempotently ensure the technician, viewer and simulator accounts; returns email -> role."""
    ensured: dict[str, Role] = {}
    for email, role in DEMO_EXTRA_ACCOUNTS:
        async with async_session_factory() as session:
            try:
                await create_user_as_admin(
                    session,
                    tenant_id=tenant_id,
                    email=email,
                    password=DEMO_EXTRA_PASSWORD,
                    role=role,
                )
            except EmailAlreadyRegisteredError:
                pass  # already seeded on a prior run
        ensured[email] = role
    return ensured


async def seed() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Idempotently ensure the demo tenant/admin/facility; returns (tenant, user, facility) ids."""
    async with async_session_factory() as session:
        # Reuse the existing demo tenant if one is already seeded - previously this
        # created a brand-new tenant (and tenant_id) on every rerun, which left orphaned
        # duplicate "Twin Labs Demo" rows and made login fail with a generic "Invalid
        # email or password" whenever someone used a tenant_id from an earlier run.
        tenant_result = await session.execute(select(Tenant).where(Tenant.id == DEMO_TENANT_ID))
        tenant = tenant_result.scalars().first()
        if tenant is None:
            tenant = Tenant(id=DEMO_TENANT_ID, name=DEMO_TENANT_NAME, plan_tier="pro")
            session.add(tenant)
            await session.commit()
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
            facility = Facility(
                id=DEMO_FACILITY_ID, tenant_id=tenant_id, name="Meridian Components Plant"
            )
            session.add(facility)
            await session.commit()

    await seed_extra_accounts(tenant_id)
    return tenant_id, user.id, facility.id


async def main() -> None:
    tenant_id, user_id, facility_id = await seed()
    print(f"tenant_id={tenant_id}")
    print(f"user_id={user_id}")
    print(f"facility_id={facility_id}")
    print(f"email={DEMO_EMAIL}")
    print(f"password={DEMO_PASSWORD}")
    for email, role in DEMO_EXTRA_ACCOUNTS:
        print(f"{role.value}: {email} / {DEMO_EXTRA_PASSWORD}")


if __name__ == "__main__":
    asyncio.run(main())
