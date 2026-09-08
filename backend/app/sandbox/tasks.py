from app.sandbox.celery_app import sandbox_celery_app
from app.sandbox.sanitize import SanitizationError, sanitize_upload
from app.sandbox.storage import fetch_raw_upload, put_sanitized_upload


def _extension_of(storage_key: str) -> str:
    return storage_key.rsplit(".", 1)[-1] if "." in storage_key else ""


@sandbox_celery_app.task(name="receive_map_upload")
def receive_map_upload(upload_id: str, tenant_id: str, storage_key: str) -> None:
    """Fetches the raw object from MinIO, sanitizes/validates it (issue 2.2 — strips
    SVG active content, structurally validates DXF/PDF, enforces size limits), and
    reports the outcome back to the main app by *task name* on the "sandbox-results"
    queue. Never imports or connects to Postgres; never lets a rejected file reach the
    GDAL conversion pipeline (task 2.3), which reads only from the sanitized bucket.
    """
    content = fetch_raw_upload(storage_key)
    extension = _extension_of(storage_key)

    try:
        sanitized = sanitize_upload(content, extension)
    except SanitizationError as exc:
        _report_result(upload_id, tenant_id, status="failed", detail=str(exc))
        return

    put_sanitized_upload(storage_key, sanitized)
    _report_result(
        upload_id,
        tenant_id,
        status="sanitized",
        detail="passed sanitization/validation",
    )


def _report_result(upload_id: str, tenant_id: str, *, status: str, detail: str) -> None:
    sandbox_celery_app.send_task(
        "record_sandbox_result",
        kwargs={
            "upload_id": upload_id,
            "tenant_id": tenant_id,
            "status": status,
            "detail": detail,
        },
        queue="sandbox-results",
    )
