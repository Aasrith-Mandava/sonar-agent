"""Pydantic v2 schemas for Fixes and Reviews."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class IssueSummary(BaseModel):
    id: str
    severity: str
    type: str
    rule_key: str
    component: str
    line: Optional[int]
    message: Optional[str]

    model_config = {"from_attributes": True}


class FixResponse(BaseModel):
    id: str
    issue_id: str
    scan_run_id: str
    file_path: str
    original_code: str
    fixed_code: str
    diff_patch: str
    explanation: Optional[str]
    confidence_score: Optional[int]
    reviewer_summary: Optional[str]
    status: str
    created_at: datetime
    updated_at: datetime
    issue: Optional[IssueSummary] = None

    model_config = {"from_attributes": True}


class FixListResponse(BaseModel):
    items: List[FixResponse]
    total: int


class ApplyFixesResult(BaseModel):
    applied: int
    branch: Optional[str]
    pr_url: Optional[str]
    pushed: bool
    pr_existed: bool = False
    pat_source: Optional[str] = None
    message: str


class ReviewRequest(BaseModel):
    action: str  # approved|rejected|edited
    comment: Optional[str] = None
    edited_code: Optional[str] = None


class BulkApproveRequest(BaseModel):
    min_confidence: int = 80


class ApplyFixesRequest(BaseModel):
    push_to_github: bool = False
    create_pr: bool = False


class ReviewStatsResponse(BaseModel):
    total: int
    pending: int
    approved: int
    rejected: int
    edited: int
    applied: int
