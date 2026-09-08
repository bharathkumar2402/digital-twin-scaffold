"""Unit tests for Settings.tile_url_template (issue 2.4): the MapLibre-style {z}/{x}/{y}
URL a browser uses to fetch tiles directly from MinIO, built from the host-facing
`minio_public_endpoint` rather than the in-Docker-network `minio_endpoint`."""

from app.core.config import Settings


def test_tile_url_template_uses_public_endpoint_not_internal_one() -> None:
    settings = Settings(
        minio_endpoint="minio:9000",
        minio_public_endpoint="localhost:9000",
        minio_tiles_bucket="facility-map-tiles",
    )

    url = settings.tile_url_template("tenant/facility/upload-id")

    assert url == "http://localhost:9000/facility-map-tiles/tenant/facility/upload-id/{z}/{x}/{y}.png"
    assert "minio:9000" not in url


def test_tile_url_template_uses_https_when_public_secure() -> None:
    settings = Settings(minio_public_endpoint="tiles.example.com", minio_public_secure=True)

    url = settings.tile_url_template("prefix")

    assert url.startswith("https://tiles.example.com/")
