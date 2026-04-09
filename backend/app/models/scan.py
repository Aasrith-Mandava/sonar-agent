from typing import Optional
"""SQLAlchemy ORM models for Scan Runs and Issues."""

import uuid
from datetime import datetime

from sqlalchemy import Float, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ScanRun(Base):
    __tablename__ = "scan_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    repo_id: Mapped[str] = mapped_column(String, ForeignKey("repos.id"), nullable=False)
    triggered_by: Mapped[Optional[str]] = mapped_column(String, ForeignKey("users.id"), nullable=True)
    status: Mapped[str] = mapped_column(String, default="pending")
    # pending|scanning|analyzing|fixing|reviewing|rescanning|completed|failed
    scan_type: Mapped[str] = mapped_column(String, default="initial")  # initial|rescan
    parent_scan_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("scan_runs.id"), nullable=True)
    sonar_task_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    total_issues: Mapped[int] = mapped_column(Integer, default=0)
    issues_by_severity: Mapped[str] = mapped_column(Text, default="{}")  # JSON
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    repo: Mapped["Repo"] = relationship("Repo", back_populates="scan_runs")
    issues: Mapped[list["Issue"]] = relationship("Issue", back_populates="scan_run", cascade="all, delete-orphan")
    fixes: Mapped[list["Fix"]] = relationship("Fix", back_populates="scan_run", cascade="all, delete-orphan")
    pipeline_runs: Mapped[list["PipelineRun"]] = relationship("PipelineRun", back_populates="scan_run", cascade="all, delete-orphan")


class Issue(Base):
    __tablename__ = "issues"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    scan_run_id: Mapped[str] = mapped_column(String, ForeignKey("scan_runs.id"), nullable=False)
    sonar_key: Mapped[str] = mapped_column(String, nullable=False)
    severity: Mapped[str] = mapped_column(String, nullable=False)  # BLOCKER|CRITICAL|MAJOR|MINOR|INFO
    type: Mapped[str] = mapped_column(String, nullable=False)       # BUG|VULNERABILITY|CODE_SMELL
    rule_key: Mapped[str] = mapped_column(String, nullable=False)
    rule_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    component: Mapped[str] = mapped_column(String, nullable=False)  # file path
    line: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    effort: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="OPEN")
    selected_for_fix: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    scan_run: Mapped["ScanRun"] = relationship("ScanRun", back_populates="issues")
    fix: Mapped[Optional["Fix"]] = relationship("Fix", back_populates="issue", uselist=False)
