"""Request middleware: correlation ids, access logs, metrics and security headers."""

from __future__ import annotations

import time
import uuid

from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from common.logging import get_logger, request_id_var
from common.metrics import api_latency, api_requests

logger = get_logger(__name__)


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        token = request_id_var.set(request_id)
        request.state.request_id = request_id
        started = time.perf_counter()
        # Route template (not the concrete path) keeps metric cardinality bounded.
        path = request.scope.get("route").path if request.scope.get("route") else request.url.path

        try:
            response = await call_next(request)
            status = response.status_code
            return response
        except Exception:
            status = 500
            raise
        finally:
            duration = time.perf_counter() - started
            path = (
                request.scope["route"].path
                if request.scope.get("route") is not None
                else path
            )
            api_latency.labels(method=request.method, path=path).observe(duration)
            api_requests.labels(method=request.method, path=path, status=str(status)).inc()
            logger.info(
                "api.request",
                method=request.method,
                path=request.url.path,
                status=status,
                duration_ms=round(duration * 1000, 2),
            )
            request_id_var.reset(token)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=63072000; includeSubDomains"
        )
        request_id = getattr(request.state, "request_id", None)
        if request_id:
            response.headers.setdefault("X-Request-Id", request_id)
        return response


def register_middleware(app: FastAPI) -> None:
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestContextMiddleware)
