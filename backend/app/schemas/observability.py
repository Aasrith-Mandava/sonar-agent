"""Pydantic schemas for Observability and Reports."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class AgentLogResponse(BaseModel):
    id: str
    agent_name: str
    scan_run_id: Optional[str]
    action: str
    input_summary: Optional[str]
    output_summary: Optional[str]
    tokens_in: Optional[int]
    tokens_out: Optional[int]
    latency_ms: Optional[int]
    cost_estimate: Optional[float]
    model_used: Optional[str]
    provider_used: Optional[str]
    status: str
    error_message: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class AgentLogListResponse(BaseModel):
    items: List[AgentLogResponse]
    total: int


class PipelineRunResponse(BaseModel):
    id: str
    scan_run_id: str
    stage: str
    status: str
    details: Optional[str]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    error_message: Optional[str]

    model_config = {"from_attributes": True}


class TokenUsageResponse(BaseModel):
    group: str
    tokens_in: int
    tokens_out: int
    total_tokens: int
    cost_estimate: float
    call_count: int


class CostSummaryResponse(BaseModel):
    total_cost: float
    total_tokens: int
    by_provider: List[TokenUsageResponse]
    by_agent: List[TokenUsageResponse]


class DeltaReportResponse(BaseModel):
    id: str
    repo_id: str
    before_scan_id: str
    after_scan_id: str
    total_before: Optional[int]
    total_after: Optional[int]
    fixed_count: Optional[int]
    new_count: Optional[int]
    improvement_pct: Optional[float]
    severity_breakdown: Optional[str]
    rule_breakdown: Optional[str]
    file_breakdown: Optional[str]
    summary_narrative: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}
