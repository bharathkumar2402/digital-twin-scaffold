from app.sandbox.celery_app import sandbox_celery_app
from app.sandbox.storage import fetch_raw_upload


@sandbox_celery_app.task(name="receive_map_upload")
def receive_map_upload(upload_id: str, tenant_id: str, storage_key: str) -> None:
    """Skeleton for issue 2.1: proves the isolation boundary end to end without any
    real GDAL/sanitization logic yet (that's issue 2.2/2.3).

    Fetches the raw object over the network from MinIO (the only thing this container
    can reach besides Redis) and reports completion back to the main app by *task
    name* on the "sandbox-results" queue — it never imports or connects to Postgres.
    """
    fetch_raw_upload(storage_key)

    sandbox_celery_app.send_task(
        "record_sandbox_result",
        kwargs={
            "upload_id": upload_id,
            "tenant_id": tenant_id,
            "status": "processing",
            "detail": "received by sandbox worker (skeleton — no sanitization yet)",
        },
        queue="sandbox-results",
    )
