import tempfile
from pathlib import Path

from app.sandbox.celery_app import sandbox_celery_app
from app.sandbox.convert import TileConversionError, convert_to_tile_pyramid
from app.sandbox.sanitize import SanitizationError, sanitize_upload
from app.sandbox.storage import (
    fetch_raw_upload,
    fetch_sanitized_upload,
    put_sanitized_upload,
    put_tile_pyramid,
)


def _extension_of(storage_key: str) -> str:
    return storage_key.rsplit(".", 1)[-1] if "." in storage_key else ""


def _tile_prefix_of(storage_key: str) -> str:
    return storage_key.rsplit(".", 1)[0] if "." in storage_key else storage_key


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
    sandbox_celery_app.send_task(
        "convert_sanitized_map",
        kwargs={"upload_id": upload_id, "tenant_id": tenant_id, "storage_key": storage_key},
        queue="upload-sandbox",
    )


@sandbox_celery_app.task(name="convert_sanitized_map")
def convert_sanitized_map(upload_id: str, tenant_id: str, storage_key: str) -> None:
    """Task 2.3: fetches the sanitized object (never the raw one — see
    fetch_sanitized_upload), rasterizes it and builds an XYZ tile pyramid
    (app/sandbox/convert.py), then uploads the pyramid to the facility-map-tiles
    bucket under a prefix derived from the upload's storage key. Reports "tiled" or
    "conversion_failed" back to the main app the same way receive_map_upload does —
    never raises, so a bad file can't leave the DB row stuck at PROCESSING forever.
    """
    content = fetch_sanitized_upload(storage_key)
    extension = _extension_of(storage_key)
    tile_prefix = _tile_prefix_of(storage_key)

    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        try:
            convert_to_tile_pyramid(content, extension, out_dir)
        except TileConversionError as exc:
            _report_result(upload_id, tenant_id, status="conversion_failed", detail=str(exc))
            return
        put_tile_pyramid(tile_prefix, out_dir)

    _report_result(
        upload_id,
        tenant_id,
        status="tiled",
        detail="tile pyramid generated",
        tile_prefix=tile_prefix,
    )


def _report_result(
    upload_id: str,
    tenant_id: str,
    *,
    status: str,
    detail: str,
    tile_prefix: str | None = None,
) -> None:
    kwargs: dict[str, str | None] = {
        "upload_id": upload_id,
        "tenant_id": tenant_id,
        "status": status,
        "detail": detail,
    }
    if tile_prefix is not None:
        kwargs["tile_prefix"] = tile_prefix
    sandbox_celery_app.send_task(
        "record_sandbox_result",
        kwargs=kwargs,
        queue="sandbox-results",
    )
