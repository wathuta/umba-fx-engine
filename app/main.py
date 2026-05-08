from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.api.routes import router
from app.core.errors import (
    ApiError,
    api_error_handler,
    bad_request,
    http_error_handler,
    problem_response,
    unhandled_error_handler,
    validation_error,
)
from app.core.observability import RequestIdMiddleware
from app.db.session import create_all


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        create_all()
        yield

    app = FastAPI(title="FX Engine", lifespan=lifespan)
    app.add_middleware(RequestIdMiddleware)
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(HTTPException, http_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        error = bad_request("Malformed JSON body.") if _is_json_decode_error(exc) else validation_error(str(exc))
        return problem_response(error, getattr(request.state, "request_id", None))

    app.include_router(router)
    return app


app = create_app()


def _is_json_decode_error(exc: RequestValidationError) -> bool:
    return any("json" in str(error.get("type", "")).lower() for error in exc.errors())
