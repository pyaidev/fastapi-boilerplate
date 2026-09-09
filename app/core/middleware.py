import logging
import time
import uuid

from prometheus_client import Counter, Gauge, Histogram
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import AppError, error_response
from app.core.logging import request_id_var
from app.core.rate_limit import client_ip, enforce_limit

REQUESTS = Counter("fastapi_http_requests", "HTTP responses", ["method", "route", "status"])
LATENCY = Histogram(
    "fastapi_http_duration_seconds",
    "Request latency",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)
IN_FLIGHT = Gauge("fastapi_http_in_flight", "In-flight requests", multiprocess_mode="livesum")
logger = logging.getLogger("http")


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        settings = request.app.state.settings
        request_id = uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        context = request_id_var.set(request_id)
        started, status = time.monotonic(), 500
        IN_FLIGHT.inc()

        async def send_with_headers(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = list(message.get("headers", []))
                headers.extend(
                    [
                        (b"x-request-id", request_id.encode()),
                        (b"x-content-type-options", b"nosniff"),
                        (b"x-frame-options", b"DENY"),
                        (b"referrer-policy", b"no-referrer"),
                    ]
                )
                if scope["path"].startswith(("/api/v1/auth", "/reset-password")):
                    headers.append((b"cache-control", b"no-store"))
                if settings.app_env == "production":
                    headers.append((b"strict-transport-security", b"max-age=3600"))
                message["headers"] = headers
            await send(message)

        try:
            if scope["path"].startswith("/api/") and scope["method"] != "OPTIONS":
                await enforce_limit(
                    request.app.state.redis, "ip", client_ip(request, settings), settings.rate_ip
                )
            content_length = request.headers.get("content-length")
            if content_length:
                try:
                    size = int(content_length)
                except ValueError as exc:
                    raise AppError(400, "invalid_length", "Invalid Content-Length.") from exc
                if size < 0 or size > settings.max_request_bytes:
                    raise AppError(413, "body_too_large", "Request body is too large.")
            # Buffer only a bounded JSON/form request; streaming uploads belong in object storage.
            chunks: list[Message] = []
            size = 0
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                size += len(message.get("body", b""))
                if size > settings.max_request_bytes:
                    raise AppError(413, "body_too_large", "Request body is too large.")
                chunks.append(message)
                if not message.get("more_body", False):
                    break

            async def replay() -> Message:
                return chunks.pop(0) if chunks else await receive()

            await self.app(scope, replay, send_with_headers)
        except AppError as exc:
            response = error_response(request, exc.status, exc.code, exc.message, headers=exc.headers)
            await response(scope, receive, send_with_headers)
        finally:
            IN_FLIGHT.dec()
            route = getattr(scope.get("route"), "path", "unmatched")
            method = (
                scope["method"]
                if scope["method"] in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
                else "OTHER"
            )
            elapsed = time.monotonic() - started
            REQUESTS.labels(method, route, str(status)).inc()
            LATENCY.labels(method, route).observe(elapsed)
            logger.info(
                "request",
                extra={
                    "method": method,
                    "route": route,
                    "status": status,
                    "duration_ms": round(elapsed * 1000, 2),
                },
            )
            request_id_var.reset(context)
