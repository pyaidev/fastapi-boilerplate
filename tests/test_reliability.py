import asyncio
import base64
import json
import ssl
from unittest.mock import AsyncMock, MagicMock

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.cli import create_admin
from app.core.errors import AppError
from app.models import AuditEvent, EmailOutbox, Note, RefreshSession, User
from app.modules.auth import service
from tests.conftest import register_login

pytestmark = pytest.mark.integration


async def test_create_admin_is_explicit_and_cannot_promote_existing_account(client):
    pair = await register_login(client)
    database = client.app.state.database
    with pytest.raises(IntegrityError):
        await create_admin(database, "alice@example.com", "Admin", "Safe-admin-password-123!")
    identifier = await create_admin(database, "admin@example.com", "Admin", "Safe-admin-password-123!")
    async with database.sessions() as session:
        assert (await session.get(User, identifier)).role == "admin"
        assert (
            await session.scalar(
                select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "admin.created")
            )
            == 1
        )
    response = await client.get(
        "/api/v1/auth/me", headers={"Authorization": "Bearer " + pair["access_token"]}
    )
    assert response.json()["role"] == "user"


async def test_logout_cannot_race_refresh_into_an_active_family(client, settings):
    pair = await register_login(client)

    async def logout():
        async with client.app.state.database.sessions() as session:
            record = await session.scalar(select(RefreshSession))
            await service.logout(session, record, "a" * 32)

    async def rotate():
        async with client.app.state.database.sessions() as session:
            try:
                await service.rotate(session, pair["refresh_token"], settings, "b" * 32)
            except AppError as exc:
                assert exc.status == 401

    await asyncio.gather(logout(), rotate())
    async with client.app.state.database.sessions() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(RefreshSession).where(RefreshSession.revoked_at.is_(None))
            )
            == 0
        )


async def test_inactive_user_cannot_login_refresh_or_use_access(client):
    pair = await register_login(client)
    async with client.app.state.database.sessions() as session:
        user = await session.scalar(select(User))
        user.is_active = False
        await session.commit()
    assert (
        await client.get("/api/v1/auth/me", headers={"Authorization": "Bearer " + pair["access_token"]})
    ).status_code == 401
    assert (
        await client.post("/api/v1/auth/refresh", json={"refresh_token": pair["refresh_token"]})
    ).status_code == 401
    assert (
        await client.post(
            "/api/v1/auth/token",
            data={"username": "alice@example.com", "password": "Long-safe-password-123!"},
        )
    ).status_code == 401


async def test_concurrent_password_reset_consumes_token_once(client, settings):
    await register_login(client)
    await client.post("/api/v1/auth/password-reset", json={"email": "alice@example.com"})
    async with client.app.state.database.sessions() as session:
        message = await session.scalar(select(EmailOutbox))
        payload = json.loads(
            Fernet(settings.outbox_encryption_key.get_secret_value().encode()).decrypt(
                message.encrypted_payload.encode()
            )
        )
    raw = payload["body"].split("#token=")[1].split()[0]
    responses = await asyncio.gather(
        *[
            client.post(
                "/api/v1/auth/password-reset/confirm",
                json={"token": raw, "password": "New-safe-password-123!"},
            )
            for _ in range(2)
        ]
    )
    assert sorted(response.status_code for response in responses) == [200, 400]


async def test_failed_audit_rolls_back_domain_write_and_redacts_error(client, monkeypatch):
    pair = await register_login(client)

    def fail(*args, **kwargs):
        raise RuntimeError("private-database-details")

    monkeypatch.setattr("app.modules.notes.router.audit", fail)
    response = await client.post(
        "/api/v1/notes",
        json={"title": "Should not persist"},
        headers={"Authorization": "Bearer " + pair["access_token"]},
    )
    assert response.status_code == 500
    assert response.json()["error"]["request_id"] == response.headers["x-request-id"]
    assert "private-database-details" not in response.text
    async with client.app.state.database.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Note)) == 0


async def test_malformed_cursor_types_are_validation_errors(client):
    pair = await register_login(client)
    for value in [["2026-01-01T00:00:00+00:00", 123], {}, [], ["date", "uuid"]]:
        cursor = base64.urlsafe_b64encode(json.dumps(value).encode()).decode()
        response = await client.get(
            "/api/v1/notes",
            params={"cursor": cursor},
            headers={"Authorization": "Bearer " + pair["access_token"]},
        )
        assert response.status_code == 422


async def test_readiness_checks_dependencies_without_hiding_liveness(client, monkeypatch):
    monkeypatch.setattr(client.app.state.database, "ping", AsyncMock(side_effect=OSError("private-host")))
    response = await client.get("/health/ready")
    assert response.status_code == 503
    assert "private-host" not in response.text
    assert (await client.get("/health/live")).status_code == 200


async def test_outbox_failed_delivery_stops_at_ten_attempts_and_verifies_tls(client, settings, monkeypatch):
    from app.infrastructure import tasks

    settings.email_use_tls = True
    settings.email_user = "test-user"
    payload = (
        Fernet(settings.outbox_encryption_key.get_secret_value().encode())
        .encrypt(json.dumps({"subject": "Test", "body": "private-message"}).encode())
        .decode()
    )
    async with client.app.state.database.sessions() as session:
        session.add(EmailOutbox(recipient="test@example.com", encrypted_payload=payload, attempts=9))
        await session.commit()
    engine = create_engine(settings.database_url.get_secret_value().replace("+asyncpg", "+psycopg"))
    monkeypatch.setattr(tasks, "settings", settings)
    monkeypatch.setattr(tasks, "worker_sessions", lambda: sessionmaker(engine, expire_on_commit=False))
    smtp = MagicMock()
    connection = smtp.return_value.__enter__.return_value
    connection.send_message.side_effect = OSError("private-smtp-details")
    monkeypatch.setattr(tasks.smtplib, "SMTP", smtp)
    try:
        assert await asyncio.to_thread(tasks.deliver_batch) == 0
        assert await asyncio.to_thread(tasks.deliver_batch) == 0
        connection.send_message.assert_called_once()
        context = connection.starttls.call_args.kwargs["context"]
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname is True
        async with client.app.state.database.sessions() as session:
            row = await session.scalar(select(EmailOutbox))
            assert row.attempts == 10 and row.sent_at is None
            assert row.encrypted_payload == payload
    finally:
        engine.dispose()
