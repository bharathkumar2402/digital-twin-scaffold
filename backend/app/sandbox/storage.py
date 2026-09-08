import io
from pathlib import Path

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


def fetch_sanitized_upload(storage_key: str) -> bytes:
    """Task 2.3's GDAL conversion pipeline reads from the sanitized bucket only —
    never the raw-uploads bucket — so a file that skipped/failed sanitization can
    never reach rasterization/tiling by accident."""
    client = get_minio_client()
    response = client.get_object(sandbox_settings.minio_sanitized_bucket, storage_key)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def put_tile_pyramid(prefix: str, tile_dir: Path) -> None:
    """Uploads every file under `tile_dir` (a gdal2tiles.py raster-profile output
    directory: {z}/{x}/{y}.png plus a couple of html/xml preview files) into the
    facility-map-tiles bucket under `prefix`, preserving the z/x/y layout the tile
    server (task 2.4) expects to serve directly."""
    client = get_minio_client()
    bucket = sandbox_settings.minio_tiles_bucket
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
    for file_path in tile_dir.rglob("*"):
        if file_path.is_file():
            relative_key = file_path.relative_to(tile_dir).as_posix()
            client.fput_object(bucket, f"{prefix}/{relative_key}", str(file_path))
