from uuid import UUID

from pydantic import BaseModel


class RateRefreshResponse(BaseModel):
    rate_refresh_id: UUID
    status: str
    fetched_at: str
    pairs_updated: int
