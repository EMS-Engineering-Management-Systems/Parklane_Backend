"""Error types and the handlers that render them as the API envelope.

Every failure leaves the API in the same shape:

    {"success": false, "error": {"status": 404, "message": "...", "details": {...}}}
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pymongo.errors import PyMongoError
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("parklane")


class ApiError(Exception):
    """An error with a deliberate HTTP status and a client-readable message."""

    def __init__(self, status_code: int, message: str, details: Any | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.details = details

    @classmethod
    def bad_request(cls, message: str, details: Any | None = None) -> ApiError:
        return cls(status.HTTP_400_BAD_REQUEST, message, details)

    @classmethod
    def not_found(cls, message: str, details: Any | None = None) -> ApiError:
        return cls(status.HTTP_404_NOT_FOUND, message, details)

    @classmethod
    def conflict(cls, message: str, details: Any | None = None) -> ApiError:
        return cls(status.HTTP_409_CONFLICT, message, details)


def _envelope(status_code: int, message: str, details: Any | None = None) -> JSONResponse:
    error: dict[str, Any] = {"status": status_code, "message": message}
    if details is not None:
        # Details can carry datetimes and ObjectIds - a duplicate-key error
        # echoes the clashing document key - and JSONResponse cannot encode
        # those itself.
        error["details"] = jsonable_encoder(details)
    return JSONResponse(status_code=status_code, content={"success": False, "error": error})


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return _envelope(exc.status_code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI defaults to 422; a malformed reading is a bad request as far as
        # the BMS clients are concerned, so keep the contract at 400.
        details = [
            {
                "field": ".".join(str(part) for part in err.get("loc", ()) if part != "body"),
                "message": err.get("msg"),
                "type": err.get("type"),
            }
            for err in exc.errors()
        ]
        return _envelope(status.HTTP_400_BAD_REQUEST, "request body or parameters failed validation", details)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        message = exc.detail if isinstance(exc.detail, str) else "request failed"
        details = None
        if exc.status_code == status.HTTP_404_NOT_FOUND and request.scope.get("route") is None:
            message = f"No route for {request.method} {request.url.path}"
            details = {"hint": "GET /api for the route index"}
        return _envelope(exc.status_code, message, details)

    @app.exception_handler(PyMongoError)
    async def _mongo_error(request: Request, exc: PyMongoError) -> JSONResponse:
        logger.exception("mongodb error on %s %s", request.method, request.url.path)
        return _envelope(status.HTTP_503_SERVICE_UNAVAILABLE, f"database error: {exc}")

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error on %s %s", request.method, request.url.path)
        return _envelope(status.HTTP_500_INTERNAL_SERVER_ERROR, "internal server error")
