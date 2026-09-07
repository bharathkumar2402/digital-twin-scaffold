import time
import uuid

import pytest
from jose import jwt

from app.core import security
from app.core.config import settings
from app.core.security import (
    InvalidTokenError,
    TokenType,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)


def test_hash_password_round_trip() -> None:
    hashed = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", hashed)


def test_verify_password_rejects_wrong_password() -> None:
    hashed = hash_password("correct horse battery staple")
    assert not verify_password("wrong password", hashed)


def test_hash_password_is_salted() -> None:
    assert hash_password("same-password") != hash_password("same-password")


def test_create_and_decode_access_token() -> None:
    user_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    token = create_access_token(user_id=user_id, tenant_id=tenant_id, role="viewer")

    payload = decode_token(token, expected_type=TokenType.ACCESS)
    assert payload["sub"] == str(user_id)
    assert payload["tenant_id"] == str(tenant_id)
    assert payload["role"] == "viewer"
    assert payload["type"] == "access"


def test_create_and_decode_refresh_token() -> None:
    user_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    token = create_refresh_token(user_id=user_id, tenant_id=tenant_id, role="technician")

    payload = decode_token(token, expected_type=TokenType.REFRESH)
    assert payload["type"] == "refresh"


def test_decode_token_rejects_wrong_type() -> None:
    token = create_access_token(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), role="viewer")
    with pytest.raises(InvalidTokenError):
        decode_token(token, expected_type=TokenType.REFRESH)


def test_decode_token_rejects_tampered_signature() -> None:
    token = create_access_token(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), role="viewer")
    tampered = token[:-4] + "abcd"
    with pytest.raises(InvalidTokenError):
        decode_token(tampered, expected_type=TokenType.ACCESS)


def test_decode_token_rejects_expired_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "access_token_expire_minutes", 0)
    token = create_access_token(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), role="viewer")
    time.sleep(1)
    with pytest.raises(InvalidTokenError):
        decode_token(token, expected_type=TokenType.ACCESS)


def test_decode_token_rejects_wrong_secret() -> None:
    forged = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "tenant_id": str(uuid.uuid4()),
            "role": "viewer",
            "type": "access",
        },
        "not-the-real-secret",
        algorithm=security.settings.jwt_algorithm,
    )
    with pytest.raises(InvalidTokenError):
        decode_token(forged, expected_type=TokenType.ACCESS)
