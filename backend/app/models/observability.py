from typing import Optional
"""SQLAlchemy ORM models for Observability: AgentLog, PipelineRun, DeltaReport."""

import uuid
from datetime import datetime

from sqlalchemy import Float, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class AgentLog(Base):
    __tablename__ = "agent_logs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    agent_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    scan_run_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("scan_runs.id"), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String, nullable=False)
    input_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    output_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tokens_in: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cost_estimate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    model_used: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    provider_used: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="success")  # success|error
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    scan_run_id: Mapped[str] = mapped_column(String, ForeignKey("scan_runs.id"), nullable=False, index=True)
    stage: Mapped[str] = mapped_column(String, nullable=False)
    # scan|analyze|fix|review|apply|rescan|report
    status: Mapped[str] = mapped_column(String, default="pending")
    # pending|running|completed|failed|skipped
    details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    scan_run: Mapped["ScanRun"] = relationship("ScanRun", back_populates="pipeline_runs")


class DeltaReport(Base):
    __tablename__ = "delta_reports"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    repo_id: Mapped[str] = mapped_column(String, ForeignKey("repos.id"), nullable=False, index=True)
    before_scan_id: Mapped[str] = mapped_column(String, ForeignKey("scan_runs.id"), nullable=False)
    after_scan_id: Mapped[str] = mapped_column(String, ForeignKey("scan_runs.id"), nullable=False)
    total_before: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_after: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    fixed_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    new_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    improvement_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    severity_breakdown: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON
    rule_breakdown: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON
    file_breakdown: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON
    summary_narrative: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
