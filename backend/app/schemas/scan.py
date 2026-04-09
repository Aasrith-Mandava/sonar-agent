"""Pydantic v2 schemas for Scans and Issues."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class ScanRunResponse(BaseModel):
    id: str
    repo_id: str
    status: str
    scan_type: str
    parent_scan_id: Optional[str]
    total_issues: int
    issues_by_severity: str
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    created_at: datetime

    model_config = {"from_attributes": True}


class IssueResponse(BaseModel):
    id: str
    scan_run_id: str
    sonar_key: str
    severity: str
    type: str
    rule_key: str
    rule_name: Optional[str]
    component: str
    line: Optional[int]
    message: Optional[str]
    effort: Optional[str]
    status: str
    selected_for_fix: bool

    model_config = {"from_attributes": True}


class IssueListResponse(BaseModel):
    items: List[IssueResponse]
    total: int
    page: int
    page_size: int
