import io

from minio import Minio

from app.core.config import settings

_client: Minio | None = None


def get_minio_client() -> Minio:
    global _client
    if _client is None:
        _client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
    return _client


def put_raw_upload(storage_key: str, content: bytes) -> None:
    """Writes untrusted floor-plan bytes straight to object storage.

    Never touches the API container's local filesystem — the sandbox worker fetches
    the object over the network from here, so the two containers share no filesystem
    or DB access, only this bucket and the Celery/Redis queue.
    """
    client = get_minio_client()
    bucket = settings.minio_raw_uploads_bucket
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
    client.put_object(bucket, storage_key, io.BytesIO(content), length=len(content))
