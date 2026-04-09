"""SQLAlchemy ORM model for Quality Gate configuration per repo."""

import uuid
from datetime import datetime

from sqlalchemy import Float, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class QualityGate(Base):
    __tablename__ = "quality_gates"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    repo_id: Mapped[str] = mapped_column(String, ForeignKey("repos.id"), nullable=False, unique=True)
    min_severity: Mapped[str] = mapped_column(String, default="MAJOR")
    max_issues_per_run: Mapped[int] = mapped_column(Integer, default=20)
    auto_fix_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    file_exclusions: Mapped[str] = mapped_column(Text, default="[]")  # JSON array
    rule_exclusions: Mapped[str] = mapped_column(Text, default="[]")  # JSON array
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    repo: Mapped["Repo"] = relationship("Repo", back_populates="quality_gate")
