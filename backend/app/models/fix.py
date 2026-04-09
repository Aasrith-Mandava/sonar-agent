from typing import Optional
"""SQLAlchemy ORM model for Proposed Fixes."""

import uuid
from datetime import datetime

from sqlalchemy import Float, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Fix(Base):
    __tablename__ = "fixes"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    issue_id: Mapped[str] = mapped_column(String, ForeignKey("issues.id"), nullable=False)
    scan_run_id: Mapped[str] = mapped_column(String, ForeignKey("scan_runs.id"), nullable=False)
    agent_run_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    file_path: Mapped[str] = mapped_column(String, nullable=False)
    original_code: Mapped[str] = mapped_column(Text, nullable=False)
    fixed_code: Mapped[str] = mapped_column(Text, nullable=False)
    diff_patch: Mapped[str] = mapped_column(Text, nullable=False)
    explanation: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    confidence_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    reviewer_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String, default="pending")
    # pending|approved|rejected|edited|applied
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    issue: Mapped["Issue"] = relationship("Issue", back_populates="fix")
    scan_run: Mapped["ScanRun"] = relationship("ScanRun", back_populates="fixes")
    reviews: Mapped[list["FixReview"]] = relationship("FixReview", back_populates="fix", cascade="all, delete-orphan")
