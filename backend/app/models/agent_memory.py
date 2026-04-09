from typing import Optional
"""SQLAlchemy ORM model for Agentic Memory — persistent agent context across runs."""

import uuid
from datetime import datetime

from sqlalchemy import Float, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AgentMemory(Base):
    """
    Stores agent observations, decisions and learned patterns per entity.
    entity_key examples:
      - "repo:{repo_id}:rule:{rule_key}"   — fixer memory per rule
      - "repo:{repo_id}:file:{file_path}"  — fixer memory per file
      - "rule:{rule_key}"                  — global rule knowledge
      - "repo:{repo_id}"                   — global repo memory
    """
    __tablename__ = "agent_memory"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    agent_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    entity_key: Mapped[str] = mapped_column(String, nullable=False, index=True)
    memory_type: Mapped[str] = mapped_column(String, nullable=False)
    # pattern|observation|decision|fix_template|error|summary
    content: Mapped[str] = mapped_column(Text, nullable=False)
    scan_run_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("scan_runs.id"), nullable=True)
    recall_count: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
