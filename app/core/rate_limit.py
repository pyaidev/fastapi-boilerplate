import hashlib
import ipaddress
import math
from collections.abc import Awaitable
from typing import cast

from redis.asyncio import Redis
from redis.exceptions import RedisError
from starlette.requests import Request

from app.core.config import Settings
from app.core.errors import AppError

COUNTER = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('PEXPIRE', KEYS[1], ARGV[1]) end
return {count, redis.call('PTTL', KEYS[1])}
"""


def client_ip(request: Request, settings: Settings) -> str:
    peer = request.client.host if request.client else "unknown"
    if peer in settings.trusted_proxy_ips:
        try:
            return str(ipaddress.ip_address(request.headers.get("x-real-ip", "")))
        except ValueError:
            pass
    return peer


async def enforce_limit(redis: Redis, scope: str, identity: str, rate: str) -> None:
    maximum, period = rate.split("/")
    duration = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}[period]
    key = f"fastapi:rate:{scope}:{hashlib.sha256(identity.encode()).hexdigest()}"
    try:
        # Redis shares eval typing with its synchronous client; this client is always async.
        result = await cast(Awaitable[list[int]], redis.eval(COUNTER, 1, key, str(duration * 1000)))
        count, ttl = result
    except RedisError as exc:
        raise AppError(503, "rate_limit_unavailable", "Request limiting is temporarily unavailable.") from exc
    if count > int(maximum):
        raise AppError(
            429,
            "rate_limited",
            "Too many requests. Try again later.",
            {"Retry-After": str(max(1, math.ceil(ttl / 1000)))},
        )
