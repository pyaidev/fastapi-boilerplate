import os
from collections.abc import AsyncIterator
from urllib.parse import urlparse

import httpx
import pytest
from asgi_lifespan import LifespanManager
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings
from app.main import create_app

TEST_KEY = "testing-only-secret-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ-abcdef"
ENCRYPTION_KEY = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        secret_key=TEST_KEY,
        outbox_encryption_key=ENCRYPTION_KEY,
        database_url=os.getenv(
            "TEST_DATABASE_URL", "postgresql+asyncpg://test:test@localhost/fastapi_test_unit"
        ),
        redis_url=os.getenv("TEST_REDIS_URL", "redis://localhost:6379/15"),
        allowed_hosts=["testserver", "localhost", "127.0.0.1"],
    )


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    if not os.getenv("TEST_DATABASE_URL"):
        pytest.skip("Use make test or scripts.test to run against an isolated PostgreSQL database")
    assert urlparse(settings.database_url.get_secret_value()).path.startswith("/fastapi_test_")
    assert urlparse(settings.redis_url.get_secret_value()).path == "/15"
    engine = create_async_engine(settings.database_url.get_secret_value(), poolclass=NullPool)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "TRUNCATE users, refresh_sessions, password_resets, notes, audit_events, email_outbox CASCADE"
            )
        )
    await engine.dispose()
    redis = Redis.from_url(settings.redis_url.get_secret_value())
    await redis.flushdb()
    await redis.aclose()
    app = create_app(settings)
    async with LifespanManager(app) as manager:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=manager.app, raise_app_exceptions=False),
            base_url="http://testserver",
        ) as http:
            http.app = app
            yield http


async def register_login(client: httpx.AsyncClient, email: str = "alice@example.com") -> dict:
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "name": "Alice",
            "password": "Long-safe-password-123!",
        },
    )
    assert response.status_code == 201, response.text
    response = await client.post(
        "/api/v1/auth/token", data={"username": email, "password": "Long-safe-password-123!"}
    )
    assert response.status_code == 200, response.text
    return response.json()
