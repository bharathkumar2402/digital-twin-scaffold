import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, verify_password
from app.core.tenant_context import scope_session_to_tenant
from app.models.user import Role, User


class EmailAlreadyRegisteredError(Exception):
    pass


class InvalidCredentialsError(Exception):
    pass


async def register_user(
    session: AsyncSession, *, tenant_id: uuid.UUID, email: str, password: str
) -> User:
    await scope_session_to_tenant(session, tenant_id)

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

    # No session.refresh() here: id/created_at are server_default columns, populated via
    # RETURNING on flush, and expire_on_commit=False keeps them on `user` after commit.
    # A refresh would run a second SELECT in a new transaction, after the transaction-
    # local `app.current_tenant_id` GUC from scope_session_to_tenant above has already
    # expired at commit — which the RLS policy then rejects.
    return user


async def authenticate_user(
    session: AsyncSession, *, tenant_id: uuid.UUID, email: str, password: str
) -> User:
    await scope_session_to_tenant(session, tenant_id)

    result = await session.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if user is None or not verify_password(password, user.hashed_password):
        raise InvalidCredentialsError

    return user


async def get_user_by_id(
    session: AsyncSession, *, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> User | None:
    await scope_session_to_tenant(session, tenant_id)

    result = await session.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()
