import uuid
from collections.abc import AsyncIterator, Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import Depends, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.core.rate_limit import client_ip, enforce_limit
from app.core.security import decode_access
from app.models import RefreshSession, User

oauth2 = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token", auto_error=False)


def settings_for(request: Request) -> Settings:
    return request.app.state.settings


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.database.sessions() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


Session = Annotated[AsyncSession, Depends(get_session)]
Config = Annotated[Settings, Depends(settings_for)]


@dataclass
class Identity:
    user: User
    session: RefreshSession


async def current_identity(
    request: Request, session: Session, settings: Config, token: Annotated[str | None, Depends(oauth2)]
) -> Identity:
    if not token:
        raise AppError(
            401, "authentication_required", "Authentication is required.", {"WWW-Authenticate": "Bearer"}
        )
    payload = decode_access(token, settings)
    await enforce_limit(request.app.state.redis, "user", payload["sub"], settings.rate_user)
    result = await session.execute(
        select(User, RefreshSession)
        .join(RefreshSession, RefreshSession.user_id == User.id)
        .where(
            User.id == uuid.UUID(payload["sub"]),
            RefreshSession.id == uuid.UUID(payload["sid"]),
            RefreshSession.revoked_at.is_(None),
            RefreshSession.expires_at > datetime.now(UTC),
            User.is_active.is_(True),
            User.auth_version == payload["ver"],
        )
    )
    row = result.first()
    if row is None:
        raise AppError(
            401, "session_revoked", "This session is no longer valid.", {"WWW-Authenticate": "Bearer"}
        )
    return Identity(user=row[0], session=row[1])


CurrentIdentity = Annotated[Identity, Depends(current_identity)]


async def require_admin(identity: CurrentIdentity) -> Identity:
    if identity.user.role != "admin":
        raise AppError(403, "forbidden", "Administrator permission is required.")
    return identity


AdminIdentity = Annotated[Identity, Depends(require_admin)]


def endpoint_limit(scope: str) -> Callable[[Request, Settings], Coroutine[Any, Any, None]]:
    async def check(request: Request, settings: Config) -> None:
        await enforce_limit(
            request.app.state.redis, scope, client_ip(request, settings), getattr(settings, f"rate_{scope}")
        )

    return check
