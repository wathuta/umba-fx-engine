"""Request correlation, structured logs, and Prometheus metrics."""

import json
import logging
from collections.abc import Callable
from uuid import uuid4

from fastapi import Request, Response
from prometheus_client import Counter, Histogram, generate_latest
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.errors import problem_response, unsupported_media_type

logger = logging.getLogger("fx")
logging.basicConfig(level=logging.INFO, format="%(message)s")

# Request IDs are propagated through responses, errors, and structured logs.
HEADER_REQUEST_ID = "X-Request-ID"

quote_created_total = Counter("fx_quote_created_total", "Quotes created")
quote_expired_total = Counter("fx_quote_expired_total", "Expired quote execution attempts")
execution_success_total = Counter("fx_execution_success_total", "Successful executions")
execution_failure_total = Counter("fx_execution_failure_total", "Failed executions")
idempotency_replay_total = Counter("fx_idempotency_replay_total", "Idempotency replays")
idempotency_conflict_total = Counter("fx_idempotency_conflict_total", "Idempotency conflicts")
insufficient_funds_total = Counter("fx_insufficient_funds_total", "Insufficient funds failures")
rate_refresh_success_total = Counter("fx_rate_refresh_success_total", "Successful rate refreshes")
rate_refresh_failure_total = Counter("fx_rate_refresh_failure_total", "Failed rate refreshes")
stale_rates_total = Counter("fx_stale_rates_total", "Stale rate failures")

quote_latency_ms = Histogram("fx_quote_latency_ms", "Quote latency in ms")
execution_latency_ms = Histogram("fx_execution_latency_ms", "Execution latency in ms")
rate_refresh_latency_ms = Histogram("fx_rate_refresh_latency_ms", "Rate refresh latency in ms")


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


def log_event(event: str, level: int = logging.INFO, **fields: object) -> None:
    """Emit JSON logs so quote and execution events can be correlated."""
    payload = {"event": event, **fields}
    logger.log(level, json.dumps(payload, default=str, sort_keys=True))


def metrics_response() -> Response:
    return Response(generate_latest(), media_type="text/plain; version=0.0.4")
