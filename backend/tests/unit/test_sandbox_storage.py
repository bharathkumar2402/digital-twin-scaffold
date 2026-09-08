"""Unit tests for the anonymous-read bucket policy issue 2.4 attaches to the
facility-map-tiles bucket, so a tile is fetchable by direct URL without a dedicated
tile-server process (see infra/docker/docker-compose.yml)."""

import json
from pathlib import Path

import pytest

from app.sandbox import storage
from app.sandbox.config import sandbox_settings


class _FakeMinioClient:
    def __init__(self) -> None:
        self.existing_buckets: set[str] = set()
        self.made_buckets: list[str] = []
        self.policy_calls: list[tuple[str, str]] = []
        self.uploaded: list[tuple[str, str, str]] = []

    def bucket_exists(self, bucket: str) -> bool:
        return bucket in self.existing_buckets

    def make_bucket(self, bucket: str) -> None:
        self.made_buckets.append(bucket)
        self.existing_buckets.add(bucket)

    def set_bucket_policy(self, bucket: str, policy: str) -> None:
        self.policy_calls.append((bucket, policy))

    def fput_object(self, bucket: str, key: str, path: str) -> None:
        self.uploaded.append((bucket, key, path))


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> _FakeMinioClient:
    client = _FakeMinioClient()
    monkeypatch.setattr(storage, "get_minio_client", lambda: client)
    return client


def test_put_tile_pyramid_creates_bucket_if_missing(
    fake_client: _FakeMinioClient, tmp_path: Path
) -> None:
    (tmp_path / "0" / "0").mkdir(parents=True)
    (tmp_path / "0" / "0" / "0.png").write_bytes(b"fake-png")

    storage.put_tile_pyramid("some-prefix", tmp_path)

    assert fake_client.made_buckets == [sandbox_settings.minio_tiles_bucket]


def test_put_tile_pyramid_sets_anonymous_read_policy(
    fake_client: _FakeMinioClient, tmp_path: Path
) -> None:
    (tmp_path / "0" / "0").mkdir(parents=True)
    (tmp_path / "0" / "0" / "0.png").write_bytes(b"fake-png")

    storage.put_tile_pyramid("some-prefix", tmp_path)

    assert len(fake_client.policy_calls) == 1
    bucket, policy_json = fake_client.policy_calls[0]
    assert bucket == sandbox_settings.minio_tiles_bucket

    policy = json.loads(policy_json)
    statements = policy["Statement"]
    assert len(statements) == 1
    statement = statements[0]
    assert statement["Effect"] == "Allow"
    assert statement["Action"] == ["s3:GetObject"]
    assert statement["Resource"] == [f"arn:aws:s3:::{sandbox_settings.minio_tiles_bucket}/*"]


def test_anonymous_read_policy_grants_no_list_or_write_actions() -> None:
    policy = json.loads(storage._anonymous_read_policy("facility-map-tiles"))
    actions = policy["Statement"][0]["Action"]

    assert "s3:ListBucket" not in actions
    assert "s3:PutObject" not in actions
    assert "s3:DeleteObject" not in actions


def test_put_tile_pyramid_still_sets_policy_when_bucket_already_exists(
    fake_client: _FakeMinioClient, tmp_path: Path
) -> None:
    fake_client.existing_buckets.add(sandbox_settings.minio_tiles_bucket)
    (tmp_path / "0").mkdir()
    (tmp_path / "0" / "0.png").write_bytes(b"fake-png")

    storage.put_tile_pyramid("some-prefix", tmp_path)

    assert fake_client.made_buckets == []
    assert len(fake_client.policy_calls) == 1
