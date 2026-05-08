"""Structured API errors.

The API uses problem+json so clients can make decisions from stable machine
codes instead of parsing human-readable messages.
"""

from dataclasses import dataclass
from typing import Any

from fastapi import Request
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse


@dataclass(slots=True)
class ApiError(Exception):
    status_code: int
    code: str
    title: str
    detail: str
    retryable: bool = False


def problem_response(error: ApiError, request_id: str | None = None) -> JSONResponse:
    """Map an application error to the response shape declared in SPEC.md."""
    payload: dict[str, Any] = {
        "type": f"https://api.example.com/problems/{error.code.replace('_', '-')}",
        "title": error.title,
        "status": error.status_code,
        "detail": error.detail,
        "instance": f"urn:request:{request_id}" if request_id else None,
        "code": error.code,
        "retryable": error.retryable,
    }
    return JSONResponse(status_code=error.status_code, content=payload, media_type="application/problem+json")


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return problem_response(exc, getattr(request.state, "request_id", None))


async def http_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
    title = exc.detail if isinstance(exc.detail, str) else "HTTP error"
    return problem_response(
        ApiError(exc.status_code, "http_error", title, str(exc.detail)),
        getattr(request.state, "request_id", None),
    )


def bad_request(detail: str) -> ApiError:
    return ApiError(400, "bad_request", "Bad request", detail)


def validation_error(detail: str) -> ApiError:
    return ApiError(422, "validation_error", "Validation error", detail)


def unsupported_media_type(detail: str) -> ApiError:
    return ApiError(415, "unsupported_content_type", "Unsupported content type", detail)


def not_found(code: str, detail: str) -> ApiError:
    return ApiError(404, code, "Not found", detail)


def conflict(code: str, detail: str) -> ApiError:
    return ApiError(409, code, "Conflict", detail)


def service_unavailable(code: str, detail: str, retryable: bool = True) -> ApiError:
    return ApiError(503, code, "Service unavailable", detail, retryable=retryable)


def bad_gateway(code: str, detail: str, retryable: bool = True) -> ApiError:
    return ApiError(502, code, "Bad upstream response", detail, retryable=retryable)


def gateway_timeout(detail: str) -> ApiError:
    return ApiError(504, "upstream_timeout", "Upstream timeout", detail, retryable=True)
