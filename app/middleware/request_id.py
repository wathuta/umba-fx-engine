from collections.abc import Callable
from uuid import uuid4

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.utils.errors import problem_response, unsupported_media_type

# Request IDs are propagated through responses, errors, and structured logs.
HEADER_REQUEST_ID = "X-Request-ID"


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Attach one request ID to responses, errors, and structured log events."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        request_id = request.headers.get(HEADER_REQUEST_ID) or str(uuid4())
        request.state.request_id = request_id
        if _has_unsupported_json_content(request):
            return problem_response(unsupported_media_type("Request body must be application/json."), request_id)
        response = await call_next(request)
        response.headers[HEADER_REQUEST_ID] = request_id
        return response


def _has_unsupported_json_content(request: Request) -> bool:
    if request.method not in {"POST", "PUT", "PATCH"}:
        return False
    if request.headers.get("content-length") in {None, "0"}:
        return False
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    return content_type != "application/json"
