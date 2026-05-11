from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.orm import Session

from app.api.schemas.executions import ExecutionRequest, ExecutionResponse
from app.configs.constants import EXECUTIONS_PATH, HTTP_POST
from app.db.session import get_db
from app.services.executions import execute_quote, request_hash

router = APIRouter()


@router.post("/executions", response_model=ExecutionResponse)
def execute_quote_endpoint(
    payload: ExecutionRequest,
    request: Request,
    session: Session = Depends(get_db),
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
) -> dict:
    """Execute stored quote terms atomically under the required idempotency key."""
    body = {"quote_id": str(payload.quote_id)}
    result, _ = execute_quote(
        session,
        payload.quote_id,
        idempotency_key,
        request_hash(HTTP_POST, EXECUTIONS_PATH, body),
        request_id=request.state.request_id,
    )
    return result
