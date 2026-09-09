import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from redis.asyncio import Redis
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.core.config import Settings, get_settings
from app.core.errors import (
    AppError,
    app_error_handler,
    error_response,
    http_error_handler,
    validation_handler,
)
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.core.security import hash_password
from app.infrastructure.database import Database
from app.modules.auth.router import router as auth_router
from app.modules.notes.router import router as notes_router
from app.modules.users.router import router as users_router

logger = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or get_settings()
    configure_logging()
    if config.sentry_dsn and config.app_env == "production":
        import sentry_sdk

        sentry_sdk.init(
            dsn=config.sentry_dsn,
            environment=config.app_env,
            send_default_pii=False,
            traces_sample_rate=0.0,
            max_request_body_size="never",
            include_local_variables=False,
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        database = Database(config)
        redis = Redis.from_url(
            config.redis_url.get_secret_value(), socket_timeout=2, socket_connect_timeout=2
        )
        app.state.settings, app.state.database, app.state.redis = config, database, redis
        try:
            await database.ping()
            await redis.ping()
            app.state.dummy_hash = await hash_password("dummy-login-password-not-an-account")
            yield
        finally:
            await redis.aclose()
            await database.close()

    app = FastAPI(
        title="FastAPI Boilerplate",
        version="1.0.0",
        lifespan=lifespan,
        debug=False,
        docs_url="/docs" if config.docs_enabled else None,
        redoc_url="/redoc" if config.docs_enabled else None,
        openapi_url="/openapi.json" if config.docs_enabled else None,
        middleware=[
            Middleware(
                CORSMiddleware,
                allow_origins=config.cors_origins,
                allow_credentials=False,
                allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
                allow_headers=["Authorization", "Content-Type"],
                expose_headers=["X-Request-ID", "Retry-After"],
            ),
            Middleware(RequestContextMiddleware),
            Middleware(TrustedHostMiddleware, allowed_hosts=config.allowed_hosts),
        ],
    )
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_handler)
    app.add_exception_handler(HTTPException, http_error_handler)

    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.error("Unhandled request error", extra={"exception_type": type(exc).__name__})
        return error_response(
            request,
            500,
            "server_error",
            "An unexpected error occurred.",
            headers={"X-Request-ID": getattr(request.state, "request_id", "-")},
        )

    app.add_exception_handler(Exception, unexpected_error)
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(notes_router, prefix="/api/v1")
    app.include_router(users_router, prefix="/api/v1")

    @app.get("/health/live", tags=["Health"])
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", tags=["Health"])
    async def ready(request: Request) -> JSONResponse:
        try:
            await request.app.state.database.ping()
            await request.app.state.redis.ping()
        except Exception:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ok"})

    @app.get("/reset-password", include_in_schema=False)
    async def reset_page() -> FileResponse:
        return FileResponse(STATIC / "reset.html", headers={"Cache-Control": "no-store"})

    @app.get("/static/reset.js", include_in_schema=False)
    async def reset_script() -> FileResponse:
        return FileResponse(
            STATIC / "reset.js",
            media_type="text/javascript",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    return app
