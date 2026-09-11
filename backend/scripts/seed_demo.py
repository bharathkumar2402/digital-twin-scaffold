"""Bootstraps a demo tenant/admin user/facility for manual testing.

Run once per fresh environment: `python -m scripts.seed_demo`. Uses the app's own
models/services so it exercises the same RLS-scoped code paths as the real API.
Prints the tenant_id, email and password for immediate use against /auth/login.
"""

import asyncio
import uuid

from app.core.db import async_session_factory
from app.core.tenant_context import scope_session_to_tenant
from app.models.facility import Facility
from app.models.user import Role
from app.services.auth_service import EmailAlreadyRegisteredError, create_user_as_admin
from app.services.tenant_service import create_tenant

DEMO_EMAIL = "demo@twinlabs.com"
DEMO_PASSWORD = "demo-password-123"


async def main() -> None:
    async with async_session_factory() as session:
        tenant = await create_tenant(session, name="Twin Labs Demo", plan_tier="pro")
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
            print("User already exists for this tenant run; rerun with a fresh DB if needed.")
            return

    async with async_session_factory() as session:
        await scope_session_to_tenant(session, tenant_id)
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
