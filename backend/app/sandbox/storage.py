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
