import io

from minio import Minio

from app.sandbox.config import sandbox_settings

_client: Minio | None = None


def get_minio_client() -> Minio:
    global _client
    if _client is None:
        _client = Minio(
            sandbox_settings.minio_endpoint,
            access_key=sandbox_settings.minio_access_key,
            secret_key=sandbox_settings.minio_secret_key,
            secure=sandbox_settings.minio_secure,
        )
    return _client


def fetch_raw_upload(storage_key: str) -> bytes:
    client = get_minio_client()
    response = client.get_object(sandbox_settings.minio_raw_uploads_bucket, storage_key)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def put_sanitized_upload(storage_key: str, content: bytes) -> None:
    """Writes sanitized bytes to a separate bucket from the raw upload — task 2.3's
    GDAL conversion pipeline reads from here, never from the raw-uploads bucket, so a
    file that skipped sanitization can never reach GDAL by accident."""
    client = get_minio_client()
    bucket = sandbox_settings.minio_sanitized_bucket
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
    client.put_object(bucket, storage_key, io.BytesIO(content), length=len(content))
