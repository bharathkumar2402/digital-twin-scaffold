"""Tests for the `/ws/alerts` WebSocket endpoint (issue 3.6).

Runs against the real app via Starlette's `TestClient` (no Postgres/Timescale needed -
this endpoint never touches a DB session, see its docstring) with `get_redis_client`
monkeypatched to a shared `fakeredis` server so a message published in the test body
is observable by the endpoint's own subscription.
"""

import time
import uuid

import pytest
from fakeredis import aioredis as fakeredis
from fastapi.testclient import TestClient

import app.api.alerts_ws as alerts_ws_module
from app.core.security import create_access_token
from app.main import app
from app.schemas.ml.alert_notification import AlertNotification
from app.services.alert_publish_service import publish_alert

TENANT_A = uuid.uuid4()
TENANT_B = uuid.uuid4()


@pytest.fixture
def fake_redis_server() -> fakeredis.FakeServer:
    return fakeredis.FakeServer()


@pytest.fixture
def client(
    monkeypatch: pytest.MonkeyPatch, fake_redis_server: fakeredis.FakeServer
) -> TestClient:
    def _get_redis_client() -> fakeredis.FakeRedis:
        return fakeredis.FakeRedis(server=fake_redis_server, decode_responses=True)

    monkeypatch.setattr(alerts_ws_module, "get_redis_client", _get_redis_client)
    return TestClient(app)


def _token(tenant_id: uuid.UUID) -> str:
    return create_access_token(user_id=uuid.uuid4(), tenant_id=tenant_id, role="viewer")


def _notification(asset_id: uuid.UUID) -> AlertNotification:
    from datetime import UTC, datetime

    return AlertNotification(
        asset_id=asset_id,
        sensor_type="vibration",
        value=999.0,
        z_score=489.5,
        triggered_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_connect_without_a_token_is_rejected(client: TestClient) -> None:
    with pytest.raises(Exception):  # noqa: B017 - starlette raises on the 1008 close
        with client.websocket_connect("/ws/alerts"):
            pass


def test_connect_with_a_malformed_token_is_rejected(client: TestClient) -> None:
    with pytest.raises(Exception):  # noqa: B017
        with client.websocket_connect("/ws/alerts?token=not-a-real-jwt"):
            pass


def test_a_refresh_token_is_rejected_for_the_alerts_socket(client: TestClient) -> None:
    """`/ws/alerts` must require an *access* token specifically - accepting a refresh
    token here would let a client hold a long-lived alert subscription open well past
    a normal access-token lifetime."""
    from app.core.security import create_refresh_token

    token = create_refresh_token(user_id=uuid.uuid4(), tenant_id=TENANT_A, role="viewer")
    with pytest.raises(Exception):  # noqa: B017
        with client.websocket_connect(f"/ws/alerts?token={token}"):
            pass


def _publish(
    fake_redis_server: fakeredis.FakeServer, *, tenant_id: uuid.UUID, asset_id: uuid.UUID
) -> None:
    import asyncio

    async def _do() -> None:
        redis = fakeredis.FakeRedis(server=fake_redis_server, decode_responses=True)
        await publish_alert(redis, tenant_id=tenant_id, notification=_notification(asset_id))
        await redis.aclose()

    # A fresh loop in this (main) thread - the TestClient's WebSocket runs its own
    # ASGI app instance in a separate portal thread/loop, so this only ever needs to
    # talk to the shared `fake_redis_server`'s in-memory state, not that loop.
    asyncio.run(_do())


def test_a_published_alert_is_delivered_to_its_own_tenant(
    client: TestClient, fake_redis_server: fakeredis.FakeServer
) -> None:
    asset_id = uuid.uuid4()
    token = _token(TENANT_A)
    with client.websocket_connect(f"/ws/alerts?token={token}") as ws:
        _publish(fake_redis_server, tenant_id=TENANT_A, asset_id=asset_id)

        data = ws.receive_text()
        payload = AlertNotification.model_validate_json(data)
        assert payload.asset_id == asset_id


# Phase 3's DoD ("a manually injected anomaly produces a browser alert in under 2
# seconds") is bounded in two independent legs - see
# tests/cross_tenant/test_telemetry_isolation.py's
# ALERT_PUBLISH_LATENCY_BUDGET_SECONDS for the ingest -> publish leg. This is the
# other leg: Redis pub/sub message -> forwarded over the WebSocket to the browser.
WS_FORWARD_LATENCY_BUDGET_SECONDS = 0.5


def test_a_published_alert_is_forwarded_over_the_socket_within_the_latency_slo(
    client: TestClient, fake_redis_server: fakeredis.FakeServer
) -> None:
    asset_id = uuid.uuid4()
    token = _token(TENANT_A)
    with client.websocket_connect(f"/ws/alerts?token={token}") as ws:
        started_at = time.perf_counter()
        _publish(fake_redis_server, tenant_id=TENANT_A, asset_id=asset_id)

        data = ws.receive_text()
        elapsed = time.perf_counter() - started_at

        payload = AlertNotification.model_validate_json(data)
        assert payload.asset_id == asset_id
        assert elapsed < WS_FORWARD_LATENCY_BUDGET_SECONDS, (
            f"publish-to-forward latency was {elapsed:.3f}s, "
            f"over the {WS_FORWARD_LATENCY_BUDGET_SECONDS}s budget"
        )


def test_an_alert_for_another_tenant_is_never_delivered(
    client: TestClient, fake_redis_server: fakeredis.FakeServer
) -> None:
    """Cross-tenant isolation for the live delivery path - a socket authenticated for
    tenant A must never see a message published on tenant B's channel."""
    token_a = _token(TENANT_A)
    with client.websocket_connect(f"/ws/alerts?token={token_a}") as ws:
        _publish(fake_redis_server, tenant_id=TENANT_B, asset_id=uuid.uuid4())

        with pytest.raises(Exception):  # noqa: B017 - starlette's queue-empty timeout
            ws.receive_text(timeout=0.5)
