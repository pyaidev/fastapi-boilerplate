from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class AppError(Exception):
    def __init__(self, status: int, code: str, message: str, headers: dict[str, str] | None = None):
        self.status, self.code, self.message = status, code, message
        self.headers = headers or {}


def error_response(
    request: Request,
    status: int,
    code: str,
    message: str,
    details: Any = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        {
            "error": {
                "code": code,
                "message": message,
                "details": details or [],
                "request_id": getattr(request.state, "request_id", "-"),
            }
        },
        status_code=status,
        headers=headers,
    )


async def app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    return error_response(request, exc.status, exc.code, exc.message, headers=exc.headers)


async def validation_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    # Never echo submitted input: it may include passwords or tokens.
    details = [
        {"field": ".".join(map(str, error["loc"])), "message": error["msg"], "type": error["type"]}
        for error in exc.errors()
    ]
    return error_response(request, 422, "validation_error", "Request validation failed.", details)


async def http_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, (HTTPException, StarletteHTTPException))
    return error_response(request, exc.status_code, "http_error", str(exc.detail), headers=exc.headers)
