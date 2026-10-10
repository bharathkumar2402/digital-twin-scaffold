"""Object-store access for raw vibration waveform snapshots (Phase 3b, task 3b.8).

Waveforms (20,480 samples each) are far too large for `sensor_readings`, so they live in
MinIO as `.npy` objects keyed `<tenant_id>/<asset_id>/<channel>/<epoch_ms>.npy`. Tenant
isolation here is by key prefix, enforced by always building the prefix from the caller's
`tenant_id` (object storage has no RLS); no function takes a free-form prefix or key.

Arrays are read with `allow_pickle=False` and shape-checked, so a corrupt or hostile object
cannot execute code or smuggle a wrong-sized snapshot into the feature pipeline.
"""

from __future__ import annotations

import io
import uuid
from datetime import datetime

import numpy as np

from app.core.config import settings
from app.core.storage import get_minio_client
from app.ml.vibration_features import N_SAMPLES


def _prefix(tenant_id: uuid.UUID, asset_id: uuid.UUID, channel: int | None = None) -> str:
    base = f"{tenant_id}/{asset_id}/"
    return base if channel is None else f"{base}{int(channel)}/"


def _key(tenant_id: uuid.UUID, asset_id: uuid.UUID, channel: int, stamp: datetime) -> str:
    return f"{_prefix(tenant_id, asset_id, channel)}{int(stamp.timestamp() * 1000):015d}.npy"


def encode_waveform(waveform: np.ndarray) -> bytes:
    arr = np.asarray(waveform, dtype=np.float32)
    if arr.shape != (N_SAMPLES,):
        raise ValueError(f"expected shape ({N_SAMPLES},), got {arr.shape}")
    if not np.isfinite(arr).all():
        raise ValueError("waveform contains NaN or infinite values")
    buffer = io.BytesIO()
    np.save(buffer, arr, allow_pickle=False)
    return buffer.getvalue()


def decode_waveform(data: bytes) -> np.ndarray:
    arr = np.load(io.BytesIO(data), allow_pickle=False)
    if arr.shape != (N_SAMPLES,):
        raise ValueError(f"stored waveform has shape {arr.shape}, expected ({N_SAMPLES},)")
    return np.asarray(arr, dtype=np.float64)


def _ensure_bucket() -> str:
    client = get_minio_client()
    bucket = settings.minio_waveforms_bucket
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
    return bucket


def put_snapshot(
    tenant_id: uuid.UUID,
    asset_id: uuid.UUID,
    channel: int,
    stamp: datetime,
    waveform: np.ndarray,
) -> str:
    data = encode_waveform(waveform)
    key = _key(tenant_id, asset_id, channel, stamp)
    get_minio_client().put_object(_ensure_bucket(), key, io.BytesIO(data), length=len(data))
    return key


def list_channels(tenant_id: uuid.UUID, asset_id: uuid.UUID) -> list[int]:
    client = get_minio_client()
    prefix = _prefix(tenant_id, asset_id)
    channels: set[int] = set()
    for obj in client.list_objects(settings.minio_waveforms_bucket, prefix=prefix):
        name = str(obj.object_name)[len(prefix) :].split("/", 1)[0]
        if name.isdigit():
            channels.add(int(name))
    return sorted(channels)


def load_channel_window(
    tenant_id: uuid.UUID,
    asset_id: uuid.UUID,
    channel: int,
    *,
    n_baseline: int,
    n_recent: int,
) -> tuple[list[np.ndarray], list[np.ndarray]]:
    """(baseline, recent): the channel's earliest `n_baseline` snapshots, and the latest
    `n_recent` of those that come after the baseline. Oldest first."""
    client = get_minio_client()
    bucket = settings.minio_waveforms_bucket
    prefix = _prefix(tenant_id, asset_id, channel)
    keys = sorted(
        str(o.object_name)
        for o in client.list_objects(bucket, prefix=prefix)
        if str(o.object_name).endswith(".npy")
    )
    wanted = [*keys[:n_baseline], *keys[n_baseline:][-n_recent:]]
    arrays: dict[str, np.ndarray] = {}
    for key in wanted:
        response = client.get_object(bucket, key)
        try:
            arrays[key] = decode_waveform(response.read())
        finally:
            response.close()
            response.release_conn()
    return [arrays[k] for k in keys[:n_baseline]], [
        arrays[k] for k in keys[n_baseline:][-n_recent:]
    ]
