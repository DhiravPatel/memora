"""Translating domain errors into HTTP responses."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from common.errors import AppError
from common.logging import get_logger

logger = get_logger(__name__)


def _serialisable(errors: list[dict]) -> list[dict]:
    """Pydantic puts the raised exception itself in ``ctx``; a custom validator's
    ``ValueError`` would otherwise turn a 422 into a 500 while being serialised."""
    cleaned = []
    for error in errors:
        item = dict(error)
        if isinstance(item.get("ctx"), dict):
            item["ctx"] = {
                key: value if isinstance(value, (str, int, float, bool, type(None))) else str(value)
                for key, value in item["ctx"].items()
            }
        cleaned.append(jsonable_encoder(item))
    return cleaned


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(_request: Request, exc: AppError) -> JSONResponse:
        if exc.status_code >= 500:
            logger.error("api.error", code=exc.code, message=exc.message, details=exc.details)
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        errors = _serialisable(exc.errors())
        first = errors[0] if errors else {}
        where = ".".join(str(part) for part in first.get("loc", ()) if part != "body")
        message = "Request validation failed."
        if first.get("msg"):
            message = f"Request validation failed: {where + ': ' if where else ''}{first['msg']}"
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": message,
                    "details": {"errors": errors},
                }
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": "http_error", "message": str(exc.detail)}},
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("api.unhandled_error", error=str(exc))
        return JSONResponse(
            status_code=500,
            content={
                "error": {"code": "internal_error", "message": "An unexpected error occurred."}
            },
        )
