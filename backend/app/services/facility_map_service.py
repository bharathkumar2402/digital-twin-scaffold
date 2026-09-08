import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.storage import put_raw_upload
from app.models.facility import Facility
from app.models.facility_map_upload import FacilityMapUpload, UploadStatus

ALLOWED_EXTENSIONS = {".svg", ".dxf", ".pdf"}


def _extension_of(filename: str) -> str:
    return "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


async def create_map_upload(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    facility_id: uuid.UUID,
    filename: str,
    content: bytes,
) -> FacilityMapUpload:
    # `session` is already RLS-scoped to `tenant_id` (get_tenant_scoped_session), but
    # this filters on tenant_id explicitly too rather than relying on `session.get`
    # alone — RLS is the primary defense, this is defense-in-depth for the case where
    # a session ever ends up unscoped (e.g. a BYPASSRLS admin connection), which would
    # otherwise silently make every tenant's facilities "visible" to this check.
    result = await session.execute(
        select(Facility).where(Facility.id == facility_id, Facility.tenant_id == tenant_id)
    )
    facility = result.scalar_one_or_none()
    if facility is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Facility not found")

    if len(content) == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty file")

    extension = _extension_of(filename)
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type '{extension}'. Allowed: {sorted(ALLOWED_EXTENSIONS)}",
        )

    if len(content) > settings.max_map_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"File exceeds the {settings.max_map_upload_bytes}-byte upload limit",
        )

    upload_id = uuid.uuid4()
    storage_key = f"{tenant_id}/{facility_id}/{upload_id}{extension}"
    put_raw_upload(storage_key, content)

    upload = FacilityMapUpload(
        id=upload_id,
        tenant_id=tenant_id,
        facility_id=facility_id,
        original_filename=filename,
        storage_key=storage_key,
        format=extension.lstrip("."),
        status=UploadStatus.PENDING,
    )
    session.add(upload)
    # No session.refresh() here - `id` is set explicitly above, and expire_on_commit=False
    # (app/core/db.py) keeps every other attribute (including `created_at`, populated via
    # RETURNING on flush) correct on `upload` after commit(). A refresh() would run a
    # second SELECT in a new transaction after the RLS-scoping GUC (SET LOCAL
    # app.current_tenant_id) has gone out of scope along with the commit above - same bug
    # as asset_service.py/asset_dependency_service.py, found live-verifying issue 2.7; see
    # migration 0008 for the RLS-policy half of the fix and auth_service.py's register_user
    # for the pattern this already correctly avoided.
    await session.commit()

    # Hand off by task name + a narrow payload (ids and a storage key) only — never
    # file bytes, never DB credentials. app/sandbox/** is a separate process/container
    # that fetches the object itself over the network.
    celery_app.send_task(
        "receive_map_upload",
        kwargs={
            "upload_id": str(upload_id),
            "tenant_id": str(tenant_id),
            "storage_key": storage_key,
        },
        queue="upload-sandbox",
    )

    return upload


async def get_map_upload(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    facility_id: uuid.UUID,
    upload_id: uuid.UUID,
) -> FacilityMapUpload:
    # Same explicit-tenant_id-filter-plus-RLS defense-in-depth as create_map_upload:
    # `session` is already RLS-scoped, but this doesn't rely on that alone.
    result = await session.execute(
        select(FacilityMapUpload).where(
            FacilityMapUpload.id == upload_id,
            FacilityMapUpload.facility_id == facility_id,
            FacilityMapUpload.tenant_id == tenant_id,
        )
    )
    upload = result.scalar_one_or_none()
    if upload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload not found")
    return upload
