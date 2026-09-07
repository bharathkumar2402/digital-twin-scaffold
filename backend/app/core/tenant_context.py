import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import InvalidTokenError, TokenType, decode_token

BEARER_PREFIX = "Bearer "


@dataclass(frozen=True)
class TenantContext:
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    role: str


async def scope_session_to_tenant(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Sets the RLS session GUC for this transaction (SET LOCAL semantics).

    Transaction-scoped (`is_local=true`), not a bare SET, so the value never survives
    past the current transaction and can't bleed into the next request that happens to
    reuse this connection from the pool.
    """
    await session.execute(
        text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
        {"tenant_id": str(tenant_id)},
    )


def get_bearer_token(authorization: str | None = Header(default=None)) -> str:
    if authorization is None or not authorization.startswith(BEARER_PREFIX):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token"
        )
    return authorization[len(BEARER_PREFIX) :]


def get_tenant_context(token: str = Depends(get_bearer_token)) -> TenantContext:
    try:
        payload = decode_token(token, expected_type=TokenType.ACCESS)
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired access token"
        ) from exc

    return TenantContext(
        tenant_id=uuid.UUID(payload["tenant_id"]),
        user_id=uuid.UUID(payload["sub"]),
        role=payload["role"],
    )


async def get_tenant_scoped_session(
    session: AsyncSession = Depends(get_session),
    context: TenantContext = Depends(get_tenant_context),
) -> AsyncGenerator[AsyncSession, None]:
    await scope_session_to_tenant(session, context.tenant_id)
    yield session
