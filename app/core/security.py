import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from pwdlib import PasswordHash
from starlette.concurrency import run_in_threadpool

from app.core.config import Settings
from app.core.errors import AppError

password_hasher = PasswordHash.recommended()


async def hash_password(password: str) -> str:
    return await run_in_threadpool(password_hasher.hash, password)


async def verify_password(password: str, hashed: str) -> bool:
    return await run_in_threadpool(password_hasher.verify, password, hashed)


def opaque_token() -> str:
    return secrets.token_urlsafe(48)


def digest_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def access_token(user_id: uuid.UUID, session_id: uuid.UUID, version: int, settings: Settings) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(user_id),
            "sid": str(session_id),
            "ver": version,
            "type": "access",
            "iat": now,
            "nbf": now,
            "exp": now + timedelta(minutes=settings.access_token_minutes),
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
            "jti": str(uuid.uuid4()),
        },
        settings.secret_key.get_secret_value(),
        algorithm="HS256",
    )


def decode_access(token: str, settings: Settings) -> dict[str, Any]:
    try:
        payload = jwt.decode(
            token,
            settings.secret_key.get_secret_value(),
            algorithms=["HS256"],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={"require": ["sub", "sid", "ver", "exp", "iat", "nbf", "type", "jti"]},
        )
        if payload["type"] != "access" or type(payload["ver"]) is not int:
            raise ValueError("Wrong token type")
        if not isinstance(payload["sub"], str) or not isinstance(payload["sid"], str):
            raise ValueError("Invalid token identifiers")
        uuid.UUID(payload["sub"])
        uuid.UUID(payload["sid"])
        return payload
    except (jwt.PyJWTError, ValueError, TypeError) as exc:
        raise AppError(
            401, "invalid_token", "Invalid or expired access token.", {"WWW-Authenticate": "Bearer"}
        ) from exc
