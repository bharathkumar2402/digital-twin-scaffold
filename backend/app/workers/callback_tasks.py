import asyncio
import uuid

from sqlalchemy import update

from app.core.celery_app import celery_app
from app.core.db import async_session_factory
from app.core.tenant_context import scope_session_to_tenant
from app.models.facility_map_upload import FacilityMapUpload

VALID_STATUSES = {
    "pending",
    "processing",
    "sanitized",
    "tiled",
    "failed",
    "conversion_failed",
}


@celery_app.task(name="record_sandbox_result")
def record_sandbox_result(
    upload_id: str,
    tenant_id: str,
    status: str,
    detail: str | None = None,
    tile_prefix: str | None = None,
) -> None:
    """Runs only in the main app's `celery-worker` (has DB credentials) — the sandbox
    worker (app/sandbox/**) never writes to Postgres itself, it only sends this task
    by name onto the "sandbox-results" queue with a narrow payload.
    """
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid upload status from sandbox: {status!r}")
    asyncio.run(
        _record_sandbox_result_async(upload_id, tenant_id, status, detail, tile_prefix)
    )


async def _record_sandbox_result_async(
    upload_id: str,
    tenant_id: str,
    status: str,
    detail: str | None,
    tile_prefix: str | None,
) -> None:
    values: dict[str, str | None] = {"status": status, "status_detail": detail}
    if tile_prefix is not None:
        values["tile_prefix"] = tile_prefix

    async with async_session_factory() as session:
        # Scoped by the tenant_id the job was enqueued with, so this write is subject
        # to the same fail-closed RLS policy (migration 0004) as any tenant-initiated
        # write — a compromised or buggy sandbox job still can't touch another
        # tenant's upload row.
        await scope_session_to_tenant(session, uuid.UUID(tenant_id))
        await session.execute(
            update(FacilityMapUpload)
            .where(FacilityMapUpload.id == uuid.UUID(upload_id))
            .values(**values)
        )
        await session.commit()
