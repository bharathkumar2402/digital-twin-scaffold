import uuid

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_session
from app.core.security import (
    InvalidTokenError,
    TokenType,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.schemas.requests.auth import (
    AccessTokenResponse,
    LoginRequest,
    RegisterRequest,
    UserResponse,
)
from app.services.auth_service import (
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    authenticate_user,
    get_user_by_id,
    register_user,
)

router = APIRouter(tags=["auth"])

REFRESH_COOKIE_NAME = "refresh_token"
REFRESH_COOKIE_PATH = "/refresh"


def _set_refresh_cookie(
    response: Response, *, user_id: uuid.UUID, tenant_id: uuid.UUID, role: str
) -> None:
    refresh_token = create_refresh_token(user_id=user_id, tenant_id=tenant_id, role=role)
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=refresh_token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path=REFRESH_COOKIE_PATH,
    )


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest, session: AsyncSession = Depends(get_session)
) -> UserResponse:
    try:
        user = await register_user(
            session, tenant_id=payload.tenant_id, email=payload.email, password=payload.password
        )
    except EmailAlreadyRegisteredError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email already registered for this tenant"
        ) from exc

    return UserResponse.model_validate(user)


@router.post("/login", response_model=AccessTokenResponse)
async def login(
    payload: LoginRequest, response: Response, session: AsyncSession = Depends(get_session)
) -> AccessTokenResponse:
    try:
        user = await authenticate_user(
            session, tenant_id=payload.tenant_id, email=payload.email, password=payload.password
        )
    except InvalidCredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
        ) from exc

    access_token = create_access_token(
        user_id=user.id, tenant_id=user.tenant_id, role=user.role.value
    )
    _set_refresh_cookie(response, user_id=user.id, tenant_id=user.tenant_id, role=user.role.value)
    return AccessTokenResponse(access_token=access_token)


@router.post("/refresh", response_model=AccessTokenResponse)
async def refresh(
    response: Response,
    session: AsyncSession = Depends(get_session),
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE_NAME),
) -> AccessTokenResponse:
    if refresh_token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing refresh token"
        )

    try:
        payload = decode_token(refresh_token, expected_type=TokenType.REFRESH)
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired refresh token"
        ) from exc

    tenant_id = uuid.UUID(payload["tenant_id"])
    user_id = uuid.UUID(payload["sub"])

    user = await get_user_by_id(session, tenant_id=tenant_id, user_id=user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User no longer exists"
        )

    access_token = create_access_token(
        user_id=user.id, tenant_id=user.tenant_id, role=user.role.value
    )
    _set_refresh_cookie(response, user_id=user.id, tenant_id=user.tenant_id, role=user.role.value)
    return AccessTokenResponse(access_token=access_token)
