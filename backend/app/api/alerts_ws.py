import uuid

import anyio
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.core.redis_client import get_redis_client
from app.core.security import InvalidTokenError, TokenType, decode_token
from app.services.alert_publish_service import alert_channel

router = APIRouter(tags=["alerts"])


@router.websocket("/ws/alerts")
async def alerts_ws(websocket: WebSocket, token: str | None = Query(default=None)) -> None:
    """Forwards one tenant's Redis alert-channel messages (see
    `alert_publish_service.publish_alert`) to this browser connection verbatim, for
    the toast/alert UI (issue 3.6).

    Auth is a `?token=` query param, not the `Authorization` header the rest of the
    API uses (`get_bearer_token` / `get_tenant_context`) - browsers' WebSocket API has
    no way to set custom headers on the handshake request, so the access token has to
    ride in the URL instead. Rejected with `close(code=1008)` *before* `accept()` on a
    missing/invalid/expired token, same fail-closed posture as `get_tenant_context`'s
    401 for the REST routes.

    Subscribes only to `alerts:{tenant_id}` (`alert_publish_service.alert_channel`) -
    structurally cannot receive another tenant's alerts, since Redis pub/sub only ever
    delivers a message to subscribers of the exact channel it was published on. There
    is deliberately no tenant-scoped DB/session dependency here: this endpoint only
    ever reads from Redis, never from Postgres/Timescale, so there's no RLS GUC to set.
    """
    if token is None:
        await websocket.close(code=1008)
        return
    try:
        payload = decode_token(token, expected_type=TokenType.ACCESS)
    except InvalidTokenError:
        await websocket.close(code=1008)
        return

    tenant_id = uuid.UUID(payload["tenant_id"])
    channel = alert_channel(tenant_id)
    await websocket.accept()

    redis = get_redis_client()
    pubsub = redis.pubsub()
    await pubsub.subscribe(channel)

    async def _forward_alerts() -> None:
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            await websocket.send_text(message["data"])

    async def _watch_for_disconnect() -> None:
        # `websocket.receive()` (the raw/low-level call) returns the disconnect
        # message as data rather than raising - it's `receive_text`/`receive_bytes`/
        # `receive_json` that check the message type and raise `WebSocketDisconnect`,
        # so one of those has to be used here even though the client never actually
        # sends anything on this push-only channel.
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass

    try:
        # anyio task group, not raw asyncio.create_task/wait: matches the async
        # framework Starlette/the test client's blocking portal already run on, so
        # cancelling the loser here doesn't race the portal's own task bookkeeping.
        # Whichever of the two finishes first - the client disconnecting, or the
        # forward loop erroring out - cancels the whole group.
        async with anyio.create_task_group() as task_group:

            async def _run_forward() -> None:
                await _forward_alerts()
                task_group.cancel_scope.cancel()

            async def _run_watch() -> None:
                await _watch_for_disconnect()
                task_group.cancel_scope.cancel()

            task_group.start_soon(_run_forward)
            task_group.start_soon(_run_watch)
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()
        await redis.aclose()
