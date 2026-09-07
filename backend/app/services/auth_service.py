import uuid

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, verify_password
from app.models.user import Role, User


class EmailAlreadyRegisteredError(Exception):
    pass


class InvalidCredentialsError(Exception):
    pass


async def _scope_session_to_tenant(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Sets the RLS session GUC for this transaction (SET LOCAL semantics).

    Required so the `tenant_isolation_users` RLS policy (see migration 0001)
    permits the query — every users-table access here goes through this.
    """
    await session.execute(
        text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
        {"tenant_id": str(tenant_id)},
    )


async def register_user(
    session: AsyncSession, *, tenant_id: uuid.UUID, email: str, password: str
) -> User:
    await _scope_session_to_tenant(session, tenant_id)

    user = User(
        tenant_id=tenant_id,
        email=email,
        role=Role.VIEWER,
        hashed_password=hash_password(password),
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise EmailAlreadyRegisteredError from exc

    await session.refresh(user)
    return user


async def authenticate_user(
    session: AsyncSession, *, tenant_id: uuid.UUID, email: str, password: str
) -> User:
    await _scope_session_to_tenant(session, tenant_id)

    result = await session.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if user is None or not verify_password(password, user.hashed_password):
        raise InvalidCredentialsError

    return user


async def get_user_by_id(
    session: AsyncSession, *, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> User | None:
    await _scope_session_to_tenant(session, tenant_id)

    result = await session.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()
