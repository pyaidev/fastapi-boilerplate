import asyncio
import json
from unittest.mock import MagicMock

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from app.models import EmailOutbox, User
from tests.conftest import register_login

pytestmark = pytest.mark.integration


async def test_register_rejects_privilege_fields(client):
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "user@example.com",
            "name": "User",
            "password": "Long-safe-password-123!",
            "role": "admin",
        },
    )
    assert response.status_code == 422
    assert "Long-safe-password" not in response.text


async def test_register_hashes_password_and_normalizes_email(client):
    pair = await register_login(client, "Alice@Example.com")
    async with client.app.state.database.sessions() as session:
        user = await session.scalar(select(User))
        assert user.email == "alice@example.com"
        assert user.password_hash.startswith("$argon2id$")
        assert user.role == "user"
    response = await client.get(
        "/api/v1/auth/me", headers={"Authorization": "Bearer " + pair["access_token"]}
    )
    assert response.status_code == 200
    assert "password" not in response.text


async def test_duplicate_email_is_conflict(client):
    await register_login(client)
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "ALICE@example.com",
            "name": "Alice",
            "password": "Long-safe-password-123!",
        },
    )
    assert response.status_code == 409


async def test_login_invalid_and_missing_access(client):
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    response = await client.post(
        "/api/v1/auth/token", data={"username": "missing@example.com", "password": "wrong"}
    )
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


async def test_refresh_rotation_and_reuse_revokes_family(client):
    first = await register_login(client)
    response = await client.post("/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert response.status_code == 200
    second = response.json()
    assert second["refresh_token"] != first["refresh_token"]
    assert (
        await client.get("/api/v1/auth/me", headers={"Authorization": "Bearer " + first["access_token"]})
    ).status_code == 401
    reused = await client.post("/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert reused.status_code == 401
    assert reused.json()["error"]["code"] == "refresh_reused"
    assert (
        await client.get("/api/v1/auth/me", headers={"Authorization": "Bearer " + second["access_token"]})
    ).status_code == 401


async def test_concurrent_refresh_never_creates_two_active_sessions(client):
    pair = await register_login(client)
    responses = await asyncio.gather(
        *[
            client.post("/api/v1/auth/refresh", json={"refresh_token": pair["refresh_token"]})
            for _ in range(2)
        ]
    )
    assert sorted(response.status_code for response in responses) == [200, 401]
    fresh = next(response.json() for response in responses if response.status_code == 200)
    assert (
        await client.get("/api/v1/auth/me", headers={"Authorization": "Bearer " + fresh["access_token"]})
    ).status_code == 401


async def test_logout_revokes_access_and_refresh(client):
    pair = await register_login(client)
    headers = {"Authorization": "Bearer " + pair["access_token"]}
    assert (await client.post("/api/v1/auth/logout", headers=headers)).status_code == 204
    assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 401
    assert (
        await client.post("/api/v1/auth/refresh", json={"refresh_token": pair["refresh_token"]})
    ).status_code == 401


async def test_admin_role_is_enforced_from_database(client):
    pair = await register_login(client)
    headers = {"Authorization": "Bearer " + pair["access_token"]}
    assert (await client.get("/api/v1/admin/users", headers=headers)).status_code == 403
    async with client.app.state.database.sessions() as session:
        user = await session.scalar(select(User))
        user.role = "admin"
        await session.commit()
    assert (await client.get("/api/v1/admin/users", headers=headers)).status_code == 200


async def test_password_reset_outbox_is_encrypted_and_non_enumerating(client, settings):
    pair = await register_login(client)
    known = await client.post("/api/v1/auth/password-reset", json={"email": "alice@example.com"})
    unknown = await client.post("/api/v1/auth/password-reset", json={"email": "unknown@example.com"})
    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()
    async with client.app.state.database.sessions() as session:
        message = await session.scalar(select(EmailOutbox))
        assert "reset-password" not in message.encrypted_payload
        payload = json.loads(
            Fernet(settings.outbox_encryption_key.get_secret_value().encode()).decrypt(
                message.encrypted_payload.encode()
            )
        )
    raw = payload["body"].split("#token=")[1].split()[0]
    response = await client.post(
        "/api/v1/auth/password-reset/confirm",
        json={
            "token": raw,
            "password": "New-safe-password-123!",
        },
    )
    assert response.status_code == 200
    assert (
        await client.get("/api/v1/auth/me", headers={"Authorization": "Bearer " + pair["access_token"]})
    ).status_code == 401
    assert (
        await client.post(
            "/api/v1/auth/password-reset/confirm",
            json={
                "token": raw,
                "password": "New-safe-password-123!",
            },
        )
    ).status_code == 400


async def test_password_change_revokes_every_session(client):
    pair = await register_login(client)
    response = await client.post(
        "/api/v1/auth/password",
        headers={"Authorization": "Bearer " + pair["access_token"]},
        json={"current_password": "Long-safe-password-123!", "new_password": "Another-safe-password-123!"},
    )
    assert response.status_code == 200
    assert (
        await client.post("/api/v1/auth/refresh", json={"refresh_token": pair["refresh_token"]})
    ).status_code == 401


async def test_outbox_delivers_and_removes_secret(client, settings, monkeypatch):
    await register_login(client)
    await client.post("/api/v1/auth/password-reset", json={"email": "alice@example.com"})
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.infrastructure import tasks

    engine = create_engine(settings.database_url.get_secret_value().replace("+asyncpg", "+psycopg"))
    monkeypatch.setattr(tasks, "settings", settings)
    monkeypatch.setattr(tasks, "worker_sessions", lambda: sessionmaker(engine, expire_on_commit=False))
    smtp = MagicMock()
    monkeypatch.setattr(tasks.smtplib, "SMTP", smtp)
    try:
        assert await asyncio.to_thread(tasks.deliver_batch) == 1
        assert await asyncio.to_thread(tasks.deliver_batch) == 0
        smtp.return_value.__enter__.return_value.send_message.assert_called_once()
        async with client.app.state.database.sessions() as session:
            message = await session.scalar(select(EmailOutbox))
            assert message.sent_at is not None
            assert message.encrypted_payload == ""
    finally:
        engine.dispose()
