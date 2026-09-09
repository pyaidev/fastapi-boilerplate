import json
import uuid
from datetime import UTC, datetime, timedelta

from cryptography.fernet import Fernet
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.core.security import access_token, digest_token, hash_password, opaque_token, verify_password
from app.models import AuditEvent, EmailOutbox, PasswordReset, RefreshSession, User
from app.modules.auth.schemas import Register, TokenPair


def audit(
    session: AsyncSession,
    user_id: uuid.UUID,
    action: str,
    request_id: str,
    resource_id: uuid.UUID | None = None,
) -> None:
    session.add(AuditEvent(actor_id=user_id, action=action, resource_id=resource_id, request_id=request_id))


async def register(session: AsyncSession, data: Register, request_id: str) -> User:
    user = User(
        email=str(data.email).lower(),
        name=data.name.strip(),
        password_hash=await hash_password(data.password.get_secret_value()),
    )
    session.add(user)
    try:
        await session.flush()
        audit(session, user.id, "user.registered", request_id)
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise AppError(409, "email_unavailable", "This email cannot be registered.") from exc
    return user


def new_session(
    session: AsyncSession, user: User, settings: Settings, family_id: uuid.UUID | None = None
) -> tuple[RefreshSession, TokenPair]:
    raw = opaque_token()
    record = RefreshSession(
        id=uuid.uuid4(),
        user_id=user.id,
        family_id=family_id or uuid.uuid4(),
        token_hash=digest_token(raw),
        expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_days),
    )
    session.add(record)
    pair = TokenPair(
        access_token=access_token(user.id, record.id, user.auth_version, settings),
        refresh_token=raw,
        expires_in=settings.access_token_minutes * 60,
    )
    return record, pair


async def login(
    session: AsyncSession, email: str, password: str, dummy_hash: str, settings: Settings, request_id: str
) -> TokenPair:
    user = await session.scalar(select(User).where(User.email == email.strip().lower()).with_for_update())
    valid = await verify_password(password, user.password_hash if user else dummy_hash)
    if not user or not valid or not user.is_active:
        raise AppError(
            401, "invalid_credentials", "Invalid email or password.", {"WWW-Authenticate": "Bearer"}
        )
    _, pair = new_session(session, user, settings)
    audit(session, user.id, "session.created", request_id)
    await session.commit()
    return pair


async def rotate(session: AsyncSession, raw: str, settings: Settings, request_id: str) -> TokenPair:
    record = await session.scalar(
        select(RefreshSession).where(RefreshSession.token_hash == digest_token(raw))
    )
    if not record:
        raise AppError(401, "invalid_refresh", "Invalid refresh token.")
    # Security operations lock the user first, then the token, in a consistent order.
    user = await session.get(User, record.user_id, with_for_update=True)
    await session.refresh(record, with_for_update=True)
    if record.revoked_at is not None:
        await session.execute(
            update(RefreshSession)
            .where(RefreshSession.family_id == record.family_id)
            .values(revoked_at=datetime.now(UTC))
        )
        audit(session, record.user_id, "session.refresh_reuse", request_id)
        await session.commit()  # Persist revocation even though the request is rejected.
        raise AppError(401, "refresh_reused", "Refresh token reuse detected. Log in again.")
    if not user or not user.is_active or record.expires_at <= datetime.now(UTC):
        raise AppError(401, "invalid_refresh", "Invalid or expired refresh token.")
    replacement, pair = new_session(session, user, settings, record.family_id)
    record.revoked_at = datetime.now(UTC)
    record.replaced_by = replacement.id
    audit(session, user.id, "session.rotated", request_id)
    await session.commit()
    return pair


async def logout(session: AsyncSession, current: RefreshSession, request_id: str) -> None:
    await session.get(User, current.user_id, with_for_update=True, populate_existing=True)
    await session.execute(
        update(RefreshSession)
        .where(RefreshSession.family_id == current.family_id)
        .values(revoked_at=datetime.now(UTC))
    )
    audit(session, current.user_id, "session.revoked", request_id)
    await session.commit()


async def request_reset(session: AsyncSession, email: str, settings: Settings, request_id: str) -> None:
    user = await session.scalar(select(User).where(User.email == email.lower(), User.is_active.is_(True)))
    if user:
        raw = opaque_token()
        session.add(
            PasswordReset(
                user_id=user.id,
                token_hash=digest_token(raw),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        # Fragments aren't sent to servers or access logs. A frontend consumes this token.
        reset_url = settings.public_base_url.rstrip("/") + "/reset-password#token=" + raw
        payload = json.dumps(
            {
                "subject": "Reset your password",
                "body": f"Open this link to reset your password within one hour:\n{reset_url}\n"
                "If you did not request this, ignore this email.",
            }
        )
        encrypted = (
            Fernet(settings.outbox_encryption_key.get_secret_value().encode())
            .encrypt(payload.encode())
            .decode()
        )
        session.add(EmailOutbox(recipient=user.email, encrypted_payload=encrypted))
        audit(session, user.id, "password.reset_requested", request_id)
        # Reset token and delivery intent commit together; a worker scans committed rows.
        await session.commit()


async def reset_password(session: AsyncSession, raw: str, password: str, request_id: str) -> None:
    record = await session.scalar(select(PasswordReset).where(PasswordReset.token_hash == digest_token(raw)))
    if not record:
        raise AppError(400, "invalid_reset", "Invalid or expired reset token.")
    user = await session.get(User, record.user_id, with_for_update=True)
    await session.refresh(record, with_for_update=True)
    if not user or not user.is_active or record.used_at or record.expires_at <= datetime.now(UTC):
        raise AppError(400, "invalid_reset", "Invalid or expired reset token.")
    user.password_hash = await hash_password(password)
    user.auth_version += 1
    now = datetime.now(UTC)
    await session.execute(update(PasswordReset).where(PasswordReset.user_id == user.id).values(used_at=now))
    await session.execute(
        update(RefreshSession).where(RefreshSession.user_id == user.id).values(revoked_at=now)
    )
    audit(session, user.id, "password.reset_completed", request_id)
    await session.commit()


async def change_password(
    session: AsyncSession, user_id: uuid.UUID, old: str, new: str, request_id: str
) -> None:
    user = await session.get(User, user_id, with_for_update=True, populate_existing=True)
    if not user or not await verify_password(old, user.password_hash):
        raise AppError(400, "invalid_password", "Current password is incorrect.")
    user.password_hash = await hash_password(new)
    user.auth_version += 1
    await session.execute(
        update(RefreshSession).where(RefreshSession.user_id == user.id).values(revoked_at=datetime.now(UTC))
    )
    await session.execute(
        update(PasswordReset).where(PasswordReset.user_id == user.id).values(used_at=datetime.now(UTC))
    )
    audit(session, user.id, "password.changed", request_id)
    await session.commit()
