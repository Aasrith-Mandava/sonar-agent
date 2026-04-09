"""Pydantic v2 schemas for Repositories."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, HttpUrl


class RepoCreate(BaseModel):
    name: str
    github_url: str
    pat: Optional[str] = None
    branch: str = "main"


class RepoUpdate(BaseModel):
    name: Optional[str] = None
    branch: Optional[str] = None
    pat: Optional[str] = None


class RepoResponse(BaseModel):
    id: str
    name: str
    github_url: str
    branch: str
    clone_path: Optional[str]
    sonar_project_key: Optional[str]
    last_scan_at: Optional[datetime]
    created_at: datetime

    model_config = {"from_attributes": True}
