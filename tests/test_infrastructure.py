import asyncio
import uuid
from unittest.mock import AsyncMock

import pytest
from redis.exceptions import ConnectionError

from app.core.errors import AppError
from app.core.rate_limit import enforce_limit

pytestmark = pytest.mark.integration


async def test_health_docs_and_private_metrics(client):
    for path in [
        "/health/live",
        "/health/ready",
        "/docs",
        "/openapi.json",
        "/reset-password",
        "/static/reset.js",
    ]:
        assert (await client.get(path)).status_code == 200
    assert (await client.get("/metrics")).status_code == 404


async def test_auth_responses_are_not_cached(client):
    response = await client.get("/api/v1/auth/me")
    assert response.headers["cache-control"] == "no-store"
    assert len(response.headers["x-request-id"]) == 32


async def test_host_and_body_limits(client, settings):
    assert (await client.get("/health/live", headers={"host": "evil.example"})).status_code == 400
    response = await client.post("/api/v1/auth/register", content=b"x" * (settings.max_request_bytes + 1))
    assert response.status_code == 413


async def test_chunked_body_cannot_bypass_size_limit(client, settings):
    async def body():
        for _ in range(3):
            yield b"x" * (settings.max_request_bytes // 2)

    assert (await client.post("/api/v1/auth/register", content=body())).status_code == 413


async def test_login_rate_limit_and_retry_header(client, settings):
    settings.rate_login = "2/minute"
    for _ in range(2):
        assert (
            await client.post("/api/v1/auth/token", data={"username": "no@example.com", "password": "bad"})
        ).status_code == 401
    response = await client.post("/api/v1/auth/token", data={"username": "no@example.com", "password": "bad"})
    assert response.status_code == 429
    assert int(response.headers["retry-after"]) > 0


async def test_atomic_redis_limits_under_concurrency(client):
    identity = uuid.uuid4().hex

    async def attempt():
        try:
            await enforce_limit(client.app.state.redis, "concurrency-test", identity, "5/second")
            return True
        except AppError as exc:
            assert exc.status == 429
            return False

    results = await asyncio.gather(*[attempt() for _ in range(30)])
    assert sum(results) == 5
    await asyncio.sleep(1.1)
    await enforce_limit(client.app.state.redis, "concurrency-test", identity, "5/second")


async def test_redis_outage_returns_503(client, monkeypatch):
    monkeypatch.setattr(
        client.app.state.redis, "eval", AsyncMock(side_effect=ConnectionError("private-host"))
    )
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 503
    assert "private-host" not in response.text


async def test_forwarded_ip_cannot_bypass_limit(client, settings):
    settings.rate_ip = "1/minute"
    assert (await client.get("/api/v1/auth/me", headers={"x-real-ip": "192.0.2.1"})).status_code == 401
    assert (await client.get("/api/v1/auth/me", headers={"x-real-ip": "192.0.2.2"})).status_code == 429
