"""Pydantic v2 schemas for Settings (LLM Providers, Agents, Quality Gates)."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


# --- LLM Providers ---
class ProviderCreate(BaseModel):
    provider_name: str
    display_name: Optional[str] = None
    base_url: Optional[str] = None


class ProviderResponse(BaseModel):
    id: str
    provider_name: str
    display_name: Optional[str]
    env_key_name: Optional[str]
    base_url: Optional[str]
    is_active: bool
    is_connected: bool
    model_count: int = 0
    created_at: datetime

    model_config = {"from_attributes": True}


class ModelResponse(BaseModel):
    id: str
    provider_id: str
    model_id: str
    model_name: Optional[str]
    context_window: Optional[int]
    is_available: bool

    model_config = {"from_attributes": True}


# --- Agent Configs ---
class AgentConfigUpdate(BaseModel):
    provider_id: Optional[str] = None
    model_id: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    system_prompt_override: Optional[str] = None


class AgentConfigResponse(BaseModel):
    id: str
    agent_name: str
    agent_role: str
    provider_id: Optional[str]
    model_id: Optional[str]
    temperature: float
    max_tokens: int
    system_prompt_override: Optional[str]
    is_active: bool
    provider_name: Optional[str] = None
    model_name: Optional[str] = None

    model_config = {"from_attributes": True}


# --- Quality Gates ---
class QualityGateUpdate(BaseModel):
    min_severity: Optional[str] = None
    max_issues_per_run: Optional[int] = None
    auto_fix_enabled: Optional[bool] = None
    file_exclusions: Optional[List[str]] = None
    rule_exclusions: Optional[List[str]] = None


class QualityGateResponse(BaseModel):
    id: str
    repo_id: str
    min_severity: str
    max_issues_per_run: int
    auto_fix_enabled: bool
    file_exclusions: str
    rule_exclusions: str

    model_config = {"from_attributes": True}
