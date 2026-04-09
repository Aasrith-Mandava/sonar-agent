"""Pydantic v2 schemas for Reviews (forgot to place in schemas dir earlier)."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel

class FixReviewResponse(BaseModel):
    id: str
    fix_id: str
    user_id: str
    action: str
    comment: Optional[str]
    edited_code: Optional[str]
    reviewed_at: datetime

    model_config = {"from_attributes": True}
